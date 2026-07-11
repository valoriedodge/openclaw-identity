.PHONY: all setup certs dirs permissions start stop restart status onboard agent register-workloads \
        validate-labels add-permission remove-permission list-agents list-entries fetch-svid clean help

SPIRE_SERVER   := docker compose exec spire-server /opt/spire/bin/spire-server
TRUST_DOMAIN   := example.org
AGENT_CERT     := spire-agent-certs/agent.crt.pem
AGENT_KEY      := spire-agent-certs/agent.key.pem

# Service list is read from .services if it exists (maintained by `make agent N=X`).
# Fall back to defaults if the file is missing.
# Override any time on the command line: make register-workloads SERVICES="openclaw-gateway my-agent"
_SERVICES_FILE := .services
SERVICES       ?= $(if $(wildcard $(_SERVICES_FILE)),$(shell cat $(_SERVICES_FILE)),openclaw-gateway openclaw-gateway-2 openclaw-cli)

# ── Top-level targets ────────────────────────────────────────────────────────

all: help

## Full first-time setup: dirs → certs → permissions → onboard → start → register
setup: dirs certs permissions onboard start wait-healthy register-workloads
	@echo ""
	@echo "Setup complete. Run 'make fetch-svid SERVICE=openclaw-gateway' to verify."

# ── Prerequisites ────────────────────────────────────────────────────────────

dirs:
	@echo "→ Creating data directories..."
	mkdir -p spire-server-data spire-agent-certs audit-logs policy
	mkdir -p ~/.openclaw_docker/workspace
	mkdir -p ~/.openclaw_docker_2/workspace
	mkdir -p ~/.openclaw_docker_cli/workspace

certs: dirs
	@echo "→ Generating agent CA and certificate..."
	@if [ -f $(AGENT_CERT) ]; then \
		echo "  Certs already exist — skipping. Run 'make clean-certs' to regenerate."; \
	else \
		openssl genrsa -out spire-agent-certs/agent-ca.key 2048; \
		openssl req -x509 -new -nodes \
			-key spire-agent-certs/agent-ca.key \
			-sha256 -days 365 \
			-subj "/CN=agent-ca" \
			-out spire-agent-certs/agent-ca.crt; \
		openssl genrsa -out $(AGENT_KEY) 2048; \
		openssl req -new \
			-key $(AGENT_KEY) \
			-subj "/CN=spire-agent" \
			-out spire-agent-certs/agent.csr; \
		printf '[v3_req]\nkeyUsage = critical, digitalSignature\n' \
			> spire-agent-certs/agent-ext.cnf; \
		openssl x509 -req \
			-in spire-agent-certs/agent.csr \
			-CA spire-agent-certs/agent-ca.crt \
			-CAkey spire-agent-certs/agent-ca.key \
			-CAcreateserial -days 365 -sha256 \
			-extfile spire-agent-certs/agent-ext.cnf \
			-extensions v3_req \
			-out $(AGENT_CERT); \
		echo "  Certs written to spire-agent-certs/"; \
	fi

permissions: dirs
	@echo "→ Setting directory ownership (SPIRE containers run as UID 1000)..."
	sudo chown -R 1000:1000 spire-server-data
	chmod 755 spire-server-data

# ── Lifecycle ────────────────────────────────────────────────────────────────

# Usage: make agent N=3
agent:
	@test -n "$(N)" || (echo "Usage: make agent N=<number>" && exit 1)
	@echo "→ Adding openclaw-gateway-$(N)..."
	python3 add-gateway.py $(N)
	@echo "→ Onboarding openclaw-gateway-$(N)..."
	docker compose run -it openclaw-gateway-$(N) bash -c "openclaw onboard"
	@echo "→ Registering SPIRE workload entry for openclaw-gateway-$(N)..."
	$(SPIRE_SERVER) entry create \
		-parentID $$(make -s agent-spiffe-id) \
		-spiffeID spiffe://$(TRUST_DOMAIN)/ns/apps/sa/openclaw-gateway-$(N) \
		-selector docker:label:app:openclaw-gateway-$(N)
	@echo ""
	@echo "Done. Start the gateway with: docker compose up -d openclaw-gateway-$(N)"

