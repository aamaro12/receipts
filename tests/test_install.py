# tests/test_install.py
import json, os, re, shlex, shutil, subprocess, sys, tomllib
import pytest
import tomli_w
from conftest import ROOT
from traceable.install_config import merge_config, install_config

ALWAYS = {"permission": "always"}

def test_merge_keeps_other_servers_and_tools_and_replaces_ours():
    cfg = {"theme": "x", "mcp_servers": [{"name": "other", "transport": "stdio", "command": ["o"]}, {"name": "traceable", "command": ["old"]}],
           "tools": {"bash": {"permission": "ask"}, "traceable_read_document": {"permission": "ask"}, "traceable_old": {}}}
    out = merge_config(cfg, "/p y/python")
    assert [s["name"] for s in out["mcp_servers"]] == ["other", "traceable"]
    assert out["mcp_servers"][1]["command"] == ["/p y/python"] and out["mcp_servers"][1]["args"] == ["-m", "traceable.launcher"]
    assert out["tools"] == {"bash": {"permission": "ask"}, "traceable_read_document": ALWAYS,
                            "traceable_calculate": ALWAYS, "traceable_calculate_many": ALWAYS,
                            "traceable_ask_document": ALWAYS, "traceable_web_search": ALWAYS,
                            "traceable_library_search": ALWAYS}

@pytest.mark.parametrize("before", ['theme = "flexoki"\n', 'theme = "x"\nmcp_servers = []\n', ""])
def test_install_config_is_idempotent_and_survives_a_vibe_rewrite(tmp_path, before):
    cfg = tmp_path / "config.toml"
    cfg.write_text(before)
    install_config(cfg, "/py")
    install_config(cfg, "/py")
    data = tomllib.loads(cfg.read_text())
    assert [s["name"] for s in data["mcp_servers"]] == ["traceable"] and (tmp_path / "config.toml.bak").exists()
    cfg.write_text(tomli_w.dumps(data))               # Vibe rewrites the file with its own TOML writer
    install_config(cfg, "/py")
    assert [s["name"] for s in tomllib.loads(cfg.read_text())["mcp_servers"]] == ["traceable"]

