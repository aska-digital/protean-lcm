"""Acceptance tests 1-2 and 15: inert default, fail-open config, diff allowlist, rollback.

Registration is exercised against the host's real ``register(ctx)`` contract with a
fake context that mirrors the plugin manager's hook registry.  When a Hermes
checkout is importable the real config loader is stubbed so the *real* resolution
path runs; when it is not, the module's own defaults path is what runs, which is
the same fail-open branch.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import plugins.lane_gate as lane_gate
from plugins.lane_gate import config, gate, manifest, receipts
from support import (
    BASE_COMMIT,
    LANE_ROOT,
    PLUGIN_DIR,
    REPO_ROOT,
    FakeCtx,
    binding_of,
    generated_artifacts_are_disjoint_from_owned,
    is_allowed_change,
    is_generated_path,
    ownership_diff_range,
    valid_manifest,
)

INSIDE_OWNED = f"{LANE_ROOT}/plugins/lane_gate/gate.py"
OUTSIDE_OWNED = "/tmp/not-my-lane/notes.md"


def _stub_hermes_config(monkeypatch, config_data) -> bool:
    """Point the real config loader at a fixture dict; False when Hermes is absent."""
    try:
        import hermes_cli.config as hermes_config
    except Exception:
        return False
    monkeypatch.setattr(hermes_config, "load_config", lambda *args, **kwargs: config_data, raising=False)
    return True


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, f"git {' '.join(args)} failed: {result.stderr.strip()}"
    return result.stdout


# --------------------------------------------------------------------------- #
# 1. inert by default
# --------------------------------------------------------------------------- #


def test_inert_default_no_hook_no_receipts(monkeypatch, tmp_path):
    """Mode unset ⇒ no hook registered, calls unaffected, no receipts file."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PROTEAN_LANE_RECEIPTS", raising=False)

    settings = config.load_lane_gate_config({})
    assert settings.mode == "off"
    assert settings.hook_active is False

    _stub_hermes_config(monkeypatch, {})  # no lane_gate block at all
    ctx = FakeCtx()
    lane_gate.register(ctx)

    assert ctx.pre_tool_call_hooks == [], "no hook is registered while the gate is off"
    assert ctx.skills == [], "the worker skill is part of the active gate, not of an inert plugin"
    assert not receipts.receipt_path().exists(), "no ledger exists until the gate is on"

    # Calls are unaffected: nothing to consult, and the gate itself decides nothing.
    assert ctx.invoke_pre_tool_call(tool_name="write_file", args={"path": OUTSIDE_OWNED}) is None
    assert not receipts.receipt_path().exists()


def test_plugin_manifest_declares_the_hook():
    """plugin.yaml is the metadata contract: name plus the one hook we register."""
    text = (PLUGIN_DIR / "plugin.yaml").read_text(encoding="utf-8")
    assert "name: lane-gate" in text
    assert "provides_hooks:" in text
    assert "pre_tool_call" in text
    assert "provides_tools: []" in text


# --------------------------------------------------------------------------- #
# 2. config fail-open, never widening
# --------------------------------------------------------------------------- #

GARBAGE_BLOCKS = [
    "not-a-dict",
    42,
    None,
    ["enforce"],
    {"mode": {"nested": "enforce"}},
    {"mode": "enfroce"},
    {"mode": "yes"},
    {"mode": 1},
    {"mode": "enforced", "manifest": 17, "receipts": {"path": "/x"}, "receipt_detail": "everything"},
]


