"""Decision-path tests for the Resource Budget slice.

Covers the locked error-path table (fail-closed directions for internal errors,
fail-open clamping for resolution faults), the closed recognized-invocation
matrix against the vendored classifier, the mode matrix, and the never-raise
contract of the wrapper.  Everything runs host-free through the fake context;
no Hermes checkout and no real pressure signals are needed.
"""

from __future__ import annotations

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import plugins.resource_budget as resource_budget  # noqa: E402
from plugins.resource_budget import config as rb_config  # noqa: E402
from plugins.resource_budget import hxrb_runtime  # noqa: E402

from conftest import S_D_PLAIN, RUNTIME_PATH  # noqa: E402

# --------------------------------------------------------------------------- #
# The frozen executable set (locked §4.2).  The classifier's closed dispatch
# set; this literal is the documentation pin -- grammar changes require a new
# upstream-shaped decision, not an edit here (non-goal N5).
# --------------------------------------------------------------------------- #

RECOGNIZED_EXECUTABLES = frozenset(
    {
        "docker",
        "podman",
        "docker-compose",
        "podman-compose",
        "git",
        "ollama",
        "hf",
        "huggingface-cli",
        "fio",
        "stress",
        "stress-ng",
        "sysbench",
        "dd",
        "curl",
        "wget",
        "aria2c",
        "apt",
        "apt-get",
        "dnf",
        "yum",
        "zypper",
        "pacman",
        "hermes",
        "systemctl",
    }
)

FOLLOWED_WRAPPERS = frozenset(
    {
        "command",
        "exec",
        "nohup",
        "time",
        "sudo",
        "env",
        "nice",
        "ionice",
        "timeout",
        "taskset",
    }
)

SHELL_LAUNCHERS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "fish"})

#: (§4.3) declared parse bounds, pinned as documentation of the vendored file.
PARSE_BOUNDS = {"expansion_depth": 6, "launcher_nesting": 3}

#: One positive + one negative per §4 category, plus the wrapper/launcher forms.
#: ``positive`` must be classified (blocked under block_all); ``negative`` must
#: fall outside the grammar (allowed even under block_all).
CATEGORY_MATRIX = (
    pytest.param("ollama pull llama3", "ollama list", id="ollama"),
    pytest.param("docker pull alpine", "docker ps", id="docker-pull"),
    pytest.param("docker build .", "docker images", id="docker-build"),
    pytest.param("docker buildx build .", "docker buildx ls", id="buildx"),
    pytest.param("docker compose --progress plain build", "docker compose up -d", id="compose"),
    pytest.param("docker-compose build", "docker-compose ps", id="docker-compose"),
    pytest.param("podman-compose build", "podman-compose up", id="podman-compose"),
    pytest.param("podman pull alpine", "podman ps", id="podman"),
    pytest.param("docker --context remote pull alpine", "docker --context remote ps", id="docker-global-opts"),
    pytest.param("docker image pull alpine", "docker image ls", id="docker-image"),
    pytest.param("git -C /repo lfs pull", "git status", id="git-lfs"),
    pytest.param("git lfs fetch --all", "git fetch origin", id="git-lfs-fetch"),
    pytest.param("hf download org/model", "hf whoami", id="hf"),
    pytest.param("huggingface-cli download org/model", "huggingface-cli env", id="huggingface-cli"),
    pytest.param("fio bench.fio", "fio --version", id="fio"),
    pytest.param("stress-ng --cpu 2", "stress-ng --version", id="stress-ng"),
    pytest.param("sysbench cpu run", "sysbench cpu prepare", id="sysbench"),
    pytest.param("dd if=/dev/zero of=/dev/sdb bs=1M", "dd if=input.img of=output.img bs=1M", id="dd"),
    pytest.param("curl -O https://example.com/big.tar.gz", "curl -sS https://example.com/api.json", id="curl"),
    pytest.param("wget https://example.com/model.gguf", "wget https://example.com/page.html", id="wget"),
    pytest.param("aria2c https://example.com/weights.safetensors", "aria2c https://example.com/page", id="aria2c"),
    pytest.param("apt-get -y upgrade", "apt-get install vim", id="apt-get"),
    pytest.param("apt upgrade", "apt search nginx", id="apt"),
    pytest.param("dnf distro-sync", "dnf search nginx", id="dnf"),
    pytest.param("yum update", "yum info nginx", id="yum"),
    pytest.param("zypper dup", "zypper ps", id="zypper"),
    pytest.param("pacman -Syu", "pacman -S vim", id="pacman"),
    pytest.param("hermes gateway restart", "hermes plugins list", id="hermes-gateway"),
    pytest.param("hermes -p coder gateway stop", "hermes -p coder plugins list", id="hermes-profile-flags"),
    pytest.param("systemctl --user restart hermes-gateway.service", "systemctl --user restart nginx.service", id="systemd-unit"),
    pytest.param("systemctl --user restart hermes-gateway-coder", "systemctl --user restart postgres", id="systemd-suffixless"),
    pytest.param("sudo bash -c 'docker pull alpine'", "sudo ls -la", id="sudo-bash-c"),
    pytest.param("env sh -c 'apt-get upgrade'", "env PATH=/usr/bin ls", id="env-sh-c"),
    pytest.param("timeout 30 docker build .", "timeout 30 ls -la", id="timeout-wrapper"),
    pytest.param("nohup nice -n 5 pacman -Syu", "nohup sleep 5", id="nohup-nice"),
    pytest.param("command docker pull alpine", "command -v docker", id="command-wrapper"),
    pytest.param("docker pull nginx && apt-get update", "docker ps && uptime", id="segmented"),
    pytest.param("echo start; wget https://example.com/disk.iso", "echo 'docker pull alpine'", id="after-separator"),
)

