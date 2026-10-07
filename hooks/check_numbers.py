#!/usr/bin/env python3
"""Vibe post_agent hook: read JSON on stdin, print decision JSON. Never crash, and never leave a stale report."""
import json, os, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "server"))
raw = sys.stdin.read()
try:
    from traceable.check import run_hook
    out = run_hook(raw, os.getcwd())        # Vibe runs hooks in the project folder; garbage input is NOT CHECKED
except Exception as e:  # noqa: BLE001 - traceable itself failed: allow, say so, and blank any older report
    report = pathlib.Path(os.getcwd(), ".traceable", "report.html")
    try:
        if report.parent.is_dir():
            report.write_text('<!doctype html><meta charset="utf-8"><title>Receipts report</title>'
                              '<p style="color:#c62828;font-weight:600">NOT CHECKED: the traceable hook failed.</p>')
    except OSError:
        pass
    out = {"decision": "allow", "system_message": f"traceable: NOT CHECKED: hook error: {e}"}
print(json.dumps(out))
