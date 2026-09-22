"""Configuration resolution for the Lane Gate plugin (``lane_gate.*``).

Mirrors the ``plugins/context_engine/lcm/config.py`` discipline:

* **Fail-open loading.**  A broken ``lane_gate`` block degrades to defaults and
  never crashes registration.
* **Fail-closed execution.**  Resolution cannot widen what runs: ``mode`` reaches
  ``enforce`` only through an explicit value, and an unrecognized value clamps to
  ``advisory`` -- never to ``enforce`` (locked contract §4).

Sources, highest priority first:

1. environment (per-session, set by ops at worker spawn): ``PROTEAN_LANE_MANIFEST``,
   ``PROTEAN_LANE_RECEIPTS``, ``PROTEAN_LANE_RECEIPT_DETAIL``
2. the ``lane_gate`` block in ``config.yaml``
3. defaults (``mode: off``: the hook is not registered at all)
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from . import receipts as _receipts
from .gate import MODES

ENV_MANIFEST = "PROTEAN_LANE_MANIFEST"
ENV_RECEIPTS = "PROTEAN_LANE_RECEIPTS"
ENV_DETAIL = "PROTEAN_LANE_RECEIPT_DETAIL"

DEFAULT_MODE = "off"
DEFAULT_DETAIL = "hash"

DEFAULTS: Dict[str, Any] = {
    "mode": DEFAULT_MODE,
    "manifest": "",
    "receipts": "",
    "receipt_detail": DEFAULT_DETAIL,
}

_KNOWN_KEYS = frozenset(DEFAULTS)


def _as_mode(value: Any) -> str:
    """Clamp a configured mode.

    Missing/empty/``off`` -> ``off`` (inert default, I2).  Recognized value ->
    itself.  Any other value -> ``advisory``: a typo can turn recording on, never
    enforcement.

    YAML 1.1 folds the bare words ``off``/``no``/``false`` into the boolean
    ``False`` before this function ever sees them, so ``False`` must resolve to
    ``off`` -- otherwise a user who writes ``mode: off`` gets an advisory gate
    (a registered hook and a receipt ledger) instead of no gate at all.
    """
    if value is None:
        return DEFAULT_MODE
    if isinstance(value, bool):
        # ``off``/``no``/``false`` -> off; ``true``/``on`` -> advisory, never enforce.
        return DEFAULT_MODE if value is False else "advisory"
    text = str(value).strip().lower()
    if not text:
        return DEFAULT_MODE
    return text if text in MODES else "advisory"


def _as_detail(value: Any) -> str:
    text = str(value).strip().lower() if value is not None else ""
    return text if text in _receipts.DETAIL_MODES else DEFAULT_DETAIL


def _as_path(value: Any) -> str:
    """A path setting: strings and path-likes only, anything else is "unset"."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, os.PathLike):
        return os.fspath(value).strip()
    return ""


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


class LaneGateConfig:
    """Resolved settings for one gate instance."""

    __slots__ = tuple(DEFAULTS) + ("extra",)

    def __init__(self, values: Optional[Dict[str, Any]] = None) -> None:
        raw: Dict[str, Any] = dict(DEFAULTS)
        extra: Dict[str, Any] = {}
        if isinstance(values, dict):
            for key, value in values.items():
                if key in _KNOWN_KEYS:
                    raw[key] = value
                else:
                    extra[key] = value

        # Environment wins over config for the manifest/ledger binding: ops sets
        # these per session at worker spawn.
        manifest_env = _env(ENV_MANIFEST)
        receipts_env = _env(ENV_RECEIPTS)
        detail_env = _env(ENV_DETAIL)

        self.mode = _as_mode(raw.get("mode"))
        self.manifest = manifest_env or _as_path(raw.get("manifest"))
        self.receipts = receipts_env or _as_path(raw.get("receipts"))
        self.receipt_detail = _as_detail(detail_env) if detail_env else _as_detail(raw.get("receipt_detail"))
        self.extra = extra

    @property
    def hook_active(self) -> bool:
        """Whether the ``pre_tool_call`` hook should be registered at all."""
        return self.mode != "off"

    def receipt_path(self) -> str:
        """The resolved ledger path."""
        return str(_receipts.receipt_path(self.receipts or None))

    def as_dict(self) -> Dict[str, Any]:
        data = {key: getattr(self, key) for key in DEFAULTS}
        data["extra"] = dict(self.extra)
        return data

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"LaneGateConfig({self.as_dict()!r})"


def load_lane_gate_config(config: Optional[Dict[str, Any]] = None) -> LaneGateConfig:
    """Resolve the ``lane_gate`` block, falling back to defaults on any failure.

    ``config`` may be passed explicitly (tests).  Otherwise the loaded Hermes
    config is used and any failure to read it yields pure defaults.
    """
    block: Any = None
    if config is None:
        try:
            from hermes_cli.config import load_config

            config = load_config()
        except Exception:
            config = None
    if isinstance(config, dict):
        block = config.get("lane_gate")
    return LaneGateConfig(block if isinstance(block, dict) else {})