onboard:
	@echo "→ Onboarding gateways (you will be prompted for each one)..."
	@for svc in $(SERVICES); do \
		echo ""; \
		echo "  ── Onboarding $$svc ──────────────────────────────"; \
		docker compose run -it $$svc bash -c "openclaw onboard" || \
			(echo "[warn] Onboarding skipped or failed for $$svc" && true); \
	done
	@echo ""
	@echo "→ Onboarding complete."

start:
	@echo "→ Starting containers..."
	docker compose up -d

stop:
	@echo "→ Stopping containers..."
	docker compose down

restart:
	docker compose restart

status:
	docker compose ps

wait-healthy:
	@echo "→ Waiting for SPIRE server to be ready..."
	@for i in $$(seq 1 20); do \
		docker compose exec spire-server \
			/opt/spire/bin/spire-server healthcheck > /dev/null 2>&1 && break; \
		echo "  ...waiting ($$i/20)"; \
		sleep 3; \
	done
	@echo "  SPIRE server is healthy."

# ── Workload registration ────────────────────────────────────────────────────

agent-spiffe-id:
	@docker compose logs spire-agent 2>/dev/null \
		| grep "attestation was successful" \
		| tail -1 \
		| grep -oE 'spiffe://[^ "]+'

validate-labels:
	@echo "→ Validating service labels match SERVICES list..."
	@errors=0; \
	for svc in $(SERVICES); do \
		label=$$(docker inspect --format '{{index .Config.Labels "app"}}' \
			$$(docker compose ps -q $$svc 2>/dev/null) 2>/dev/null); \
		if [ "$$label" != "$$svc" ]; then \
			echo "  [FAIL] $$svc: expected label app=$$svc, got '$$label'"; \
			errors=$$((errors+1)); \
		else \
			echo "  [ok]   $$svc: label app=$$svc"; \
		fi; \
	done; \
	[ $$errors -eq 0 ] || (echo "Fix labels in docker-compose.yml" && exit 1)

register-workloads: _agent-id-check validate-labels
	@echo "→ Registering workload entries for: $(SERVICES)"
	@PARENT_ID=$$(make -s agent-spiffe-id); \
	for svc in $(SERVICES); do \
		echo "  registering $$svc ..."; \
		$(SPIRE_SERVER) entry create \
			-parentID $$PARENT_ID \
			-spiffeID spiffe://$(TRUST_DOMAIN)/ns/apps/sa/$$svc \
			-selector docker:label:app:$$svc \
			2>&1 | grep -v "^$$" || true; \
	done
	@echo "  Done."

list-agents:
	@echo "→ Tracked services (from .services):"
	@if [ -f $(_SERVICES_FILE) ]; then cat $(_SERVICES_FILE); else echo "  (none — .services not found)"; fi

list-entries:
	$(SPIRE_SERVER) entry show

delete-all-entries:
	@echo "→ Deleting all workload entries..."
	@for id in $$($(SPIRE_SERVER) entry show | grep "Entry ID" | awk '{print $$3}'); do \
		$(SPIRE_SERVER) entry delete -entryID $$id; \
	done

_agent-id-check:
	@make -s agent-spiffe-id | grep -q "spiffe://" || \
		(echo "ERROR: Could not determine agent SPIFFE ID. Is the agent running and attested?" && exit 1)

# ── SVID verification ────────────────────────────────────────────────────────

# Usage: make fetch-svid SERVICE=openclaw-gateway
fetch-svid:
	@test -n "$(SERVICE)" || (echo "Usage: make fetch-svid SERVICE=<container-name>" && exit 1)
	docker compose exec $(SERVICE) \
		/bin/spire-agent-tool api fetch x509 \
		-socketPath /opt/spire/sockets/agent.sock

# ── OPA policy management ────────────────────────────────────────────────────

# Usage: make add-permission IDENTITY=spiffe://example.org/ns/apps/sa/gateway-1 TOOL=read_file
add-permission:
	@test -n "$(IDENTITY)" || (echo "Usage: make add-permission IDENTITY=<spiffe-id> TOOL=<tool>" && exit 1)
	@test -n "$(TOOL)"     || (echo "Usage: make add-permission IDENTITY=<spiffe-id> TOOL=<tool>" && exit 1)
	python3 update-policy.py --identity $(IDENTITY) --tool $(TOOL) --action add

