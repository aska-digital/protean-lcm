"""Single-authority tests for the Resource Budget slice (locked §6, S1).

The slice decides nothing about lifecycle and owns no durable state. These
tests lock that down: the registration surface is exactly one hook plus one
read-only tool; the only plugin-state keys ever written are the three telemetry
keys; the unattended marker is read and never written; there is no network,
process, service, kanban, context-engine or command surface anywhere in the
slice's Python; and the vendored runtime contains no file-write API at all.
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import PLUGIN_DIR, RUNTIME_PATH, SLICE_PYTHON_PATHS  # noqa: E402

import plugins.resource_budget as resource_budget  # noqa: E402
from plugins.resource_budget import hxrb_runtime  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

TELEMETRY_KEYS = {"blocked_count_approx", "observed_count_approx", "last_event"}

# §8.3 import whitelist.
RUNTIME_MODULES = {
    "__future__",
    "json",
    "math",
    "os",
    "re",
    "shlex",
    "shutil",
    "secrets",
    "time",
    "pathlib",
    "typing",
}
ADAPTER_MODULES = {"__future__", "os", "re", "typing", "logging", "pathlib"}

FORBIDDEN_MODULE_ROOTS = {
    "subprocess",
    "socket",
    "urllib",
    "requests",
    "http",
    "ctypes",
    "signal",
    "asyncio",
    "multiprocessing",
    "threading",
    "tempfile",
    "ftplib",
    "smtplib",
    "telnetlib",
    "ssl",
    "selectors",
}

#: Capability names that would widen the slice's surface.  Scanned inside the
#: three plugin files (the enforcement surface).  This test module itself
#: necessarily contains these names as scan patterns, so it is exempt from
#: this particular scan -- the module-import scan below covers every file.
FORBIDDEN_CALLS = (
    re.compile(r"\bsubprocess\."),
    re.compile(r"\bsocket\."),
    re.compile(r"\burllib\."),
    re.compile(r"\brequests\."),
    re.compile(r"\bhttp\."),
    re.compile(r"\bctypes\."),
    re.compile(r"\bsignal\."),
    re.compile(r"\bregister_context_engine\b"),
    re.compile(r"\bregister_command\b"),
    re.compile(r"\bset_config\b"),
    re.compile(r"\bauto_approve\b|\bapprov(e|al)_tool\b"),
    re.compile(r"\bcronjob|\bdaemonize\b"),
)

#: Kanban-call forms; scanned across every slice file (lowercase only, so the
#: environment marker name never matches).
KANBAN_CALL = re.compile(r"\bkanban_[a-z_]+\(")

#: The three plugin files: the enforcement surface.
ENFORCEMENT_PATHS = (
    PLUGIN_DIR / "__init__.py",
    PLUGIN_DIR / "config.py",
    RUNTIME_PATH,
)

WRITE_FORMS = (
    re.compile(r"\bopen\("),
    re.compile(r"\bos\.open\("),
    re.compile(r"\.write_text\("),
    re.compile(r"\.write_bytes\("),
    re.compile(r"\bwritelines\("),
    re.compile(r"\.write\("),
    re.compile(r"\.mkdir\("),
    re.compile(r"\bmakedirs\("),
    re.compile(r"\.touch\("),
    re.compile(r"\.unlink\("),
    re.compile(r"\bos\.remove\("),
    re.compile(r"\bos\.rename\("),
    re.compile(r"\bos\.replace\("),
    re.compile(r"\bos\.write\("),
    re.compile(r"\bshutil\.(copy|move|rmtree|make_archive)"),
)

ENV_WRITE_FORMS = (
    re.compile(r"\bos\.environ\["),
    re.compile(r"\bos\.putenv\b"),
    re.compile(r"\bos\.unsetenv\b"),
    re.compile(r"\bos\.environ\.setdefault\b"),
)


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _imported_roots(path: Path) -> set:
    """Top-level module names imported by *path* (relative imports excluded)."""
    tree = ast.parse(_source(path), filename=str(path))
    roots: set = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


# --------------------------------------------------------------------------- #
# surface lock
# --------------------------------------------------------------------------- #


def test_registration_surface_is_exactly_one_hook_and_one_tool(make_ctx):
    ctx = make_ctx({"mode": "observe"})
    resource_budget.register(ctx)

    assert ctx.hook_names == ["pre_tool_call"]
    assert ctx.tool_names == ["resource_budget_status"]
    assert ctx.skill_calls == []

    # Idempotent: a second register() with the same context adds nothing.
    resource_budget.register(ctx)
    assert len(ctx.hook_calls) == 1
    assert len(ctx.tool_calls) == 1


def test_off_mode_registers_no_surface(make_ctx):
    ctx = make_ctx({})
    resource_budget.register(ctx)
    assert ctx.hook_calls == []
    assert ctx.tool_calls == []
    assert ctx.skill_calls == []


def test_tool_registration_carries_the_pinned_schema(enabled_ctx):
    ctx = enabled_ctx({"mode": "observe"})
    call = ctx.tool_calls[0]
    assert call["schema"] is hxrb_runtime._STATUS_SCHEMA
    assert call["name"] == "resource_budget_status"
    assert call["toolset"] == "resource_budget"


# --------------------------------------------------------------------------- #
# state authority
# --------------------------------------------------------------------------- #


def test_state_writes_are_only_the_three_telemetry_keys(enabled_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    ctx = enabled_ctx({"mode": "block_all"})

    # A scripted mix: blocked, observed-class, allowed, hostile, and an
    # injected internal error.
    ctx.hook()(tool_name="terminal", args={"command": "docker pull alpine"})
    ctx.hook()(tool_name="terminal", args={"command": "pip install requests"})
    ctx.hook()(tool_name="terminal", args={"command": "wget https://example.com/x.iso"})
    ctx.hook()(tool_name="terminal", args={"command": "bash -c 'apt-get -y upgrade'"})
    ctx.hook()(tool_name="terminal", args=None)
    monkeypatch.setattr(hxrb_runtime, "_classify", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    ctx.hook()(tool_name="terminal", args={"command": "ollama pull llama3"})
    ctx.status_json()

    written = set(ctx.state.keys_written)
    assert written <= TELEMETRY_KEYS
    assert written, "the mix never touched telemetry at all; the scan would be vacuous"
    assert "blocked_count_approx" in written and "last_event" in written


def test_telemetry_never_drives_a_decision(enabled_ctx, monkeypatch):
    """Empty the state bridge mid-session: the same calls decide the same way."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    ctx = enabled_ctx({"mode": "block_unattended"})
    first = ctx.hook()(tool_name="terminal", args={"command": "docker pull alpine"})
    ctx.state._values.clear()
    ctx.state.set_calls.clear()
    second = ctx.hook()(tool_name="terminal", args={"command": "docker pull alpine"})
    assert first is not None and second is not None
    assert first["message"].split("event_id=")[0] == second["message"].split("event_id=")[0]