def test_yaml_off_folding_stays_inert(monkeypatch):
    """``lane_gate.mode: off`` reaches us as YAML's boolean False: it must stay inert.

    A naive string clamp would read ``False`` as an unrecognized value, clamp it
    to ``advisory``, register the hook and start writing receipts -- silently
    turning "off" into "on, recording".

    The folding premise, the resolved mode and the inert registration are all
    asserted through the pure config path, so this test is green on any supported
    local environment.  The same inert property is then re-asserted through the
    real ``hermes_cli.config`` loader when a Hermes checkout is importable; when it
    is not, that half is skipped with a reason rather than failing the suite.
    """
    try:
        import yaml
    except Exception:  # pragma: no cover - the host always ships PyYAML
        pytest.skip("PyYAML unavailable")
    folded = yaml.safe_load("lane_gate:\n  mode: off\n")
    assert folded["lane_gate"]["mode"] is False, "the premise of this test is YAML 1.1 folding"

    settings = config.load_lane_gate_config(folded)
    assert settings.mode == "off"
    assert settings.hook_active is False

    # ``true``/``on`` fold to True: recording is allowed, enforcement never is.
    assert config.load_lane_gate_config({"lane_gate": {"mode": True}}).mode == "advisory"
    assert config.load_lane_gate_config({"lane_gate": {"mode": False}}).mode == "off"

    # Pure config path: registration fed with exactly the folded settings.
    real_loader = config.load_lane_gate_config
    monkeypatch.setattr(config, "load_lane_gate_config", lambda *a, **kw: settings)
    ctx = FakeCtx()
    lane_gate.register(ctx)
    assert ctx.pre_tool_call_hooks == [], "no hook for a user who switched the gate off"
    assert ctx.skills == []
    monkeypatch.undo()
    assert config.load_lane_gate_config is real_loader, "the pure assertions above used the real one"

    # Real Hermes config path, when this environment has one.
    if not _stub_hermes_config(monkeypatch, folded):
        pytest.skip(
            "hermes_cli.config is not importable in this environment (no Hermes checkout on "
            "the path): the real-loader half of this test cannot run here. The YAML folding "
            "premise, the resolved 'off' mode and the inert registration were still asserted "
            "above through the pure config path."
        )
    real_ctx = FakeCtx()
    lane_gate.register(real_ctx)
    assert real_ctx.pre_tool_call_hooks == [], "the real loader must not register a hook either"
    assert real_ctx.skills == []


def test_config_fail_open_never_widens(monkeypatch):
    """A garbage lane_gate block resolves to defaults and never reaches enforce."""
    for garbage in GARBAGE_BLOCKS:
        settings = config.load_lane_gate_config({"lane_gate": garbage})
        assert settings.mode in ("off", "advisory"), f"{garbage!r} produced mode {settings.mode!r}"
        assert settings.mode != "enforce"
        assert settings.receipt_detail in receipts.DETAIL_MODES

    # A block that cannot be read at all degrades to pure defaults.
    assert config.load_lane_gate_config({"lane_gate": "garbage"}).as_dict()["mode"] == "off"
    assert config.load_lane_gate_config(None).mode in ("off", "advisory")

    # Registration with a garbage block succeeds; the hook is registered at most in advisory.
    stubbed = _stub_hermes_config(monkeypatch, {"lane_gate": {"mode": "enfroce", "manifest": []}})
    ctx = FakeCtx()
    lane_gate.register(ctx)  # must not raise
    if stubbed:
        assert len(ctx.pre_tool_call_hooks) == 1
        assert lane_gate._STATE["settings"]["mode"] == "advisory"
    else:
        assert ctx.pre_tool_call_hooks == [], "no Hermes config: defaults keep the gate off"

    # A broken config cannot turn an ALLOW into a wider one either.
    settings = config.load_lane_gate_config({"lane_gate": {"mode": "enforce", "manifest": 5}})
    assert settings.mode == "enforce" and settings.manifest == ""