@pytest.mark.parametrize("name", ["My Docs", "My $Docs", 'q"x'])
def test_install_sh_with_awkward_repo_paths(tmp_path, name):
    repo = tmp_path / name / "receipts"
    for sub in ("server", "hooks", "skill"):
        shutil.copytree(ROOT / sub, repo / sub, ignore=shutil.ignore_patterns(".venv", "__pycache__"))
    shutil.copy(ROOT / "install.sh", repo / "install.sh")
    home = tmp_path / "home"
    # No stand-in executables (no fake uv or python anywhere): TN_PYTHON runs the installer with this test's
    # Python and TN_SKIP_SYNC skips uv sync, while config.toml and hooks.toml still name the repo's venv Python.
    env = {**os.environ, "VIBE_HOME": str(home), "TN_PYTHON": sys.executable, "TN_SKIP_SYNC": "1"}
    r = subprocess.run(["bash", str(repo / "install.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert "vibe --legacy-harness" in r.stdout and "enable_auto_update = false" in r.stdout
    assert "receipts" in r.stdout                                                             # the wrapper
    cfg = tomllib.loads((home / "config.toml").read_text())
    assert cfg["mcp_servers"][0]["command"] == [str(repo / "server/.venv/bin/python")]
    assert (home / "skills/traceable-numbers/SKILL.md").exists()
    cmd = tomllib.loads((repo / "demo/finance/.vibe/hooks.toml").read_text())["hooks"][0]["command"]
    assert shlex.split(cmd) == [str(repo / "server/.venv/bin/python"), str(repo / "hooks/check_numbers.py")]  # as sh reads it
    out = subprocess.run([sys.executable, str(repo / "hooks/check_numbers.py")], input="{}", capture_output=True, text=True,
                         cwd=tmp_path)
    assert out.returncode == 0 and json.loads(out.stdout)["decision"] == "allow", out.stderr


from traceable.install_config import PROMPT

def test_server_prompt_carries_the_core_rules():  # Vibe appends it as "Hint:" to every traceable tool description
    server = merge_config({}, "/py")["mcp_servers"][0]
    assert server["prompt"] == PROMPT and len(PROMPT) <= 700 and "\u2014" not in PROMPT
    for phrase in ("traceable_read_document", "[Dd:pP:bB]", "[Cn]", "traceable_calculate_many", "exactly as written",
                   "scale", "format pct", "never multiply by 100", "write it as",
                   "never read PDFs or .traceable files with other tools",
                   "A figure in another scale needs no calculation: write $20.3bn [block] for 20,269 in a $ million table"):
        assert phrase in PROMPT, phrase
    assert "traceable_web_search, not web_search" in PROMPT and "traceable_ask_document" in PROMPT

def test_prompt_survives_the_toml_round_trip(tmp_path):
    cfg = tmp_path / "config.toml"
    install_config(cfg, "/py")
    assert tomllib.loads(cfg.read_text())["mcp_servers"][0]["prompt"] == PROMPT

def test_read_only_arm_hides_the_calculator_and_the_rules():
    out = merge_config({"tools": {"traceable_calculate": ALWAYS}}, "/py", read_only=True)
    server = out["mcp_servers"][0]
    assert server["env"]["TRACEABLE_TOOLS"] == "read" and "prompt" not in server
    assert out["tools"] == {"traceable_read_document": ALWAYS}


from traceable.verdict import summary, LOUD
from traceable.check import run as check_run

def test_backups_keep_the_original_and_add_a_timestamped_copy(tmp_path):  # a backup once lost the file's comments
    cfg = tmp_path / "config.toml"
    original = '# keep the default model pinned for the demo\nactive_model = "mistral-medium"   # inline comment\n'
    cfg.write_text(original)
    for _ in range(3):
        install_config(cfg, "/py")
    assert (tmp_path / "config.toml.bak").read_text() == original          # the first backup is never overwritten
    stamped = sorted(tmp_path.glob("config.toml.bak-*"))
    assert len(stamped) == 3 and stamped[0].read_text() == original
    assert all(re.fullmatch(r"config\.toml\.bak-\d{8}-\d{6}(-\d+)?", p.name) for p in stamped)
    assert tomllib.loads(cfg.read_text())["active_model"] == "mistral-medium"

def test_the_skill_carries_the_reading_and_scale_rules():
    skill = (ROOT / "skill/traceable-numbers/SKILL.md").read_text()
    for phrase in ("Never read PDFs or `.traceable` files with other tools; use `traceable_read_document`",
                   "PDF page numbers (1 = first page of the file), not the numbers printed on the pages",
                   "A figure in another scale needs no calculation: write $20.3bn [block] for 20,269 in a $ million table",
                   "from row"):
        assert phrase in skill, phrase
    assert "\u2014" not in skill

def test_the_hook_script_survives_garbage_stdin(tmp_path):
    from test_check import setup, transcript, Q, B
    setup(tmp_path)
    check_run(transcript(tmp_path, [Q, {"role": "assistant", "content": f"Revenue was £9,434m {B}."}]))
    out = subprocess.run([sys.executable, str(ROOT / "hooks/check_numbers.py")], input="garbage", cwd=tmp_path,
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["system_message"].startswith("traceable: NOT CHECKED: the hook input was not valid JSON")
    html = (tmp_path / ".traceable/report.html").read_text()
    assert "NOT CHECKED" in html and "9,434" not in html

def test_the_verdict_names_the_decision_the_counts_and_the_report(tmp_path):  # what ./receipts prints after each run
    from test_check import setup, transcript, Q, B
    assert summary(tmp_path) == "traceable: no document was read in this folder, so nothing was checked"
    setup(tmp_path)
    check_run(transcript(tmp_path, [Q, {"role": "assistant", "content": f"Revenue was £9,434m {B}."}]))
    report = (tmp_path / ".traceable/report.html").resolve()
    assert summary(tmp_path, 0) == f"traceable: checked: allow · 1 figure: 1 traced, 0 calculated, 0 untraced · report: {report}"
    s = summary(tmp_path, 1)                                                # the hook did not run (untrusted folder?)
    assert s.startswith(LOUD) and "!!! traceable: NOT CHECKED: no check was recorded for this run" in s
    assert "a turn, price or token limit ends the run before Vibe's post-agent hooks can check it" in s   # the usual cause, named first
    check_run({"cwd": str(tmp_path), "hook_event_name": "post_agent"})     # the unified harness: no transcript
    s = summary(tmp_path, 1)
    assert s.splitlines()[0] == LOUD and "!!! traceable: NOT CHECKED: this Vibe harness does not pass the transcript" in s
    check_run(transcript(tmp_path, [Q, {"role": "assistant", "content": f"Revenue was £9,999m {B}."}]))
    assert summary(tmp_path).startswith("traceable: DENIED (attempt ")

def test_receipts_runs_vibe_with_the_legacy_harness_and_prints_the_verdict(tmp_path):
    from test_check import setup, transcript, Q, B
    folder = tmp_path / "demo"
    folder.mkdir()
    setup(folder)
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps(transcript(folder, [Q, {"role": "assistant", "content": f"Revenue was £9,434m {B}."}])))
    fake = tmp_path / "fake-vibe"                   # stands in for vibe: records its arguments and runs the real hook once
    fake.write_text(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{tmp_path}/args.txt"\n'
                    f'"{sys.executable}" "{ROOT / "hooks/check_numbers.py"}" < "{payload}" > /dev/null\n')
    fake.chmod(0o755)
    env = {**os.environ, "TN_VIBE": str(fake), "TN_PYTHON": sys.executable, "TN_TIMEOUT": "60"}
    r = subprocess.run(["bash", str(ROOT / "receipts"), "-p", "a question", "--trust"], cwd=folder, env=env, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    # bash is switched off: a run that went looking for the file with bash stalled on the approval
    # and --trust: Vibe runs a folder's hooks only in a trusted folder; after the 2.25.8 update none of the demo
    # folders was trusted, so an interactive session ran with no check at all
    assert (tmp_path / "args.txt").read_text().splitlines() == ["--legacy-harness", "--trust", "--disabled-tools", "bash",
                                                                "-p", "a question", "--trust"]
    assert r.stdout.startswith("traceable: checked: allow · 1 figure: 1 traced"), r.stdout
    r = subprocess.run(["bash", str(ROOT / "receipts")], cwd=folder, env={**env, "TN_VIBE": "false"}, capture_output=True,
                       text=True, stdin=subprocess.DEVNULL)
    assert r.returncode == 1 and "!!! traceable: NOT CHECKED: no check was recorded for this run" in r.stdout


def test_receipts_before_install_says_not_checked_and_how_to_fix(tmp_path):
    r = subprocess.run(["bash", str(ROOT / "receipts"), "-p", "q"], cwd=tmp_path, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env={**os.environ, "TN_VIBE": "true", "TN_PYTHON": str(tmp_path / "no-python")})
    assert "NOT CHECKED" in r.stdout and "./install.sh" in r.stdout and "No such file" not in r.stderr


def test_logistics_pdfs_are_byte_reproducible(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("mk", ROOT / "demo/make_logistics_docs.py")
    mk = importlib.util.module_from_spec(spec); spec.loader.exec_module(mk)
    name, (title, lines) = next(iter(mk.DOCS.items()))
    mk.write(tmp_path / "a.pdf", title, lines)
    import time; time.sleep(1.1)                          # a creation timestamp would now differ
    mk.write(tmp_path / "b.pdf", title, lines)
    assert (tmp_path / "a.pdf").read_bytes() == (tmp_path / "b.pdf").read_bytes()


def test_receipts_runs_on_mistral_medium_unless_a_model_is_set(tmp_path):
    # A Vibe experiment made GLM-5.3 the active model on this machine; the demo must run on Mistral's model
    fake = tmp_path / "fake-vibe"
    fake.write_text(f'#!/bin/sh\nprintf "%s" "$VIBE_ACTIVE_MODEL" > "{tmp_path}/model.txt"\n')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "VIBE_ACTIVE_MODEL"}
    env.update(TN_VIBE=str(fake), TN_PYTHON=sys.executable)
    subprocess.run(["bash", str(ROOT / "receipts"), "-p", "q"], cwd=tmp_path, env=env, capture_output=True, stdin=subprocess.DEVNULL)
    assert (tmp_path / "model.txt").read_text() == "mistral-medium-3.5"
    subprocess.run(["bash", str(ROOT / "receipts"), "-p", "q"], cwd=tmp_path, env={**env, "VIBE_ACTIVE_MODEL": "local"},
                   capture_output=True, stdin=subprocess.DEVNULL)
    assert (tmp_path / "model.txt").read_text() == "local"


def test_the_server_entry_names_its_toolset():
    # a changed toolset must change the server's config: Vibe 2.25.8 caches the tool list for 24 h keyed by that config
    from traceable.install_config import merge_config, TOOLSET
    entry = next(s for s in merge_config({}, "/py")["mcp_servers"] if s["name"] == "traceable")
    assert entry["env"]["TRACEABLE_TOOLSET"] == TOOLSET
    ro = next(s for s in merge_config({}, "/py", read_only=True)["mcp_servers"] if s["name"] == "traceable")
    assert ro["env"] == {"TRACEABLE_TOOLS": "read", "TRACEABLE_TOOLSET": TOOLSET}


def test_receipts_caps_a_stalled_model_request_unless_set(tmp_path):
    # A live run sat 7 minutes on one model request; Vibe's default read timeout is 720 s, and a timeout is
    # retried, so a short cap turns a stall into a retry
    fake = tmp_path / "fake-vibe"
    fake.write_text(f'#!/bin/sh\nprintf "%s" "$VIBE_API_TIMEOUT" > "{tmp_path}/t.txt"\n')
    fake.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if k != "VIBE_API_TIMEOUT"}
    env.update(TN_VIBE=str(fake), TN_PYTHON=sys.executable)
    subprocess.run(["bash", str(ROOT / "receipts"), "-p", "q"], cwd=tmp_path, env=env, capture_output=True, stdin=subprocess.DEVNULL)
    assert (tmp_path / "t.txt").read_text() == "120"
    subprocess.run(["bash", str(ROOT / "receipts"), "-p", "q"], cwd=tmp_path, env={**env, "VIBE_API_TIMEOUT": "600"},
                   capture_output=True, stdin=subprocess.DEVNULL)
    assert (tmp_path / "t.txt").read_text() == "600"