def test_last_event_carries_no_command_content(enabled_ctx):
    ctx = enabled_ctx({"mode": "block_all"})
    command = "hf download acme/secret-model-name"
    ctx.hook()(tool_name="terminal", args={"command": command})
    event = ctx.state.get("last_event")
    assert event is not None
    assert "secret-model-name" not in repr(event)
    assert len(event["event_id"]) == 16  # random opaque id, never a command digest


# --------------------------------------------------------------------------- #
# HERMES_KANBAN_TASK: read, never written
# --------------------------------------------------------------------------- #


def test_hermes_kanban_task_is_read_never_written(monkeypatch, enabled_ctx):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-9")
    unattended = enabled_ctx({"mode": "block_unattended"})
    assert unattended.hook()(tool_name="terminal", args={"command": "docker pull alpine"}) is not None

    monkeypatch.delenv("HERMES_KANBAN_TASK")
    attended = enabled_ctx({"mode": "block_unattended"})
    assert attended.hook()(tool_name="terminal", args={"command": "docker pull alpine"}) is None

    for path in SLICE_PYTHON_PATHS:
        source = _source(path)
        for pattern in ENV_WRITE_FORMS:
            assert not pattern.search(source), f"{path.name}: {pattern.pattern}"
    assert "os.environ" not in _source(PLUGIN_DIR / "config.py")
    assert "os.environ" not in _source(PLUGIN_DIR / "__init__.py")