# Usage: make remove-permission IDENTITY=spiffe://example.org/ns/apps/sa/gateway-1 TOOL=read_file
remove-permission:
	@test -n "$(IDENTITY)" || (echo "Usage: make remove-permission IDENTITY=<spiffe-id> TOOL=<tool>" && exit 1)
	@test -n "$(TOOL)"     || (echo "Usage: make remove-permission IDENTITY=<spiffe-id> TOOL=<tool>" && exit 1)
	python3 update-policy.py --identity $(IDENTITY) --tool $(TOOL) --action remove

seed-permissions:
	@echo "→ Seeding default permissions..."
	python3 update-policy.py --identity spiffe://$(TRUST_DOMAIN)/ns/apps/sa/gateway-1 --tool read_file  --action add --no-validate
	python3 update-policy.py --identity spiffe://$(TRUST_DOMAIN)/ns/apps/sa/gateway-1 --tool exec       --action add --no-validate
	python3 update-policy.py --identity spiffe://$(TRUST_DOMAIN)/ns/apps/sa/gateway-2 --tool read_file  --action add --no-validate
	python3 update-policy.py --identity spiffe://$(TRUST_DOMAIN)/ns/apps/sa/gateway-2 --tool write_file --action add --no-validate
	python3 update-policy.py --identity spiffe://$(TRUST_DOMAIN)/ns/apps/sa/gateway-2 --tool exec       --action add --no-validate
	@echo "  Default permissions written to policy/openclaw.rego"

# ── Cleanup ──────────────────────────────────────────────────────────────────

clean-certs:
	@echo "→ Removing generated certificates..."
	rm -f spire-agent-certs/agent-ca.key spire-agent-certs/agent-ca.crt \
	      spire-agent-certs/agent.csr spire-agent-certs/agent-ext.cnf \
	      spire-agent-certs/agent.crt.pem spire-agent-certs/agent.key.pem \
	      spire-agent-certs/agent-ca.srl

clean-data:
	@echo "→ Removing SPIRE server data (forces re-attestation)..."
	sudo rm -rf spire-server-data/*

clean: stop clean-certs clean-data
	@echo "→ Clean complete."

# ── Help ─────────────────────────────────────────────────────────────────────

help:
	@echo ""
	@echo "Openclaw + SPIRE + OPA setup"
	@echo ""
	@echo "First-time install:"
	@echo "  make setup                  Generate certs, start containers, register workloads"
	@echo ""
	@echo "Lifecycle:"
	@echo "  make agent N=3              Add a new gateway (creates dir, updates compose, onboards, registers)"
	@echo "  make onboard                Run openclaw onboard for each gateway (interactive, one at a time)"
	@echo "  make start                  Start all containers"
	@echo "  make stop                   Stop all containers"
	@echo "  make restart                Restart all containers"
	@echo "  make status                 Show container status"
	@echo ""
	@echo "SPIRE:"
	@echo "  make register-workloads     Register workloads (default: openclaw-gateway openclaw-gateway-2 openclaw-cli)"
	@echo "  make register-workloads SERVICES='openclaw-gateway my-agent'  Register specific services"
	@echo "  make validate-labels        Check that each service has a matching app label in docker-compose.yml"
	@echo "  make list-agents            Show tracked services from .services"
	@echo "  make list-entries           List all registered workload entries"
	@echo "  make delete-all-entries     Delete all workload entries"
	@echo "  make agent-spiffe-id        Print the current agent SPIFFE ID"
	@echo "  make fetch-svid SERVICE=X   Fetch X509 SVID from a running container"
	@echo ""
	@echo "OPA permissions:"
	@echo "  make seed-permissions       Write default permissions to policy/openclaw.rego"
	@echo "  make add-permission IDENTITY=spiffe://... TOOL=read_file"
	@echo "  make remove-permission IDENTITY=spiffe://... TOOL=read_file"
	@echo ""
	@echo "Cleanup:"
	@echo "  make clean-certs            Regenerate TLS certificates on next setup"
	@echo "  make clean-data             Wipe SPIRE server DB (forces re-attestation)"
	@echo "  make clean                  Full reset"
	@echo ""
