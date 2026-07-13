import re
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

POLICY_FILE = Path(__file__).parent.parent.parent / "policy" / "openclaw.rego"


POLICY_TEMPLATE = """\
package openclaw.authz

import rego.v1

default allow := false

allow if {
\tpermitted_tools[input.spiffe_id][_] == input.tool_name
}

permitted_tools := {
}
"""


def load() -> str:
    if not POLICY_FILE.exists():
        return POLICY_TEMPLATE
    return POLICY_FILE.read_text()


def save(content: str) -> None:
    POLICY_FILE.parent.mkdir(parents=True, exist_ok=True)
    POLICY_FILE.write_text(content)


def get_tools(content: str, identity: str) -> Optional[List[str]]:
    pattern = rf'"{re.escape(identity)}"\s*:\s*\[(.*?)\]'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return None
    return re.findall(r'"([^"]+)"', match.group(1))


def set_tools(content: str, identity: str, tools: List[str]) -> str:
    tools_str = "".join(f'\t\t"{t}",\n' for t in sorted(tools))
    replacement = f'"{identity}": [\n{tools_str}\t]'
    pattern = rf'"{re.escape(identity)}"\s*:\s*\[.*?\]'
    return re.sub(pattern, replacement, content, flags=re.DOTALL)


def add_identity(content: str, identity: str, tools: List[str]) -> str:
    tools_str = "".join(f'\t\t"{t}",\n' for t in sorted(tools))
    new_block = f'\t"{identity}": [\n{tools_str}\t],\n'
    return re.sub(
        r'(permitted_tools\s*:=\s*\{.*?)(^\})',
        lambda m: m.group(1) + new_block + m.group(2),
        content,
        flags=re.DOTALL | re.MULTILINE,
    )


def validate() -> bool:
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "opa", "opa", "check", "/policies"],
            capture_output=True, text=True, timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return True


def all_identities(content: str) -> Dict[str, List[str]]:
    pattern = r'"(spiffe://[^"]+)"\s*:\s*\[(.*?)\]'
    results = {}
    for identity, raw in re.findall(pattern, content, re.DOTALL):
        results[identity] = re.findall(r'"([^"]+)"', raw)
    return results
