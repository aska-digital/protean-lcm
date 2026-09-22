"""Resource Budget: an opt-in terminal policy ingredient for the LCM surface.

Layout

    plugins/resource_budget/
    ├── __init__.py           entry point: ``register(ctx)``; inert-by-default
    │                         activation, per-call resolution, fail-closed wrapper
    ├── config.py             ``resource_budget.*`` resolution (mode clamp, config shim)
    ├── hxrb_runtime.py       vendored upstream runtime, byte-identical to the pin
    ├── UPSTREAM-LICENSE.md   vendored upstream MIT notice
    └── plugin.yaml           manifest metadata (provides_hooks: pre_tool_call)

Inert by default.  With the ``mode`` setting unset or ``off``, ``register()``
returns before any ``ctx.register_*`` call: no ``pre_tool_call`` hook, no
``resource_budget_status`` tool, no behavior change (invariant I2).  ``observe``
records and never denies; the blocking modes deny only what the vendored
classifier recognizes, and the wrapper resolves the mode again on every call, so
flipping to ``off`` makes the very next call a no-op.

The wrapper is fail-closed in the direction that matters: an internal error in
the decision path can never produce an allow where the error-free path with the
same policy and input produces a deny.  It falls back to the conservative
closure of the mode predicate and never raises into the host.

Rollback is ``hermes plugins disable protean-resource-budget``: the host drops
the two callbacks and the tool, stock behavior returns, and nothing in Hermes
core or its config schema was ever touched.  Setting ``mode: "off"`` is the
immediate per-call rollback.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from . import config as _config
from . import hxrb_runtime as _runtime_module

logger = logging.getLogger(__name__)

PLUGIN_NAME = "resource-budget"
HOOK_NAME = "pre_tool_call"
STATUS_TOOL_NAME = "resource_budget_status"
STATUS_TOOLSET = "resource_budget"

#: Marker set on our callback so re-registration can be detected.
_HOOK_MARKER = "_resource_budget_hook"

#: Static deny literal for the fail-closed fallback.
#:
#: It carries no interpolation and no command content: the fallback runs when
#: the normal message path is the thing that failed.  The boundary sentence and
#: the categorical-policy sentence are the vendored source's own wording (its
#: README Boundary block and its block message), with the document's markdown
#: emphasis markers removed because this is a runtime message, not a document.
_FALLBACK_MESSAGE = (
    "Hermes Resource Budget could not complete its policy evaluation for a "
    "recognized terminal operation, so the conservative outcome was applied. "
    "It is not a sandbox or complete host-resource security boundary. "
    "This is a categorical best-effort terminal policy rule, not a measured "
    "transfer/runtime threshold or host sandbox. No command contents or "
    "credentials were persisted. Run the operation from an operator-controlled "
    "foreground workflow, or deliberately change this profile's plugin mode if "
    "that is intended."
)

#: Module-global state, refreshed by ``register()`` and read on every call.
#: Mirrors the lane-gate entry point: the callback reads the settings holder
#: rather than capturing a snapshot, so a re-enable with a different mode takes
#: effect without registering a second callback.
_STATE: Dict[str, Any] = {"ctx": None}
_HOOK = None
_STATUS_HANDLER = None


# --------------------------------------------------------------------------- #
# fail-closed fallback
# --------------------------------------------------------------------------- #


def _deny_directive() -> Dict[str, Any]:
    return {"action": "block", "message": _FALLBACK_MESSAGE}


def _unattended_bucket() -> bool:
    """Read the unattended marker through the vendored reader.

    Unreadable resolves to unattended: the conservative closure, matching the
    fallback table where an unknown signal can only add enforcement.
    """
    try:
        return bool(_runtime_module._is_unattended())
    except Exception:  # noqa: BLE001
        return True


def _fallback_directive(mode: str) -> Dict[str, Any] | None:
    """The conservative closure of the mode predicate for an internal error.

    Unknown classification resolves to "recognized" and unknown pressure
    resolves to "pressured", so the executing set under an internal error is a
    subset of the executing set without one.

    ``off`` is unreachable here: the wrapper returns before entering the
    decision path.  ``observe`` can never deny by contract.
    """
    if mode in (_config.DEFAULT_MODE, "observe"):
        return None
    if mode == "block_unattended":
        return _deny_directive() if _unattended_bucket() else None
    # block_all, pressure_only, and any value outside the closed mode set.
    return _deny_directive()


# --------------------------------------------------------------------------- #
# callbacks
# --------------------------------------------------------------------------- #


def _build_hook():
    """Build the ``pre_tool_call`` callback.

    Resolution happens per call, and the vendored decision path runs inside one
    ``try``: whatever it raises becomes the fallback directive, so the callback
    never raises into the host.
    """

    def _hook(tool_name: str = "", args: Any = None, **kwargs: Any):
        ctx = _STATE.get("ctx")
        mode = _config.resolve_mode(ctx)
        if mode == _config.DEFAULT_MODE:
            return None
        runtime = _runtime_module._Runtime(_config.SettingsShim(ctx))
        try:
            return runtime.pre_tool_call(tool_name=tool_name, args=args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - the hook must never raise into the host
            logger.warning(
                "resource-budget: gate error on %r (%s); applying the conservative outcome",
                tool_name,
                exc,
            )
            return _fallback_directive(mode)

    setattr(_hook, _HOOK_MARKER, True)
    return _hook


def _make_status_handler():
    """Build the read-only ``resource_budget_status`` handler.

    It returns the vendored payload with one correction: the status surface
    reports the adapter's resolved ``mode``, so an ``off`` resolution is visible
    to operators instead of the shim's upstream-valid ``observe`` stand-in.
    """

    def _status(params: Any = None, **kwargs: Any) -> str:
        ctx = _STATE.get("ctx")
        runtime = _runtime_module._Runtime(_config.SettingsShim(ctx))
        payload = _runtime_module.json.loads(runtime.status(params, **kwargs))
        policy = payload.get("policy")
        if isinstance(policy, dict):
            policy["mode"] = _config.resolve_mode(ctx)
        return _runtime_module.json.dumps(payload, indent=2, sort_keys=True, default=str)

    return _status


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


def _registered_entries(ctx: Any, registry_names, name: str) -> tuple:
    """Best-effort view of what a context already holds under ``name``."""
    found: list = []
    for attribute in registry_names:
        holder = getattr(ctx, attribute, None)
        if not isinstance(holder, dict):
            continue
        existing = holder.get(name)
        if existing is None:
            continue
        found.extend(list(existing) if isinstance(existing, (list, tuple)) else [existing])
    return tuple(found)


def _already_registered(ctx: Any, registry_names, name: str, callback: Any) -> bool:
    """Whether this callback (or an equal entry) is already registered.

    An unreadable registry yields ``False``: a duplicate registration is a
    nuisance, a silently missing gate is worse.
    """
    for existing in _registered_entries(ctx, registry_names, name):
        if existing is callback:
            return True
        if getattr(existing, _HOOK_MARKER, False) is True:
            return True
        if isinstance(existing, dict) and (
            existing.get("handler") is callback or existing.get("name") == name
        ):
            return True
    return False


def register(ctx: Any) -> None:
    """Register the slice with the host.  Feature-detected and idempotent.

    In ``off`` mode this returns immediately: nothing is registered, nothing is
    recorded, nothing changes (I2).
    """
    global _HOOK, _STATUS_HANDLER
    _STATE["ctx"] = ctx

    mode = _config.resolve_mode(ctx)
    if mode == _config.DEFAULT_MODE:
        return

    callback = _HOOK if _HOOK is not None else _build_hook()
    _HOOK = callback
    register_hook = getattr(ctx, "register_hook", None)
    if callable(register_hook):
        if _already_registered(ctx, ("_hooks", "hooks"), HOOK_NAME, callback):
            logger.debug("resource-budget: pre_tool_call hook already registered; reusing it")
        else:
            register_hook(HOOK_NAME, callback)
    else:
        # Thin-registration limitation, recorded rather than worked around: the
        # package stays importable, but no call is gated on this host.
        logger.warning(
            "resource-budget: context object has no register_hook(); the pre_tool_call "
            "gate is inactive (mode=%s).",
            mode,
        )

    handler = _STATUS_HANDLER if _STATUS_HANDLER is not None else _make_status_handler()
    _STATUS_HANDLER = handler
    register_tool = getattr(ctx, "register_tool", None)
    if callable(register_tool):
        if _already_registered(ctx, ("_tools", "tools"), STATUS_TOOL_NAME, handler):
            logger.debug("resource-budget: status tool already registered; reusing it")
        else:
            register_tool(
                name=STATUS_TOOL_NAME,
                toolset=STATUS_TOOLSET,
                schema=_runtime_module._STATUS_SCHEMA,
                handler=handler,
                description=_runtime_module._STATUS_SCHEMA["description"],
                emoji="\U0001f9ef",
            )


def _reset_for_tests() -> None:
    """Drop the process-global callback state (tests only)."""
    global _HOOK, _STATUS_HANDLER
    _HOOK = None
    _STATUS_HANDLER = None
    _STATE["ctx"] = None