#: (§4.3) forms the finite grammar does not recognize: direction locked ALLOW.
UNRECOGNIZED_COMMANDS = (
    "pip install requests",
    "npm install left-pad",
    "cargo build --release",
    "make -j8",
    "python3 x.py",
    "node server.js",
    "bash script.sh",
    "./build-thing",
    "ruby app.rb",
    "go build ./...",
)

RECOGNIZED_HEAVY = "docker pull alpine"
RECOGNIZED_BULK = "apt-get -y upgrade"


def _decide(ctx, command: str, tool_name: str = "terminal"):
    """Call the registered wrapper exactly as the host would."""
    return ctx.hook()(tool_name=tool_name, args={"command": command})


# --------------------------------------------------------------------------- #
# closed-set and bound pins (documentation literals)
# --------------------------------------------------------------------------- #


def test_frozen_executable_set_matches_the_pinned_runtime():
    """Every name in the frozen set is a dispatch literal inside the pinned file."""
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    for name in sorted(RECOGNIZED_EXECUTABLES | FOLLOWED_WRAPPERS | SHELL_LAUNCHERS):
        assert f'"{name}"' in source, f"{name} is not in the pinned grammar"


def test_parse_bounds_are_declared_as_documented():
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    assert f"if _depth < {PARSE_BOUNDS['expansion_depth']}:" in source
    assert f"depth < {PARSE_BOUNDS['launcher_nesting']}" in source


# --------------------------------------------------------------------------- #
# recognition matrix against the vendored classifier (§4.2)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("positive,negative", CATEGORY_MATRIX)
def test_recognized_invocation_matrix(enabled_ctx, positive, negative):
    ctx = enabled_ctx({"mode": "block_all"})
    directive = _decide(ctx, positive)
    assert directive is not None, f"recognized invocation not classified: {positive!r}"
    assert directive["action"] == "block"

    assert _decide(enabled_ctx({"mode": "block_all"}), negative) is None

    observed = enabled_ctx({"mode": "observe"})
    assert _decide(observed, positive) is None  # observe records, never denies


