"""Merge the traceable MCP server and tool permissions into Vibe's config.toml, and write a project hooks.toml.

Runs with the server's Python 3.12 (tomllib) and tomli-w. Comments in config.toml are not kept, so the file is backed
up first: config.toml.bak keeps the original (it is never overwritten), and every run adds config.toml.bak-<timestamp>.

usage: python -m traceable.install_config config <config.toml> <python> [--read-only]
       python -m traceable.install_config hooks <hooks.toml> <python> <hook.py>

--read-only is the eval baseline arm: only traceable_read_document, and no rules in the server prompt.
"""
from __future__ import annotations
import pathlib, shlex, shutil, sys, time, tomllib
import tomli_w

SERVER = "traceable"
TOOLS = ("traceable_read_document", "traceable_calculate", "traceable_calculate_many",
         "traceable_ask_document", "traceable_web_search", "traceable_library_search")
HOOK = "traceable-check"
# Vibe appends this to every tool description of the server as "Hint: ...", so the rules reach the model even when
# the skill is not pulled in. The skill stays as the longer documentation.
PROMPT = ("Read PDFs only with traceable_read_document or traceable_ask_document (absolute path); never read PDFs or "
          ".traceable files with other tools. For the web use traceable_web_search, not web_search. Put a citation right "
          "after every figure: its block, quote or source ID, or [Cn] if computed ([Dd:pP:bB] for a block). A figure in "
          "another scale needs no calculation: write $20.3bn [block] for 20,269 in a $ million table. Compute derived and "
          "change figures in one traceable_calculate_many call, inputs exactly as written, with their scale. Percentages: "
          "ratio with format pct; never multiply by 100. Copy the \"write it as\" text.")

# Changes the server's config whenever the tool list changes: Vibe 2.25.8 caches a server's tool list for 24 hours,
# keyed by its config, so a new version must look like a new server.
TOOLSET = "2026-09-29"


def merge_config(cfg: dict, python: str, *, read_only: bool = False) -> dict:
    """Drop any previous traceable server and traceable_* tool entries, then add the current ones."""
    servers = cfg.get("mcp_servers", [])
    if not isinstance(servers, list):
        raise SystemExit("config.toml: mcp_servers must be a list of tables")
    entry = {"name": SERVER, "transport": "stdio", "command": [python], "args": ["-m", "traceable.launcher"],
             "startup_timeout_sec": 30, "tool_timeout_sec": 300}
    entry["env"] = {"TRACEABLE_TOOLSET": TOOLSET}
    if read_only:
        entry["env"] = {"TRACEABLE_TOOLS": "read", **entry["env"]}
    else:
        entry["prompt"] = PROMPT
    cfg["mcp_servers"] = [s for s in servers if not (isinstance(s, dict) and s.get("name") == SERVER)] + [entry]
    tools = cfg.get("tools", {})
    if not isinstance(tools, dict):
        raise SystemExit("config.toml: tools must be a table")
    ours = TOOLS[:1] if read_only else TOOLS
    cfg["tools"] = {k: v for k, v in tools.items() if not k.startswith("traceable_")} | {t: {"permission": "always"} for t in ours}
    return cfg

def _backup(path: pathlib.Path) -> None:
    """Keep the first backup (config.toml.bak, the original with its comments) and add a timestamped copy each run.
    Without this, a second install overwrote config.toml.bak with the already rewritten file, and the comments were lost."""
    original = path.with_name(path.name + ".bak")
    if not original.exists():
        shutil.copy2(path, original)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target, n = path.with_name(f"{path.name}.bak-{stamp}"), 1
    while target.exists():
        n += 1
        target = path.with_name(f"{path.name}.bak-{stamp}-{n}")
    shutil.copy2(path, target)

def install_config(path: pathlib.Path, python: str, *, read_only: bool = False) -> None:
    cfg = {}
    if path.exists():
        _backup(path)
        cfg = tomllib.loads(path.read_text())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(merge_config(cfg, python, read_only=read_only)))

def write_hooks(path: pathlib.Path, python: str, hook: str) -> None:
    """Add (or replace) the post_agent hook; shlex.join keeps spaces, quotes and $ in paths safe for the shell."""
    doc = tomllib.loads(path.read_text()) if path.exists() else {}
    others = [h for h in doc.get("hooks", []) if h.get("name") != HOOK]
    doc["hooks"] = others + [{"name": HOOK, "type": "post_agent", "command": shlex.join([python, hook]), "timeout": 30}]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(doc))

def main(argv: list[str]) -> None:
    if len(argv) in (3, 4) and argv[0] == "config" and argv[3:] in ([], ["--read-only"]):
        install_config(pathlib.Path(argv[1]), argv[2], read_only=argv[3:] == ["--read-only"])
    elif len(argv) == 4 and argv[0] == "hooks":
        write_hooks(pathlib.Path(argv[1]), argv[2], argv[3])
    else:
        raise SystemExit(__doc__)

if __name__ == "__main__":
    main(sys.argv[1:])
