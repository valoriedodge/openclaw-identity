import * as crypto from 'crypto';
import net from 'net';
import { createClient } from 'spiffe';
import { definePluginEntry } from 'openclaw/plugin-sdk/plugin-entry';

// --- SPIFFE SVID WATCHER ---
let currentSpiffeId: string | null = null;
let signingKey: crypto.KeyObject | null = null;

async function watchSvid(): Promise<void> {
  const client = createClient();
  try {
    const rpc = client.fetchX509SVID({});
    for await (const response of rpc.responses) {
      const svid = response.svids?.[0];
      if (!svid) continue;
      currentSpiffeId = svid.spiffeId?.toString() ?? null;
      signingKey = crypto.createPrivateKey({
        key: Buffer.from(svid.x509SvidKey),
        format: 'der',
        type: 'pkcs8',
      });
      console.log(`[Audit] SVID loaded: ${currentSpiffeId}`);
    }
  } catch (err) {
    console.error('[Audit] SVID stream ended, retrying in 5s:', (err as Error).message);
    setTimeout(watchSvid, 5000);
  }
}

// --- OPA AUTHORIZATION ---
const OPA_URL = process.env.OPA_URL ?? 'http://opa:8181';
const OPA_POLICY_PATH = '/v1/data/openclaw/authz/allow';

async function isAuthorizedByOpa(spiffeId: string, toolName: string, params: unknown): Promise<boolean> {
  const response = await fetch(`${OPA_URL}${OPA_POLICY_PATH}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ input: { spiffe_id: spiffeId, tool_name: toolName, params } }),
  });

  if (!response.ok) {
    throw new Error(`OPA returned HTTP ${response.status}`);
  }

  const body = await response.json() as { result?: boolean };
  return body.result === true;
}

// --- HASH CHAIN STATE ---
const GENESIS_HASH = '0'.repeat(64);
let previousHash: string = GENESIS_HASH;
let chainSequence: number = 0;

function hashPayload(payload: string): string {
  return crypto.createHash('sha256').update(payload).digest('hex');
}

// --- TCP LOGGER SETUP ---
const SIEM_HOST = process.env.SIEM_HOST || 'fluentd-logger';
const SIEM_PORT = process.env.SIEM_PORT ? parseInt(process.env.SIEM_PORT) : 24224;

let tcpClient = new net.Socket();

function connectToSIEM() {
  tcpClient.connect(SIEM_PORT, SIEM_HOST, () => {
    console.log(`[Audit] Connected to SIEM at ${SIEM_HOST}:${SIEM_PORT} via TCP`);
  });
}

tcpClient.on('error', (err) => {
  console.error(`[Audit] SIEM TCP connection error:`, err.message);
});

tcpClient.on('close', () => {
  console.warn(`[Audit] SIEM connection closed. Reconnecting in 5s...`);
  setTimeout(connectToSIEM, 5000);
});

connectToSIEM();

function sendSignedAuditLog(entry: object, key: crypto.KeyObject) {
  const chainedEntry = {
    ...entry,
    previousHash,
    sequence: chainSequence,
  };

  const payload = JSON.stringify(chainedEntry);
  const currentHash = hashPayload(payload);
  const signature = crypto.sign('sha256', Buffer.from(payload), key).toString('base64');
  const message = Buffer.from(JSON.stringify({ payload: chainedEntry, hash: currentHash, signature }) + '\n');

  if (!tcpClient.pending && !tcpClient.destroyed) {
    tcpClient.write(message);
    previousHash = currentHash;
    chainSequence += 1;
  } else {
    console.error('[Audit] Cannot write audit log: TCP socket is disconnected.');
  }
}

export default definePluginEntry({
  id: "spiffe-security-enforcer",
  name: "SPIFFE Zero Trust Enforcer",
  register(api) {
    api.on('gateway_start', async () => {
      watchSvid().catch((err) =>
        console.error('[Audit] Failed to start SVID watcher:', err)
      );
    });

    api.on('before_tool_call', async (event) => {
      console.log(`[Audit] Tool call intercepted: ${event.toolName}`);

      if (!signingKey || !currentSpiffeId) {
        return { block: true, blockReason: 'Audit failure: SPIFFE SVID not yet available.' };
      }

      // --- OPA policy check ---
      let opaDecision: boolean;
      try {
        opaDecision = await isAuthorizedByOpa(currentSpiffeId, event.toolName, event.params);
      } catch (error) {
        console.error('[Audit] OPA query failed:', error);
        // Fail closed: deny on OPA unavailability.
        return {
          block: true,
          blockReason: `Authorization failure: OPA unreachable (${error instanceof Error ? error.message : String(error)})`,
        };
      }

      const auditEntry = {
        type: 'TOOL_POLICY_EVALUATION',
        spiffeId: currentSpiffeId,
        agentId: event.runId ?? 'unknown',
        toolName: event.toolName,
        params: event.params,
        opaDecision,
        timestamp: Date.now(),
      };

      try {
        sendSignedAuditLog(auditEntry, signingKey);
        console.log(`[Audit] Log sent for tool: ${event.toolName}, allowed: ${opaDecision}`);
      } catch (error) {
        console.error('[Audit] Failed to send audit log:', error);
        return {
          block: true,
          blockReason: `Audit failure: could not sign and log tool call (${error instanceof Error ? error.message : String(error)})`,
        };
      }

      if (!opaDecision) {
        return {
          block: true,
          blockReason: `Policy denied: identity ${currentSpiffeId} is not permitted to call tool '${event.toolName}'.`,
        };
      }
    }, { priority: 100 });
  },
});