@pytest.mark.parametrize("command", UNRECOGNIZED_COMMANDS)
def test_unrecognized_invocation_is_allowed_in_block_all(enabled_ctx, command):
    """Direction locked: an unmodeled form runs; a whitelist would not (N4)."""
    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, command) is None


def test_only_exact_native_terminal_tool_is_intercepted(enabled_ctx):
    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, RECOGNIZED_HEAVY, tool_name="browser_exec") is None
    assert _decide(ctx, RECOGNIZED_HEAVY, tool_name="") is None
    assert _decide(ctx, RECOGNIZED_HEAVY, tool_name="Terminal") is None
    assert _decide(ctx, RECOGNIZED_HEAVY)["action"] == "block"


# --------------------------------------------------------------------------- #
# mode matrix (§2, §3)
# --------------------------------------------------------------------------- #


def test_mode_matrix_block_unattended(enabled_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-42")
    ctx = enabled_ctx({"mode": "block_unattended"})
    blocked = _decide(ctx, RECOGNIZED_HEAVY)
    assert blocked is not None and blocked["action"] == "block"
    assert "unattended=true" in blocked["message"]

    monkeypatch.delenv("HERMES_KANBAN_TASK")
    foreground = enabled_ctx({"mode": "block_unattended"})
    assert _decide(foreground, RECOGNIZED_HEAVY) is None

    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-42")
    unknown = enabled_ctx({"mode": "block_unattended"})
    assert _decide(unknown, "pip install requests") is None


def test_block_all_blocks_foreground(enabled_ctx, monkeypatch):
    monkeypatch.delenv("HERMES_KANBAN_TASK", raising=False)
    ctx = enabled_ctx({"mode": "block_all"})
    directive = _decide(ctx, RECOGNIZED_BULK)
    assert directive is not None and directive["action"] == "block"
    assert "mode=block_all" in directive["message"]


def test_observe_never_denies_but_records(enabled_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-42")
    ctx = enabled_ctx({"mode": "observe"})
    assert _decide(ctx, RECOGNIZED_HEAVY) is None
    assert ctx.state.get("observed_count_approx", 0) == 1
    assert ctx.state.get("blocked_count_approx", 0) == 0


def test_pressure_only_requires_pressure(enabled_ctx, monkeypatch):
    """Missing platform signals are unavailable, not evidence of pressure."""
    monkeypatch.setattr(hxrb_runtime, "_host_snapshot", lambda: {})
    ctx = enabled_ctx({"mode": "pressure_only"})
    assert _decide(ctx, RECOGNIZED_HEAVY) is None

    monkeypatch.setattr(
        hxrb_runtime,
        "_host_snapshot",
        lambda: {
            "home_fs_disk_free_gb": 0.1,
            "memory_available_gb": 0.01,
            "memory_available_percent": 0.4,
            "load_per_cpu": 99.0,
        },
    )
    pressuring = enabled_ctx({"mode": "pressure_only"})
    directive = _decide(pressuring, RECOGNIZED_HEAVY)
    assert directive is not None and directive["action"] == "block"


def test_git_clone_default_and_opt_in(enabled_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-42")
    default = enabled_ctx({"mode": "block_unattended"})
    assert _decide(default, "git clone https://example.com/repo.git") is None

    opted = enabled_ctx({"mode": "block_unattended", "block_git_clone": True})
    directive = _decide(opted, "git clone https://example.com/repo.git")
    assert directive is not None and directive["action"] == "block"


def test_gateway_protection_can_be_disabled(enabled_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-42")
    on = enabled_ctx({"mode": "block_unattended"})
    assert _decide(on, "hermes gateway restart")["action"] == "block"

    off = enabled_ctx({"mode": "block_unattended", "protect_gateway_restart": False})
    assert _decide(off, "hermes gateway restart") is None


def test_profile_alias_recognition_requires_proof(enabled_ctx, tmp_path, monkeypatch):
    """On-disk profile proof decides the alias category (upstream behavior)."""
    bot = tmp_path / "profiles" / "definitelynotarealprofile"
    bot.mkdir(parents=True)
    (bot / "config.yaml").write_text("profile: {}\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, "definitelynotarealprofile gateway restart")["action"] == "block"
    assert _decide(ctx, "nosuchprofile gateway restart") is None


# --------------------------------------------------------------------------- #
# inertness of non-executing text (§4.1)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "command",
    (
        "echo 'docker pull alpine' > README.txt",
        "grep -R 'docker pull alpine' .",
        "cat <<EOF\n  docker pull alpine\nEOF",
        "cat <<'EOF'\ndocker pull alpine\nEOF",
        "printf 'apt-get upgrade\\n' > script.sh",
        "echo done # docker pull alpine",
    ),
)
def test_inert_text_is_not_classified(enabled_ctx, command):
    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, command) is None


@pytest.mark.parametrize(
    "command",
    (
        "cat <<EOF\n$(docker pull alpine)\nEOF",
        "sudo bash -c \"docker pull alpine\"",
        "bash -c 'apt-get -y upgrade'",
        "echo $(wget https://example.com/model.gguf)",
    ),
)
def test_executed_launchers_are_classified(enabled_ctx, command):
    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, command)["action"] == "block"


@pytest.mark.parametrize(
    "command",
    (
        "docker build --help",
        "apt-get --simulate upgrade",
        "zypper --dry-run update",
        "pip3 install --help",
        "git help clone",
    ),
)
def test_help_and_simulation_forms_stay_allowed(enabled_ctx, command):
    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, command) is None


def test_block_message_carries_source_boundary_language(enabled_ctx):
    ctx = enabled_ctx({"mode": "block_all"})
    directive = _decide(ctx, RECOGNIZED_HEAVY)
    assert S_D_PLAIN in directive["message"]
    assert "event_id=" in directive["message"]


def test_directive_shape_is_a_bare_block(enabled_ctx):
    ctx = enabled_ctx({"mode": "block_all"})
    directive = _decide(ctx, RECOGNIZED_HEAVY)
    assert sorted(directive) == ["action", "message"]


# --------------------------------------------------------------------------- #
# T3: error-path directions (locked §3.1 table + fallback deny table)
# --------------------------------------------------------------------------- #

_INJECTED_ERRORS = {
    "E2-classify": "_classify",
    "E3-snapshot": "_host_snapshot",
    "E7-message": "_current_profile",
}


def _expectations(mode: str, unattended: bool):
    """The §3 fallback deny table, by resolved mode and attended bucket."""
    if mode == "observe":
        return "allow"
    if mode == "block_unattended":
        return "deny" if unattended else "allow"
    if mode == "block_all":
        return "deny"
    return "deny"  # pressure_only: unknown pressure resolves to pressured


@pytest.mark.parametrize("error_label", sorted(_INJECTED_ERRORS))
@pytest.mark.parametrize("mode", ("observe", "block_unattended", "block_all", "pressure_only"))
@pytest.mark.parametrize("unattended", (False, True))
def test_gate_error_directions(enabled_ctx, monkeypatch, error_label, mode, unattended):
    """Class-B internal errors take the conservative direction, never raise."""
    if unattended:
        monkeypatch.setenv("HERMES_KANBAN_TASK", "task-7")

    if error_label == "E7-message":
        # The message path is only reached once the mode says block; pin a
        # pressuring host snapshot so that gate is open deterministically on
        # any host instead of relying on this machine's real load.
        monkeypatch.setattr(
            hxrb_runtime,
            "_host_snapshot",
            lambda: {"home_fs_disk_free_gb": 0.01, "memory_available_gb": 0.01,
                     "memory_available_percent": 0.2, "load_per_cpu": 99.0},
        )

    defect = RuntimeError("injected decision-path fault")

    def _raise(*_args, **_kwargs):
        raise defect

    monkeypatch.setattr(hxrb_runtime, _INJECTED_ERRORS[error_label], _raise)

    ctx = enabled_ctx({"mode": mode})
    directive = _decide(ctx, RECOGNIZED_HEAVY)
    expected = _expectations(mode, unattended)

    if expected == "allow":
        assert directive is None
    else:
        assert directive is not None and directive["action"] == "block"
        assert directive["message"] == resource_budget._FALLBACK_MESSAGE


def test_fallback_message_is_static_and_carries_boundary_language():
    message = resource_budget._FALLBACK_MESSAGE
    assert "It is not a sandbox or complete host-resource security boundary." in message
    assert S_D_PLAIN in message
    assert "{" not in message  # no interpolation slots: the literal is the message


def test_error_in_off_mode_cannot_widen_enforcement(make_ctx, monkeypatch):
    """With the master switch off, even a hostile classifier is never invoked."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-7")

    def boom(*_a, **_k):
        raise AssertionError("the vendored path must not run while mode is off")

    monkeypatch.setattr(hxrb_runtime, "_classify", boom)
    ctx = make_ctx({"mode": "off"})
    resource_budget.register(ctx)
    assert ctx.hook_calls == []  # nothing registered; nothing can raise


def test_resolution_fault_takes_less_enforcement_not_more(make_ctx, monkeypatch):
    """E1/E0: a throwing settings bridge degrades to observe-class, never blocks."""

    class BrokenSettings:
        profile_name = "probe"
        state = None

        def __init__(self):
            self.hook_calls = []
            self.tool_calls = []

        def get_config(self, key, default=None, **_kw):
            raise RuntimeError("bridge down")

        def register_hook(self, name, cb):
            self.hook_calls.append((name, cb))

        def register_tool(self, **kw):
            self.tool_calls.append(kw)

    ctx = BrokenSettings()
    assert rb_config.resolve_mode(ctx) == "off"
    resource_budget.register(ctx)  # off -> registers nothing, cannot crash (F2)
    assert ctx.hook_calls == []
    assert ctx.tool_calls == []

    shim = rb_config.SettingsShim(ctx)
    assert shim.get_config("mode") == "observe"  # only reachable via observe stand-in


# --------------------------------------------------------------------------- #
# hostile inputs: the hook never raises into the host
# --------------------------------------------------------------------------- #

_HOSTILE_COMMANDS = (
    "",
    "   ",
    "echo '" + "x" * 5000,
    'docker pull "unbalanced',
    "docker pull \x00 alpine",
    "$(" * 400 + " docker pull alpine",
    "; " * 2000 + "docker pull alpine",
    "sudo " * 40 + "docker pull alpine",
    "bash -c " * 40 + "'docker pull alpine'",
    "docker pull " + "a" * 120000,
    "<<<<<<<",
    "cat <<",
    "\x00\x00\x00",
    "$( echo $(';'.join(['x' * 300])) )",
)


@pytest.mark.parametrize("command", _HOSTILE_COMMANDS)
def test_hostile_commands_never_raise(enabled_ctx, command):
    ctx = enabled_ctx({"mode": "block_all"})
    outcome = _decide(ctx, command)
    assert outcome is None or isinstance(outcome, dict)


@pytest.mark.parametrize("args", (None, "a string", 123, [], {}, {"command": None}, {"command": 12345.5}, {"cmd": "docker pull alpine"}))
def test_hostile_arg_shapes_never_raise(enabled_ctx, args):
    ctx = enabled_ctx({"mode": "block_all"})
    outcome = ctx.hook()(tool_name="terminal", args=args)
    assert outcome is None or isinstance(outcome, dict)


@pytest.mark.parametrize("tool_name", (None, 0, "read_file", "web_search", "terminal ", "TERMINAL"))
def test_other_tools_pass_through(enabled_ctx, tool_name):
    ctx = enabled_ctx({"mode": "block_all"})
    assert _decide(ctx, RECOGNIZED_HEAVY, tool_name=tool_name) is None