def test_env_binding_overrides_config(monkeypatch, tmp_path):
    """The per-session env binding wins over the config file."""
    monkeypatch.setenv("PROTEAN_LANE_MANIFEST", str(tmp_path / "env.json"))
    monkeypatch.setenv("PROTEAN_LANE_RECEIPTS", str(tmp_path / "env.jsonl"))
    monkeypatch.setenv("PROTEAN_LANE_RECEIPT_DETAIL", "sanitized")

    settings = config.load_lane_gate_config(
        {"lane_gate": {"mode": "advisory", "manifest": "/config/manifest.json", "receipts": "/config/ledger.jsonl"}}
    )
    assert settings.manifest == str(tmp_path / "env.json")
    assert settings.receipts == str(tmp_path / "env.jsonl")
    assert settings.receipt_detail == "sanitized"
    assert settings.receipt_path() == str(tmp_path / "env.jsonl")

    monkeypatch.setenv("PROTEAN_LANE_RECEIPT_DETAIL", "nonsense")
    assert config.load_lane_gate_config({}).receipt_detail == "hash"


# --------------------------------------------------------------------------- #
# 15. no core diff + rollback + registration idempotence
# --------------------------------------------------------------------------- #


def test_ownership_range_uses_merge_parent_for_merged_main():
    """The default branch accepts unrelated sibling integration history."""
    assert ownership_diff_range("merge main-parent lane-parent", branch="main") is None


def test_ownership_range_keeps_linear_feature_base():
    """A feature branch uses main, then the locked base in an isolated checkout."""
    assert ownership_diff_range("linear-head", branch="fix/lane-gate", upstream="origin/main") == "origin/main..HEAD"
    assert ownership_diff_range("linear-head", branch="fix/lane-gate") == f"{BASE_COMMIT}..HEAD"


def test_feature_branch_allows_owned_change_but_rejects_unauthorized_path():
    """Feature-branch scope narrows the baseline, not the owned-file invariant."""
    assert ownership_diff_range("linear-head", branch="fix/lane-gate", upstream="origin/main")
    assert is_allowed_change("plugins/lane_gate/gate.py")
    assert not is_allowed_change("README.md")


def test_default_branch_sibling_slice_is_not_rechecked_as_lane_work():
    """A merged default branch does not relitigate a sibling slice."""
    assert ownership_diff_range("merge old-main resource-budget", branch="main") is None


def test_ownership_allowlist_rejects_real_boundary_violation():
    """The merge-baseline repair must not weaken the owned-file invariant."""
    assert not is_allowed_change("README.md")
    assert not is_allowed_change("plugins/context_engine/lcm/engine.py")


