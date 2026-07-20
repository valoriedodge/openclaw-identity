import re
import subprocess
from typing import List, Optional
from . import compose

SPIRE_SERVER_BIN = "/opt/spire/bin/spire-server"
SPIRE_SERVICE    = "spire-server"


def server(*args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return compose.exec(SPIRE_SERVICE, SPIRE_SERVER_BIN, *args, capture=capture, check=check)


def agent_spiffe_id() -> Optional[str]:
    log = compose.logs("spire-agent", tail=200)
    matches = re.findall(r'spiffe://[^\s"]+', log)
    # find the last attestation success line
    for line in reversed(log.splitlines()):
        if "attestation was successful" in line:
            m = re.search(r'spiffe://[^\s"]+', line)
            if m:
                return m.group(0)
    return None


def is_healthy() -> bool:
    try:
        result = server("healthcheck", capture=True)
        return result.returncode == 0
    except Exception:
        return False


def list_entries() -> str:
    result = server("entry", "show", capture=True, check=False)
    return result.stdout or result.stderr


def create_entry(parent_id: str, spiffe_id: str, selector: str) -> subprocess.CompletedProcess:
    return server(
        "entry", "create",
        "-parentID", parent_id,
        "-spiffeID", spiffe_id,
        "-selector", selector,
        capture=True,
        check=False,
    )


def delete_entry(entry_id: str) -> subprocess.CompletedProcess:
    return server("entry", "delete", "-entryID", entry_id, capture=True)


def entry_ids() -> List[str]:
    output = list_entries()
    return re.findall(r"Entry ID\s+:\s+(\S+)", output)
