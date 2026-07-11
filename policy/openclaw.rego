package openclaw.authz

import rego.v1

default allow := false

# Grant access if the SPIFFE ID appears in the tool's allow-list.
allow if {
	permitted_tools[input.spiffe_id][_] == input.tool_name
}

# ---------------------------------------------------------------------------
# Policy table — edit this to control which identities may call which tools.
# Keys are full SPIFFE IDs; values are arrays of permitted tool names.
# ---------------------------------------------------------------------------
permitted_tools := {
	"spiffe://example.org/service/agent-a": [
		"read_file",
		"list_directory",
	],
	"spiffe://example.org/service/agent-b": [
		"read_file",
		"write_file",
		"execute_command",
	],
	"spiffe://example.org/ns/apps/sa/gateway-2": [
		"exec",
		"read",
		"read_file",
		"write",
		"write_file",
	],
	"spiffe://example.org/ns/apps/sa/gateway-1": [
		"exec",
		"read_file",
	],
}
