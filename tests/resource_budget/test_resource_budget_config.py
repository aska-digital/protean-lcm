"""Configuration and activation tests for the Resource Budget slice.

Covers the inert-by-default contract (nothing configured registers nothing), the
mode clamp (an unrecognized value can turn recording on but never blocking on),
the threshold defaults carried from upstream, the immediate rollback semantics,
and per-call resolution.  All host contact is through the doubles in
``conftest.py``; no Hermes checkout is needed.
"""

from __future__ import annotations

import re

import pytest

from conftest import PLUGIN_DIR  # noqa: E402  (conftest puts the repo root on sys.path)

from plugins.resource_budget import config as rb_config  # noqa: E402
from plugins.resource_budget import hxrb_runtime  # noqa: E402

import plugins.resource_budget as resource_budget  # noqa: E402

#: Every value the slice accepts for ``mode``, including the local-only ``off``.
VALID_MODES = ("off", "observe", "block_unattended", "block_all", "pressure_only")

#: Values that must resolve to the inert default.
INERT_INPUTS = (None, "", "   ", "off", "OFF", " Off ", False, "no", "false", "no\n")

#: Values that are not recognized and must clamp to ``observe``.
UNRECOGNIZED_INPUTS = ("block_al", "enfroce", "enforce", "blokc_all", 1, 0, [], {}, object(), True, "yes", "on")

_SETTINGS_BY_MODE = {
    "off": {"mode": "off"},
    "observe": {"mode": "observe"},
    "block_unattended": {"mode": "block_unattended"},
    "block_all": {"mode": "block_all"},
    "pressure_only": {"mode": "pressure_only"},
}

_RECOGNIZED_HEAVY_COMMAND = "docker pull alpine"

#: A snapshot where every domain is over its documented default threshold.
_PRESSURED_SNAPSHOT = {
    "home_fs_disk_free_gb": 0.5,
    "load_per_cpu": 99.0,
    "cpu_psi_avg10": 99.0,
    "memory_available_gb": 0.1,
    "memory_available_percent": 0.5,
    "memory_psi_avg10": 99.0,
    "io_psi_avg10": 99.0,
}


def _register_body() -> str:
    """The source of the entry point's ``register()``, for the static proof."""
    source = (PLUGIN_DIR / "__init__.py").read_text(encoding="utf-8")
    start = source.index("def register(ctx")
    tail = source[start:]
    end = tail.index("\ndef ")
    return tail[:end]


def _headroom(settings):
    """The vendored headroom vector for one settings block, through the shim."""
    shim = rb_config.SettingsShim(settings)
    return hxrb_runtime._headroom(_PRESSURED_SNAPSHOT, hxrb_runtime._policy(shim))


# --------------------------------------------------------------------------- #
# T1 - inert by default
# --------------------------------------------------------------------------- #


def test_nothing_configured_registers_nothing(make_ctx):
    ctx = make_ctx({})
    resource_budget.register(ctx)

    assert ctx.hook_calls == []
    assert ctx.tool_calls == []
    assert ctx.skill_calls == []


@pytest.mark.parametrize("mode", INERT_INPUTS)
def test_mode_off_registers_nothing(make_ctx, mode):
    ctx = make_ctx({"mode": mode})
    assert rb_config.resolve_mode(ctx) == "off"

    resource_budget.register(ctx)

    assert ctx.hook_calls == []
    assert ctx.tool_calls == []
    assert ctx.skill_calls == []


def test_missing_settings_bridge_registers_nothing():
    class Bare:
        """A context with no config bridge at all."""

    ctx = Bare()
    resource_budget.register(ctx)
    assert rb_config.resolve_mode(ctx) == "off"


