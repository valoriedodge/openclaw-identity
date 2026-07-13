import subprocess
import sys
from typing import Optional


PROJECT_DIR = None  # set at startup by main.py


def run(
    *args: str,
    capture: bool = False,
    check: bool = True,
    input: Optional[str] = None,
) -> subprocess.CompletedProcess:
    cmd = ["docker", "compose", *args]
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        input=input,
        cwd=PROJECT_DIR,
    )


def exec(service: str, *args: str, capture: bool = False, check: bool = True) -> subprocess.CompletedProcess:
    return run("exec", service, *args, capture=capture, check=check)


def run_interactive(service: str, *args: str) -> None:
    cmd = ["docker", "compose", "run", "--rm", "-it", service, *args]
    subprocess.run(cmd, check=True, cwd=PROJECT_DIR)


def ps(service: Optional[str] = None, quiet: bool = False) -> subprocess.CompletedProcess:
    args = ["ps"]
    if quiet:
        args.append("-q")
    if service:
        args.append(service)
    return run(*args, capture=quiet)


def container_id(service: str) -> Optional[str]:
    result = ps(service, quiet=True)
    return result.stdout.strip() or None


def container_label(service: str, label: str) -> Optional[str]:
    cid = container_id(service)
    if not cid:
        return None
    result = subprocess.run(
        ["docker", "inspect", "--format", f"{{{{index .Config.Labels \"{label}\"}}}}",  cid],
        capture_output=True, text=True, check=False,
    )
    return result.stdout.strip() or None


def logs(service: str, tail: int = 50) -> str:
    result = run("logs", "--tail", str(tail), service, capture=True, check=False)
    return result.stdout + result.stderr