# --------------------------------------------------------------------------- #
# no network / process / service / kanban surface (import whitelist + scans)
# --------------------------------------------------------------------------- #


def test_runtime_imports_are_exactly_the_locked_whitelist():
    assert _imported_roots(RUNTIME_PATH) == RUNTIME_MODULES


@pytest.mark.parametrize("module_file", ("__init__.py", "config.py"))
def test_adapter_imports_stay_inside_the_stdlib_subset(module_file):
    roots = _imported_roots(PLUGIN_DIR / module_file)
    assert roots <= ADAPTER_MODULES, f"{module_file}: {sorted(roots - ADAPTER_MODULES)}"


def test_no_network_process_service_or_kanban_surface():
    """§6/§8.3: no forbidden imports anywhere in the slice; no forbidden
    capability names inside the enforcement files; no kanban call form."""
    for path in SLICE_PYTHON_PATHS:
        roots = _imported_roots(path)
        assert not (roots & FORBIDDEN_MODULE_ROOTS), f"{path.name}: {sorted(roots & FORBIDDEN_MODULE_ROOTS)}"
        source = _source(path)
        assert not KANBAN_CALL.search(source), f"{path.name}: kanban call form"
    for path in ENFORCEMENT_PATHS:
        source = _source(path)
        for pattern in FORBIDDEN_CALLS:
            assert not pattern.search(source), f"{path.name}: {pattern.pattern}"


def test_registration_only_uses_the_two_documented_seams():
    source = _source(PLUGIN_DIR / "__init__.py")
    seam_calls = sorted(set(re.findall(r"\bregister_(?:hook|tool|skill|command|context_engine)\b", source)))
    assert "register_hook" in seam_calls
    assert "register_tool" in seam_calls
    assert "register_skill" not in source  # N8: no skill surface
    for forbidden in ("register_command", "register_context_engine", "register_kanban"):
        assert forbidden not in source


# --------------------------------------------------------------------------- #
# the runtime writes no files
# --------------------------------------------------------------------------- #


def test_no_file_writes_in_runtime():
    source = _source(RUNTIME_PATH)
    for pattern in WRITE_FORMS:
        assert not pattern.search(source), f"hxrb_runtime.py: {pattern.pattern}"

    # The scan is not vacuous: the read-only surfaces really are there.
    assert "read_text(" in source
    assert "disk_usage(" in source
    assert "iterdir(" in source


def test_adapter_and_config_write_no_files():
    for name in ("__init__.py", "config.py"):
        source = _source(PLUGIN_DIR / name)
        for pattern in WRITE_FORMS:
            assert not pattern.search(source), f"{name}: {pattern.pattern}"


def test_no_third_party_dependencies_anywhere_in_the_slice():
    """§8.3: stdlib only across every slice .py file (pytest is test-only)."""
    import sysconfig
    stdlib = set(sys.builtin_module_names)
    purelib = Path(sysconfig.get_paths()["purelib"])
    for path in SLICE_PYTHON_PATHS:
        in_plugin = path.parent == PLUGIN_DIR
        for root in _imported_roots(path):
            if root in {"conftest", "plugins"} or root.startswith("test_"):
                continue
            if root == "pytest":
                assert not in_plugin, f"{path.name}: pytest imported by a plugin file"
                continue
            if root in stdlib:
                continue
            # Anything not builtin must NOT resolve to an installed package.
            assert not (purelib / f"{root}.py").exists(), f"{path.name}: {root}"
            assert not (purelib / root).is_dir(), f"{path.name}: {root}"
