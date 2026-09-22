"""Configuration resolution for the Resource Budget plugin (``resource_budget.*``).

The discipline mirrors ``plugins/lane_gate/config.py``:

* **Fail-open loading.**  A broken or unreadable settings block degrades to the
  documented defaults and never crashes registration.
* **Fail-closed execution.**  Resolution cannot widen what runs.  ``mode``
  reaches a blocking value only through an explicitly recognized value, and an
  unrecognized value clamps to ``observe`` -- never to ``block_unattended``,
  ``block_all`` or ``pressure_only``.  A typo can turn recording on, never
  blocking on.

Settings live in the profile's plugin settings block and are read through the
host config bridge (``ctx.get_config``), which is the same seam the vendored
runtime uses.  There is no environment override and no second config file.

The vendored runtime keeps its own ``mode`` clamp pointing at
``block_unattended``.  That clamp is unreachable here: :class:`SettingsShim`
serves it an already-clamped, upstream-valid value on every call, and an ``off``
resolution is served as ``observe`` instead of reaching the vendored policy as
an unknown value.
"""

from __future__ import annotations

from typing import Any, Dict, Mapping

#: Inert master switch (I2).  ``off`` means the vendored runtime is never invoked.
DEFAULT_MODE = "off"

#: The clamp target for anything unrecognized: upstream's never-block mode.
CLAMP_MODE = "observe"

#: Every value this slice accepts for ``mode``, including the local-only ``off``.
MODES = ("off", "observe", "block_unattended", "block_all", "pressure_only")

#: Modes that can deny.  Used by the fail-closed fallback and by tests.
ENFORCING_MODES = ("block_unattended", "block_all", "pressure_only")

#: Modes the vendored runtime knows (``off`` is adapter-only, divergence G4).
UPSTREAM_MODES = ("observe", "block_unattended", "block_all", "pressure_only")

#: YAML 1.1 folds these scalar spellings into booleans before Python sees them.
_FALSE_WORDS = ("false", "no", "off")
_TRUE_WORDS = ("true", "yes", "on")

#: Upstream defaults, carried unchanged (``/tmp/hxrb/src/__init__.py`` DEFAULTS).
#: The only change is ``mode``, which defaults to ``off`` here (divergence G1).
DEFAULTS: Dict[str, Any] = {
    "mode": DEFAULT_MODE,
    "min_home_fs_free_gb": 20,
    "max_load_per_cpu": 1.25,
    "max_cpu_psi_avg10": 25.0,
    "min_memory_available_gb": 2.0,
    "min_memory_available_percent": 5.0,
    "max_memory_psi_avg10": 20.0,
    "max_io_psi_avg10": 25.0,
    "protect_gateway_restart": True,
    "block_git_clone": False,
}

#: The threshold/flag keys, in the upstream order (``mode`` excluded).
THRESHOLD_KEYS = (
    "min_home_fs_free_gb",
    "max_load_per_cpu",
    "max_cpu_psi_avg10",
    "min_memory_available_gb",
    "min_memory_available_percent",
    "max_memory_psi_avg10",
    "max_io_psi_avg10",
    "protect_gateway_restart",
    "block_git_clone",
)


def as_mode(value: Any) -> str:
    """Clamp one configured ``mode`` value.

    Total function: it never raises and always returns a member of :data:`MODES`.

    * missing / ``None`` / empty / ``off`` (case- and space-insensitive) -> ``off``
    * YAML folded booleans, or their scalar spellings: false/no -> ``off``,
      true/on -> ``observe`` (a boolean can never become a blocking mode)
    * an exact recognized value -> itself
    * anything else -> ``observe`` (the clamp target)
    """
    try:
        if value is None:
            return DEFAULT_MODE
        if isinstance(value, bool):
            return DEFAULT_MODE if value is False else CLAMP_MODE
        text = str(value).strip().lower()
        if not text:
            return DEFAULT_MODE
        if text in MODES:
            return text
        if text in _FALSE_WORDS:
            return DEFAULT_MODE
        if text in _TRUE_WORDS:
            return CLAMP_MODE
        return CLAMP_MODE
    except Exception:  # noqa: BLE001 - resolution is total by contract
        return DEFAULT_MODE


def read_setting(source: Any, key: str, default: Any = None) -> Any:
    """Read one setting through the host config bridge, or from a mapping.

    Never raises: a bridge that is missing, that rejects the keyword form, or
    that throws resolves to ``default``.  This is error path E1 in the locked
    error table: a settings read failure lands on the documented default.
    """
    getter = getattr(source, "get_config", None)
    if callable(getter):
        try:
            return getter(key, default=default)
        except TypeError:
            try:
                return getter(key, default)
            except Exception:  # noqa: BLE001
                return default
        except Exception:  # noqa: BLE001
            return default
    if isinstance(source, Mapping):
        try:
            return source.get(key, default)
        except Exception:  # noqa: BLE001
            return default
    return default


def resolve_mode(source: Any) -> str:
    """Resolve the clamped ``mode`` for one settings source, per call."""
    return as_mode(read_setting(source, "mode", DEFAULT_MODE))


def resolve_thresholds(source: Any) -> Dict[str, Any]:
    """Resolve the nine threshold/flag keys, falling back per key on any fault."""
    return {key: read_setting(source, key, DEFAULTS[key]) for key in THRESHOLD_KEYS}


def resolve_settings(source: Any) -> Dict[str, Any]:
    """Resolve the whole settings block: clamped ``mode`` plus raw thresholds."""
    resolved: Dict[str, Any] = {"mode": resolve_mode(source)}
    resolved.update(resolve_thresholds(source))
    return resolved


def default_threshold(key: str, fallback: Any = None) -> Any:
    """Return the documented default for one threshold/flag key."""
    return DEFAULTS.get(key, fallback)


class SettingsShim:
    """Config bridge handed to the vendored runtime.

    The vendored policy reads every setting through ``get_config`` on the object
    it is given.  This shim resolves against the live plugin settings on every
    call, so a mid-session change takes effect on the next call with no
    re-registration.

    ``mode`` is always served as an upstream-valid value: an ``off`` resolution
    is served as ``observe``.  ``off`` is adapter-only, and serving it would
    leave the vendored policy to fall back to its own ``block_unattended``
    clamp.  The adapter's wrapper never enters the vendored path in ``off``
    mode, and the local status surface reports the adapter's resolved mode, so
    ``off`` stays visible to operators without ever reaching the decision path.
    """

    __slots__ = ("_source",)

    def __init__(self, source: Any) -> None:
        self._source = source

    @property
    def source(self) -> Any:
        """The wrapped settings source (the host context object)."""
        return self._source

    def __getattr__(self, name: str) -> Any:
        """Delegate the host surfaces the vendored runtime reads directly.

        ``get_config`` and ``state`` are read off the context object, and the
        profile name is read with ``getattr(ctx, "profile_name", None)``.  The
        shim overrides only ``get_config``; everything else must reach the
        wrapped context unchanged, or telemetry and profile attribution would
        quietly disappear.
        """
        if name == "_source":
            raise AttributeError(name)
        source = object.__getattribute__(self, "_source")
        return getattr(source, name)

    def get_config(self, key: str, default: Any = None) -> Any:
        """Serve one setting; ``mode`` comes back already clamped."""
        if key == "mode":
            mode = resolve_mode(self._source)
            return CLAMP_MODE if mode == DEFAULT_MODE else mode
        fallback = DEFAULTS.get(key, default) if default is None else default
        return read_setting(self._source, key, fallback)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return f"SettingsShim({self._source!r})"

