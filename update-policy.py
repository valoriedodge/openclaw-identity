#!/usr/bin/env python3
"""
Update permitted_tools in policy/openclaw.rego.

Usage:
  python update-policy.py --identity spiffe://example.org/service/agent-a --tool write_file --action add
  python update-policy.py --identity spiffe://example.org/service/agent-a --tool write_file --action remove
"""

import argparse
import re
import subprocess
import sys
from pathlib import Path

POLICY_FILE = Path(__file__).parent / "policy" / "openclaw.rego"


def load_policy() -> str:
    return POLICY_FILE.read_text()


def save_policy(content: str) -> None:
    POLICY_FILE.write_text(content)


def validate_policy(content: str) -> bool:
    """Run opa check via docker compose if available, otherwise skip."""
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "opa", "opa", "check", "/policies"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            print(f"[error] OPA validation failed:\n{result.stderr}", file=sys.stderr)
            return False
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        # Docker not available or timed out — skip validation
        print("[warn] Could not reach OPA container to validate; skipping check.")
        return True


def get_tools_for_identity(content: str, identity: str) -> list[str] | None:
    """Return the list of tools for an identity, or None if identity not found."""
    # Match the identity key and capture its tool array.
    pattern = rf'"{re.escape(identity)}"\s*:\s*\[(.*?)\]'
    match = re.search(pattern, content, re.DOTALL)
    if not match:
        return None
    raw = match.group(1)
    return re.findall(r'"([^"]+)"', raw)


def set_tools_for_identity(content: str, identity: str, tools: list[str]) -> str:
    """Replace the tool list for an existing identity."""
    tools_str = "".join(f'\t\t"{t}",\n' for t in sorted(tools))
    replacement = f'"{identity}": [\n{tools_str}\t]'
    pattern = rf'"{re.escape(identity)}"\s*:\s*\[.*?\]'
    return re.sub(pattern, replacement, content, flags=re.DOTALL)


def add_identity(content: str, identity: str, tools: list[str]) -> str:
    """Insert a new identity block before the closing brace of permitted_tools."""
    tools_str = "".join(f'\t\t"{t}",\n' for t in sorted(tools))
    new_block = f'\t"{identity}": [\n{tools_str}\t],\n'
    # Insert before the closing `}` of the permitted_tools map.
    return re.sub(r'(permitted_tools\s*:=\s*\{.*?)(^\})',
                  lambda m: m.group(1) + new_block + m.group(2),
                  content, flags=re.DOTALL | re.MULTILINE)


def main() -> None:
    parser = argparse.ArgumentParser(description="Update OPA tool permissions.")
    parser.add_argument("--identity", required=True, help="Full SPIFFE ID")
    parser.add_argument("--tool", required=True, help="Tool name to add or remove")
    parser.add_argument("--action", required=True, choices=["add", "remove"])
    parser.add_argument("--no-validate", action="store_true", help="Skip OPA validation")
    args = parser.parse_args()

    content = load_policy()
    tools = get_tools_for_identity(content, args.identity)

    if args.action == "add":
        if tools is None:
            print(f"[info] Identity '{args.identity}' not found — creating new entry.")
            content = add_identity(content, args.identity, [args.tool])
        elif args.tool in tools:
            print(f"[info] '{args.tool}' is already permitted for '{args.identity}'. No change.")
            sys.exit(0)
        else:
            tools.append(args.tool)
            content = set_tools_for_identity(content, args.identity, tools)
            print(f"[ok] Added '{args.tool}' to '{args.identity}'.")

    elif args.action == "remove":
        if tools is None:
            print(f"[error] Identity '{args.identity}' not found in policy.", file=sys.stderr)
            sys.exit(1)
        elif args.tool not in tools:
            print(f"[info] '{args.tool}' is not in the list for '{args.identity}'. No change.")
            sys.exit(0)
        else:
            tools.remove(args.tool)
            content = set_tools_for_identity(content, args.identity, tools)
            print(f"[ok] Removed '{args.tool}' from '{args.identity}'.")

    if not args.no_validate and not validate_policy(content):
        print("[error] Policy not saved due to validation failure.", file=sys.stderr)
        sys.exit(1)

    save_policy(content)
    print(f"[ok] Policy saved to {POLICY_FILE}")


if __name__ == "__main__":
    main()