def test_no_core_diff_and_rollback(lane_settings, monkeypatch, tmp_path):
    """The branch touches only its owned files, and disable/re-enable restores stock behaviour."""
    # --- diff allowlist (lock §3.0 / §6) -----------------------------------
    changed = set()
    parent_line = _git("rev-list", "--parents", "-n", "1", "HEAD").strip()
    upstream = "origin/main" if subprocess.run(
        ["git", "cat-file", "-e", "origin/main^{commit}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    ).returncode == 0 else None
    branch_result = subprocess.run(
        ["git", "symbolic-ref", "--short", "-q", "HEAD"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        check=False,
    )
    assert branch_result.returncode in (0, 1), branch_result.stderr.strip()
    branch = branch_result.stdout.strip() or None
    diff_range = ownership_diff_range(parent_line, upstream=upstream, branch=branch)
    base_resolves = subprocess.run(
        ["git", "cat-file", "-e", f"{BASE_COMMIT}^{{commit}}"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        check=False,
    ).returncode == 0
    if diff_range is not None and (base_resolves or diff_range != f"{BASE_COMMIT}..HEAD"):
        changed.update(line.strip() for line in _git("diff", "--name-only", diff_range).splitlines() if line.strip())
    for line in _git("status", "--porcelain").splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().split(" -> ")[-1].rstrip("/")
        if is_generated_path(path):
            # The skip is only legitimate while nothing under that path is tracked:
            # a cache directory can never hide a source change from the check.
            assert _git("ls-files", path).strip() == "", f"{path} holds tracked files"
            continue
        changed.add(path)
    stray = sorted(path for path in changed if not is_allowed_change(path))
    assert stray == [], f"diff escapes the owned file set: {stray}"
    if diff_range is not None:
        assert changed, "the feature branch has changes to check"
        assert any(path.startswith(("plugins/lane_gate", "tests/lane_gate")) for path in changed)

    # The generated-artifact filter must never be able to hide a source file.
    assert generated_artifacts_are_disjoint_from_owned()
    for source in ("pyproject.toml", "PROVENANCE-LANE-GATE.md", "plugins/lane_gate/gate.py",
                   "README.md", "LICENSE", "plugins/context_engine/lcm/engine.py"):
        assert not is_generated_path(source), source

    # --- rollback ----------------------------------------------------------
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("PROTEAN_LANE_RECEIPTS", raising=False)
    manifest_path = tmp_path / "lane-manifest.json"
    manifest_path.write_text(json.dumps(valid_manifest().as_dict()), encoding="utf-8")
    ledger = tmp_path / "receipts.jsonl"
    lane_settings("advisory", manifest=str(manifest_path), receipts=str(ledger))

    ctx = FakeCtx()
    lane_gate.register(ctx)
    assert len(ctx.pre_tool_call_hooks) == 1, "enabled ⇒ the hook is registered"
    assert ctx.skills and ctx.skills[0][0] == "lane-discipline"
    assert ctx.skills[0][1].name == "SKILL.md" and ctx.skills[0][1].exists()

    lane_gate.register(ctx)
    assert len(ctx.pre_tool_call_hooks) == 1, "registration is idempotent, not additive"

    directive = ctx.invoke_pre_tool_call(
        tool_name="write_file",
        args={"path": INSIDE_OWNED, "content": "x"},
        session_id="sess",
        tool_call_id="call",
    )
    assert directive is None, "advisory records and proceeds"
    assert len(receipts.read_receipts(ledger)) == 1, "the gate recorded the call while enabled"

    # ``hermes plugins disable protean-lane-gate``: the host drops our callbacks.
    ctx.disable()
    assert ctx.pre_tool_call_hooks == []
    assert ctx.invoke_pre_tool_call(tool_name="write_file", args={"path": OUTSIDE_OWNED}) is None
    assert len(receipts.read_receipts(ledger)) == 1, "a disabled gate writes nothing"

    # Re-enable works.
    lane_gate.register(ctx)
    assert len(ctx.pre_tool_call_hooks) == 1


def test_missing_hook_api_stays_importable(lane_settings, tmp_path, ledger):
    """A host without ``register_hook`` records the limitation and keeps the pure gate working."""
    lane_settings("enforce")
    ctx = FakeCtx(with_hook_api=False)
    lane_gate.register(ctx)  # must not raise

    assert ctx.pre_tool_call_hooks == []
    assert ctx.skills and ctx.skills[0][0] == "lane-discipline"

    outcome = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": OUTSIDE_OWNED, "content": "x"},
        mode="enforce",
        manifest=binding_of(valid_manifest()),
        receipts_path=ledger,
    )
    assert (outcome.decision, outcome.reason) == ("BLOCK", "outside_owned")
    assert outcome.directive["action"] == "block"


def test_gate_package_is_stdlib_only():
    """Module-level imports are stdlib only; the Hermes import is lazy and feature-detected."""
    import ast

    stdlib = {
        "__future__",
        "fnmatch",
        "hashlib",
        "json",
        "os",
        "re",
        "shlex",
        "time",
        "urllib.parse",
        "dataclasses",
        "datetime",
        "pathlib",
        "typing",
        "logging",
    }
    lazy_only = {"hermes_cli"}
    sources = sorted(PLUGIN_DIR.glob("*.py"))
    assert sources, "the plugin package has modules to check"

    for source in sources:
        tree = ast.parse(source.read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.Import):
                names = {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = {node.module.split(".")[0]}
            else:
                continue
            unexpected = names - stdlib
            assert not unexpected, f"{source.name} imports {sorted(unexpected)} at module level"

        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.module.split(".")[0] in lazy_only:
                assert node not in tree.body, f"{source.name} imports {node.module} eagerly"
