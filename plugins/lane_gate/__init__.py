"""Lane Gate: opt-in per-worker lane enforcement at ``pre_tool_call``.

Layout

    plugins/lane_gate/
    ├── __init__.py     entry point: ``register(ctx)``; feature-detected hook registration
    ├── plugin.yaml     manifest metadata (provides_hooks: pre_tool_call)
    ├── manifest.py     ``protean/lane-manifest/v1``: closed schema, hash binding, glob match
    ├── gate.py         ``protean/lane-gate/v1``: the decision procedure
    ├── receipts.py     ``protean/lane-receipt/v1``: append-only JSONL ledger
    ├── config.py       ``lane_gate.*`` resolution (fail-open load, fail-closed execution)
    └── skills/lane-discipline/  the worker-facing skill

Inert by default.  With ``lane_gate.mode`` unset the hook is not registered: no
gate, no receipts, no behavior change (invariant I2).  ``advisory`` records every
decision and lets every call through; ``enforce`` denies everything that is not a
recorded ALLOW.

Rollback is ``hermes plugins disable protean-lane-gate``: the callbacks are
removed by the host, the stock behaviour returns, and nothing in Hermes core or
its config schema was ever touched (invariant I8).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, Optional

from . import config as _config
from . import gate as _gate
from . import manifest as _manifest
from . import receipts as _receipts

__all__ = [
    "PLUGIN_NAME",
    "HOOK_NAME",
    "build_pre_tool_call_hook",
    "register",
]

logger = logging.getLogger(__name__)

PLUGIN_NAME = "lane-gate"
HOOK_NAME = "pre_tool_call"

#: Marker set on our callback so re-registration can be detected.
_HOOK_MARKER = "_lane_gate_hook"

_SKILL_PATH = Path(__file__).resolve().parent / "skills" / "lane-discipline" / "SKILL.md"

_SKILL_DESCRIPTION = (
    "Lane discipline for workers under a Lane Gate manifest: what the lane means, "
    "what denial looks like, and how to cite lane receipts."
)


# --------------------------------------------------------------------------- #
# hook construction
# --------------------------------------------------------------------------- #


def _settings_snapshot(settings: _config.LaneGateConfig) -> Dict[str, str]:
    """Immutable settings view for one hook invocation."""
    return {
        "mode": settings.mode,
        "manifest": settings.manifest,
        "receipts": settings.receipts,
        "receipt_detail": settings.receipt_detail,
    }


def build_pre_tool_call_hook(state: Dict[str, Dict[str, str]]):
    """Build the ``pre_tool_call`` callback over a mutable settings holder.

    ``state`` is a dict with a ``settings`` key; ``register()`` refreshes that
    entry on every call, so a re-enable with a different mode takes effect without
    re-registering a second callback.  The callback never raises: any failure
    inside it is a denial under ``enforce`` and a no-op under ``advisory``.
    """
    resolver = _manifest.default_resolver()

    def _hook(**kwargs: Any) -> Optional[Dict[str, Any]]:
        settings = state.get("settings") or {}
        mode = str(settings.get("mode") or _config.DEFAULT_MODE)
        if mode == "off":
            return None
        try:
            resolver.set_path(settings.get("manifest") or "")
            tool_name = str(kwargs.get("tool_name") or "")
            args = kwargs.get("args")
            binding = resolver.binding()
            outcome = _gate.evaluate_tool_call(
                tool=tool_name,
                args=args,
                mode=mode,
                manifest=binding,
                session_id=str(kwargs.get("session_id") or ""),
                request_id=kwargs.get("tool_call_id") or None,
                receipt_detail=str(settings.get("receipt_detail") or _config.DEFAULT_DETAIL),
                receipts_path=settings.get("receipts") or None,
            )
            if not outcome.receipt_written and outcome.record.get("mode") == "enforce":
                logger.warning(
                    "lane-gate: receipt ledger unavailable (%s); failing closed for %s",
                    outcome.receipt_error or "unknown error",
                    tool_name,
                )
            return outcome.directive
        except Exception as exc:  # noqa: BLE001 - the hook must never raise into the host
            logger.warning("lane-gate: gate error on %r (%s)", kwargs.get("tool_name"), exc)
            if mode == "enforce":
                # Uncertainty never executes: deny with the recorded reason.
                record = {
                    "contract": _gate.CONTRACT,
                    "decision": "APPROVAL",
                    "reason": "gate_error",
                    "manifest_sha256": _manifest.ABSENT_SHA256,
                    "mode": "enforce",
                }
                return {"action": "block", "message": _gate.block_message(record)}
            return None

    setattr(_hook, _HOOK_MARKER, True)
    return _hook


def _registered_callbacks(ctx: Any) -> tuple:
    """Best-effort view of the ``pre_tool_call`` callbacks already registered.

    Consults the host's plugin manager when it is importable, then any hook
    registry the context object exposes (the shape tests use).  An unreadable
    registry yields ``()``: a duplicate registration is a nuisance, a broken
    re-enable is worse, so ambiguity resolves towards registering.
    """
    found: list = []
    try:
        from hermes_cli.plugins import get_plugin_manager

        manager = get_plugin_manager()
        hooks = getattr(manager, "_hooks", None)
        if isinstance(hooks, dict):
            found.extend(list(hooks.get(HOOK_NAME) or []))
    except Exception:
        pass
    for attribute in ("_hooks", "hooks"):
        holder = getattr(ctx, attribute, None)
        if isinstance(holder, dict):
            found.extend(list(holder.get(HOOK_NAME) or []))
    return tuple(found)


def _already_registered(ctx: Any, callback: Any) -> bool:
    return any(existing is callback or getattr(existing, _HOOK_MARKER, False) is True
               for existing in _registered_callbacks(ctx))


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #

_STATE: Dict[str, Dict[str, str]] = {"settings": {}}
_HOOK = None


def register(ctx: Any) -> None:
    """Register the gate with the host.  Feature-detected and idempotent.

    In ``off`` mode this returns immediately: nothing is registered, nothing is
    recorded, nothing changes (I2).
    """
    global _HOOK
    settings = _config.load_lane_gate_config()
    _STATE["settings"] = _settings_snapshot(settings)

    if not settings.hook_active:
        return

    register_hook = getattr(ctx, "register_hook", None)
    if not callable(register_hook):
        # Thin-registration limitation, recorded rather than worked around: the
        # package stays importable and the pure gate/receipt/manifest behaviour
        # is unaffected, but no call is gated on this host.
        logger.warning(
            "lane-gate: context object has no register_hook(); the pre_tool_call gate is inactive "
            "(lane_gate.mode=%s).",
            settings.mode,
        )
    else:
        callback = _HOOK if _HOOK is not None else build_pre_tool_call_hook(_STATE)
        _HOOK = callback
        if _already_registered(ctx, callback):
            logger.debug("lane-gate: pre_tool_call hook already registered; reusing it")
        else:
            register_hook(HOOK_NAME, callback)

    register_skill = getattr(ctx, "register_skill", None)
    if callable(register_skill) and _SKILL_PATH.exists():
        try:
            register_skill("lane-discipline", _SKILL_PATH, description=_SKILL_DESCRIPTION)
        except Exception as exc:  # noqa: BLE001 - a skill that will not register must not break the gate
            logger.warning("lane-gate: skill registration failed (%s)", exc)


def _reset_for_tests() -> None:
    """Drop the process-global hook/settings state (tests only)."""
    global _HOOK
    _HOOK = None
    _STATE["settings"] = {}
    _manifest.reset_default_resolver()