def test_off_path_returns_before_any_registration_static():
    """The ``off`` path returns before the first ``ctx.register_*`` call."""
    body = _register_body()
    guard = re.search(r"if mode == _config\.DEFAULT_MODE:\s*\n\s*return\b", body)
    assert guard is not None, "register() no longer has an explicit off guard"

    registrations = [
        match.start()
        for match in (
            re.search(r"\bregister_hook\b", body),
            re.search(r"\bregister_tool\b", body),
        )
        if match is not None
    ]
    assert registrations, "register() registers nothing at all"
    assert guard.start() < min(registrations)


# --------------------------------------------------------------------------- #
# T2 - the clamp
# --------------------------------------------------------------------------- #


def test_valid_modes_resolve_to_themselves(make_ctx):
    for mode in VALID_MODES:
        assert rb_config.as_mode(mode) == mode
        assert rb_config.resolve_mode(make_ctx({"mode": mode})) == mode


@pytest.mark.parametrize("mode", UNRECOGNIZED_INPUTS)
def test_unrecognized_mode_clamps_to_observe(make_ctx, mode):
    assert rb_config.as_mode(mode) == "observe"
    assert rb_config.resolve_mode(make_ctx({"mode": mode})) == "observe"


def test_clamped_mode_cannot_block_even_unattended(make_ctx, monkeypatch):
    """A typo can turn recording on; it can never turn blocking on."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    ctx = make_ctx({"mode": "block_al"})
    resource_budget.register(ctx)

    assert ctx.hook_names == ["pre_tool_call"]
    directive = ctx.hook()(tool_name="terminal", args={"command": _RECOGNIZED_HEAVY_COMMAND})
    assert directive is None


def test_settings_shim_serves_only_upstream_valid_modes(make_ctx):
    """Divergence G2: the vendored clamp stays unreachable defensive depth."""
    for mode in VALID_MODES + UNRECOGNIZED_INPUTS[:3]:
        shim = rb_config.SettingsShim(make_ctx({"mode": mode}))
        served = shim.get_config("mode")
        assert served in rb_config.UPSTREAM_MODES


# --------------------------------------------------------------------------- #
# thresholds and flags
# --------------------------------------------------------------------------- #


def test_threshold_defaults_are_carried_from_upstream():
    assert rb_config.DEFAULTS["mode"] == "off"
    assert rb_config.DEFAULTS["min_home_fs_free_gb"] == 20
    assert rb_config.DEFAULTS["max_load_per_cpu"] == 1.25
    assert rb_config.DEFAULTS["max_cpu_psi_avg10"] == 25.0
    assert rb_config.DEFAULTS["min_memory_available_gb"] == 2.0
    assert rb_config.DEFAULTS["min_memory_available_percent"] == 5.0
    assert rb_config.DEFAULTS["max_memory_psi_avg10"] == 20.0
    assert rb_config.DEFAULTS["max_io_psi_avg10"] == 25.0
    assert rb_config.DEFAULTS["protect_gateway_restart"] is True
    assert rb_config.DEFAULTS["block_git_clone"] is False


def test_threshold_nonfinite_and_bool_fall_back_to_defaults(make_ctx):
    ctx = make_ctx(
        {
            "mode": "pressure_only",
            "min_home_fs_free_gb": True,
            "max_load_per_cpu": float("nan"),
            "max_cpu_psi_avg10": float("inf"),
            "min_memory_available_gb": "nan",
            "min_memory_available_percent": float("-inf"),
            "max_memory_psi_avg10": {"nested": True},
            "max_io_psi_avg10": None,
            "protect_gateway_restart": "yes",
            "block_git_clone": 0,
        }
    )
    resource_budget.register(ctx)
    policy = ctx.status_json()["policy"]

    assert policy["mode"] == "pressure_only"
    assert policy["min_home_fs_free_gb"] == 20
    assert policy["max_load_per_cpu"] == 1.25
    assert policy["max_cpu_psi_avg10"] == 25.0
    assert policy["min_memory_available_gb"] == 2.0
    assert policy["min_memory_available_percent"] == 5.0
    assert policy["max_memory_psi_avg10"] == 20.0
    assert policy["max_io_psi_avg10"] == 25.0
    assert policy["protect_gateway_restart"] is True
    assert policy["block_git_clone"] is False


def test_finite_thresholds_pass_through_unchanged(make_ctx):
    ctx = make_ctx({"mode": "pressure_only", "max_load_per_cpu": 4.5, "min_home_fs_free_gb": 7})
    resource_budget.register(ctx)
    policy = ctx.status_json()["policy"]

    assert policy["max_load_per_cpu"] == 4.5
    assert policy["min_home_fs_free_gb"] == 7


def test_zero_threshold_disables_threshold(make_ctx):
    disabled = make_ctx(
        {
            "mode": "pressure_only",
            "min_home_fs_free_gb": 0,
            "max_load_per_cpu": 0,
            "max_cpu_psi_avg10": 0,
            "min_memory_available_gb": 0,
            "min_memory_available_percent": 0,
            "max_memory_psi_avg10": 0,
            "max_io_psi_avg10": 0,
        }
    )
    default = make_ctx({"mode": "pressure_only"})

    assert _headroom(disabled)["pressured"] is False
    assert _headroom(disabled)["reasons"] == []

    pressured = _headroom(default)
    assert pressured["pressured"] is True
    assert set(pressured["domains"]) == {"cpu", "memory", "io", "disk"}
    assert all(domain["pressured"] for domain in pressured["domains"].values())


# --------------------------------------------------------------------------- #
# rollback and liveness
# --------------------------------------------------------------------------- #


def test_mode_flip_to_off_is_immediately_inert(enabled_ctx, monkeypatch):
    """T9: the very next call after ``mode: off`` is a no-op."""
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    ctx = enabled_ctx()
    hook = ctx.hook()

    blocked = hook(tool_name="terminal", args={"command": _RECOGNIZED_HEAVY_COMMAND})
    assert blocked is not None and blocked["action"] == "block"

    ctx.settings["mode"] = "off"
    assert hook(tool_name="terminal", args={"command": _RECOGNIZED_HEAVY_COMMAND}) is None
    assert ctx.status_json()["policy"]["mode"] == "off"


def test_resolution_is_per_call_without_reregistration(make_ctx, monkeypatch):
    monkeypatch.setenv("HERMES_KANBAN_TASK", "task-1")
    ctx = make_ctx({"mode": "observe"})
    resource_budget.register(ctx)
    hook = ctx.hook()
    assert len(ctx.hook_calls) == 1

    assert hook(tool_name="terminal", args={"command": _RECOGNIZED_HEAVY_COMMAND}) is None

    ctx.settings["mode"] = "block_all"
    assert hook(tool_name="terminal", args={"command": _RECOGNIZED_HEAVY_COMMAND}) is not None

    ctx.settings["mode"] = "observe"
    assert hook(tool_name="terminal", args={"command": _RECOGNIZED_HEAVY_COMMAND}) is None

    assert len(ctx.hook_calls) == 1
    assert len(ctx.tool_calls) == 1


def test_settings_bridge_failure_falls_back_to_defaults():
    """E1: a bridge that throws resolves to the documented default per key."""

    class BrokenBridge:
        profile_name = "probe"

        def get_config(self, key, default=None, **_kwargs):
            raise RuntimeError("settings unavailable")

    source = BrokenBridge()
    assert rb_config.resolve_mode(source) == "off"
    assert rb_config.resolve_thresholds(source) == dict(
        (key, rb_config.DEFAULTS[key]) for key in rb_config.THRESHOLD_KEYS
    )


def test_settings_bridge_without_keyword_default_is_tolerated():
    """Older bridges that only accept a positional default still resolve."""

    class PositionalBridge:
        profile_name = "probe"
        settings = {"mode": "observe", "max_load_per_cpu": 3.0}

        def get_config(self, key, default):
            return self.settings.get(key, default)

    source = PositionalBridge()
    assert rb_config.resolve_mode(source) == "observe"
    assert rb_config.read_setting(source, "max_load_per_cpu", 1.25) == 3.0
