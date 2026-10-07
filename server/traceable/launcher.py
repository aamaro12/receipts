"""Resolve the Mistral API key (env, then macOS Keychain) and start the server."""
from __future__ import annotations
import os, subprocess, sys

def _keychain() -> str | None:
    try:
        out = subprocess.run(["security", "find-generic-password", "-s", "ai.mistral.vibe",
                              "-a", "MISTRAL_API_KEY", "-w"], capture_output=True, text=True, timeout=10)
        return out.stdout.strip() or None if out.returncode == 0 else None
    except Exception:  # noqa: BLE001
        return None

def main() -> None:
    if not os.environ.get("MISTRAL_API_KEY"):
        key = _keychain()
        if key:
            os.environ["MISTRAL_API_KEY"] = key
        else:
            print("traceable: no MISTRAL_API_KEY in env or Keychain; read_document will fail", file=sys.stderr)
    from .server import build
    build().run()

if __name__ == "__main__":
    main()
