"""Lane manifest: load, closed-schema validation, hash binding, path matching.

Implements ``protean/lane-manifest/v1`` -- the per-worker lane manifest of
Lane Gate v1 (locked architecture, decision D3).  This module *implements* the
locked contract; it does not extend it.

Contract, as locked:

* **Closed schema.**  Every key is REQUIRED and unknown keys FAIL.  A typo is a
  failure, not a shrug (the artifact-contract rule-8 discipline).
* **Binding.**  ``manifest_sha256`` is sha256 over the canonical JSON bytes of
  the parsed manifest (sorted keys, no whitespace).  The resolver loads once and
  re-checks per call with one ``stat`` plus a conditional hash, so an edit after
  load is detectable (``tampered``).
* **Matching.**  ``fnmatch`` over normalized absolute paths.  ``fnmatch`` is not
  path-aware: ``*`` spans ``/``.  Symlink-escape analysis is out of scope for
  slice one (residual risk G7) -- paths are normalized, never realpath-resolved.

Provenance: the closed-schema and path-binding ideas are adapted from
``keeltrace/hermes-nerve`` @ ``de219b1875a9943cb81de406c6853caf53496aaf``
(MIT, ``Copyright (c) 2026 Nerve contributors``).  See PROVENANCE-LANE-GATE.md.
No upstream source file is copied.
"""

from __future__ import annotations

import fnmatch
import hashlib
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SCHEMA = "protean/lane-manifest/v1"

#: Every key below is required; anything else fails validation.
REQUIRED_KEYS: Tuple[str, ...] = (
    "schema",
    "lane_id",
    "owned_write_globs",
    "forbidden_globs",
    "forbidden_tools",
    "external_writes",
)

#: Argument names that carry a filesystem path in a tool call.
PATH_ARG_KEYS = frozenset(
    {
        "path",
        "paths",
        "file",
        "files",
        "file_path",
        "filepath",
        "filename",
        "workdir",
        "cwd",
        "dir",
        "directory",
        "target",
        "target_path",
        "dest",
        "destination",
        "dest_path",
        "root",
        "root_path",
        "repo",
        "repo_path",
        "project_path",
        "output_path",
        "out_path",
        "source_path",
        "src_path",
        "new_path",
        "old_path",
    }
)

#: Argument names that carry a URL; only the URL's path component is matched.
URL_ARG_KEYS = frozenset({"url", "urls", "uri", "link", "target_url", "source_url"})

#: ``sha256(b"")``.  Sentinel digest for "no manifest bound": canonical JSON of a
#: valid manifest is never empty, so a real manifest can never hash to this.
ABSENT_SHA256 = hashlib.sha256(b"").hexdigest()

_MAX_EXTRACT_DEPTH = 3


# --------------------------------------------------------------------------- #
# canonical JSON + hashing
# --------------------------------------------------------------------------- #


def canonical_json_bytes(value: Any) -> bytes:
    """Canonical JSON bytes: sorted keys, no whitespace, ASCII-escaped UTF-8."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def sha256_hex(data: Any) -> str:
    """``sha256`` hex digest of *data* (bytes/str hashed directly, anything else as canonical JSON)."""
    if isinstance(data, bytes):
        raw = data
    elif isinstance(data, str):
        raw = data.encode("utf-8")
    else:
        raw = canonical_json_bytes(data)
    return hashlib.sha256(raw).hexdigest()


# --------------------------------------------------------------------------- #
# closed-schema validation
# --------------------------------------------------------------------------- #


class ManifestInvalid(Exception):
    """The manifest is missing, unreadable, or fails the closed schema."""

    reason = "manifest_invalid"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = str(detail)


def _require_str(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestInvalid(f"{key} must be a non-empty string")
    return value.strip()


def _require_str_list(data: Mapping[str, Any], key: str) -> Tuple[str, ...]:
    value = data.get(key)
    if not isinstance(value, (list, tuple)):
        raise ManifestInvalid(f"{key} must be a list of strings")
    out: List[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ManifestInvalid(f"{key}[{index}] must be a non-empty string")
        out.append(item.strip())
    return tuple(out)


def validate_manifest(data: Any) -> Dict[str, Any]:
    """Validate a parsed manifest against the closed schema; return a normalized dict.

    Raises :class:`ManifestInvalid` on a missing key, an unknown key, an empty
    ``lane_id``, a wrong ``schema``, or a wrong value type.
    """
    if not isinstance(data, Mapping):
        raise ManifestInvalid("manifest must be a JSON object")

    keys = set(data)
    missing = sorted(key for key in REQUIRED_KEYS if key not in keys)
    if missing:
        raise ManifestInvalid("missing required key(s): " + ", ".join(missing))
    unknown = sorted(key for key in keys if key not in REQUIRED_KEYS)
    if unknown:
        raise ManifestInvalid("unknown key(s): " + ", ".join(unknown))

    schema = _require_str(data, "schema")
    if schema != SCHEMA:
        raise ManifestInvalid(f"schema must be {SCHEMA!r}, got {schema!r}")

    external_writes = data.get("external_writes")
    if not isinstance(external_writes, bool):
        raise ManifestInvalid("external_writes must be a JSON boolean")

    return {
        "schema": SCHEMA,
        "lane_id": _require_str(data, "lane_id"),
        "owned_write_globs": _require_str_list(data, "owned_write_globs"),
        "forbidden_globs": _require_str_list(data, "forbidden_globs"),
        "forbidden_tools": _require_str_list(data, "forbidden_tools"),
        "external_writes": external_writes,
    }


# --------------------------------------------------------------------------- #
# manifest value object
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LaneManifest:
    """A validated manifest plus its content-bound digest."""

    schema: str
    lane_id: str
    owned_write_globs: Tuple[str, ...]
    forbidden_globs: Tuple[str, ...]
    forbidden_tools: Tuple[str, ...]
    external_writes: bool
    sha256: str
    source: str = ""

    @classmethod
    def from_obj(cls, data: Any, *, source: str = "", sha256: Optional[str] = None) -> "LaneManifest":
        normalized = validate_manifest(data)
        digest = sha256 if isinstance(sha256, str) and sha256 else sha256_hex(normalized)
        return cls(
            schema=normalized["schema"],
            lane_id=normalized["lane_id"],
            owned_write_globs=normalized["owned_write_globs"],
            forbidden_globs=normalized["forbidden_globs"],
            forbidden_tools=normalized["forbidden_tools"],
            external_writes=normalized["external_writes"],
            sha256=digest,
            source=source,
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "lane_id": self.lane_id,
            "owned_write_globs": list(self.owned_write_globs),
            "forbidden_globs": list(self.forbidden_globs),
            "forbidden_tools": list(self.forbidden_tools),
            "external_writes": self.external_writes,
        }


def manifest_from_bytes(raw: bytes, *, source: str = "") -> LaneManifest:
    """Parse and validate manifest bytes; raise :class:`ManifestInvalid` on any defect."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError:
        raise ManifestInvalid("manifest is not valid UTF-8") from None
    except json.JSONDecodeError as exc:
        raise ManifestInvalid(f"manifest is not valid JSON: {exc.msg}") from None
    return LaneManifest.from_obj(parsed, source=source)


def load_manifest_file(path: Any) -> LaneManifest:
    """Read, parse and validate a manifest file."""
    target = Path(str(path)).expanduser()
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ManifestInvalid(f"manifest unreadable: {exc.__class__.__name__}") from None
    return manifest_from_bytes(raw, source=str(target))


# --------------------------------------------------------------------------- #
# binding (file -> validated manifest, with tamper detection)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ManifestBinding:
    """The manifest in force for a call: ``ok``, ``missing``, ``invalid`` or ``tampered``."""

    status: str
    sha256: str
    manifest: Optional[LaneManifest] = None
    detail: str = ""

    @property
    def ok(self) -> bool:
        return self.status == "ok" and self.manifest is not None

    def bound(self) -> LaneManifest:
        """Return the bound manifest, or raise :class:`ManifestInvalid`."""
        if not self.ok or self.manifest is None:
            raise ManifestInvalid(self.detail or f"manifest {self.status}")
        return self.manifest

    @classmethod
    def absent(cls, detail: str = "no manifest bound") -> "ManifestBinding":
        return cls(status="missing", sha256=ABSENT_SHA256, manifest=None, detail=detail)


class ManifestResolver:
    """Load a manifest once per session and re-check it per call.

    Cost per call is one ``stat``; a changed ``(mtime_ns, size)`` triggers the
    hash re-check.  A semantic change after load yields ``tampered``.
    """

    def __init__(self, path: Any = None) -> None:
        self._explicit_path = self._normalize_path_arg(path)
        self._cache_key: Optional[Tuple[str, int, int]] = None
        self._cache: Optional[ManifestBinding] = None
        self._bound: Optional[LaneManifest] = None

    @staticmethod
    def _normalize_path_arg(path: Any) -> str:
        if path is None:
            return ""
        text = str(path).strip()
        return text

    def set_path(self, path: Any) -> None:
        """Rebind the resolver to a new manifest path (clears the cache)."""
        self._explicit_path = self._normalize_path_arg(path)
        self.reset()

    def target(self) -> str:
        """The manifest path this resolver watches (``PROTEAN_LANE_MANIFEST`` or an explicit path)."""
        if self._explicit_path:
            return str(Path(self._explicit_path).expanduser())
        env_path = str(os.environ.get("PROTEAN_LANE_MANIFEST") or "").strip()
        return str(Path(env_path).expanduser()) if env_path else ""

    def reset(self) -> None:
        """Forget the cached manifest (used on disable and by tests)."""
        self._cache_key = None
        self._cache = None
        self._bound = None

    def binding(self, path: Any = None) -> ManifestBinding:
        """Resolve the current binding, re-checking the file for tampering."""
        if path is not None:
            self.set_path(path)
        target = self.target()
        if not target:
            return ManifestBinding.absent()

        try:
            stat = os.stat(target)
        except OSError as exc:
            self.reset()
            return ManifestBinding.absent(f"manifest unreadable: {exc.__class__.__name__}")

        key = (target, stat.st_mtime_ns, stat.st_size)
        if self._cache is not None and self._cache_key == key:
            return self._cache

        try:
            raw = Path(target).read_bytes()
        except OSError as exc:
            self.reset()
            return ManifestBinding.absent(f"manifest unreadable: {exc.__class__.__name__}")

        try:
            manifest = manifest_from_bytes(raw, source=target)
        except ManifestInvalid as exc:
            binding = ManifestBinding(status="invalid", sha256=sha256_hex(raw), detail=exc.detail)
            self._cache_key, self._cache = key, binding
            return binding

        if self._bound is not None and self._bound.sha256 != manifest.sha256:
            binding = ManifestBinding(
                status="tampered",
                sha256=manifest.sha256,
                manifest=self._bound,
                detail="manifest changed after load (hash mismatch)",
            )
        else:
            self._bound = manifest
            binding = ManifestBinding(status="ok", sha256=manifest.sha256, manifest=manifest)

        self._cache_key, self._cache = key, binding
        return binding


_DEFAULT_RESOLVER: Optional[ManifestResolver] = None


def default_resolver() -> ManifestResolver:
    """Process-wide resolver used by the registered hook."""
    global _DEFAULT_RESOLVER
    if _DEFAULT_RESOLVER is None:
        _DEFAULT_RESOLVER = ManifestResolver()
    return _DEFAULT_RESOLVER


def reset_default_resolver() -> None:
    """Drop the process-wide resolver (plugin disable, tests)."""
    global _DEFAULT_RESOLVER
    _DEFAULT_RESOLVER = None


# --------------------------------------------------------------------------- #
# path extraction and glob matching
# --------------------------------------------------------------------------- #


def normalize_path(raw: Any, *, base: Optional[str] = None) -> str:
    """Normalize one path argument to an absolute, ``normpath``-ed string.

    Relative paths are resolved against *base* (the call's working directory,
    default the current directory).  No symlink resolution: G7 is out of scope.
    """
    text = str(raw).strip()
    if not text:
        return ""
    expanded = os.path.expanduser(text)
    if not os.path.isabs(expanded):
        expanded = os.path.join(base or os.getcwd(), expanded)
    return os.path.normpath(expanded)


def _url_path(value: str) -> str:
    from urllib.parse import urlsplit

    try:
        split = urlsplit(value)
    except ValueError:
        return ""
    return split.path or ""


def extract_paths(args: Any, *, base: Optional[str] = None) -> Tuple[str, ...]:
    """Return every path-like argument of a call, normalized and de-duplicated.

    Recognized keys are :data:`PATH_ARG_KEYS` (a path) and :data:`URL_ARG_KEYS`
    (the URL's path component).  Values may be a string or a list of strings;
    nested lists are followed to ``_MAX_EXTRACT_DEPTH``.  Nothing else in the
    payload is interpreted.
    """
    if not isinstance(args, Mapping):
        return ()

    found: List[str] = []

    def visit(key: str, value: Any, depth: int) -> None:
        if depth > _MAX_EXTRACT_DEPTH:
            return
        if isinstance(value, str):
            path = _url_path(value) if key in URL_ARG_KEYS else value
            if not path:
                return
            normalized = normalize_path(path, base=base)
            if normalized:
                found.append(normalized)
            return
        if isinstance(value, (list, tuple, set, frozenset)):
            for item in value:
                visit(key, item, depth + 1)
            return
        if isinstance(value, Mapping):
            for nested_key, nested_value in value.items():
                if isinstance(nested_key, str) and (nested_key in PATH_ARG_KEYS or nested_key in URL_ARG_KEYS):
                    visit(nested_key, nested_value, depth + 1)

    for key, value in args.items():
        if isinstance(key, str) and (key in PATH_ARG_KEYS or key in URL_ARG_KEYS):
            visit(key, value, 0)

    seen: Dict[str, None] = {}
    for item in found:
        seen.setdefault(item, None)
    return tuple(seen)


def match_glob(pattern: str, path: str) -> bool:
    """``fnmatch.fnmatchcase`` -- case-sensitive, ``*`` spans separators."""
    return fnmatch.fnmatchcase(path, pattern)


def matches_any(globs: Iterable[str], path: str) -> bool:
    """Whether *path* matches any glob in *globs*."""
    return any(match_glob(pattern, path) for pattern in globs)


def paths_outside_globs(globs: Sequence[str], paths: Sequence[str]) -> Tuple[str, ...]:
    """Those paths that no glob covers."""
    return tuple(path for path in paths if not matches_any(globs, path))


def paths_inside_globs(globs: Sequence[str], paths: Sequence[str]) -> Tuple[str, ...]:
    """Those paths that at least one glob covers."""
    return tuple(path for path in paths if matches_any(globs, path))


def forbidden_hits(manifest: LaneManifest, paths: Sequence[str]) -> Tuple[str, ...]:
    """Those touched paths that intersect ``forbidden_globs``."""
    return paths_inside_globs(manifest.forbidden_globs, paths)


# --------------------------------------------------------------------------- #
# artifact-contract v1 mapping (rules M1-M3; M4 lives in receipts.py)
# --------------------------------------------------------------------------- #


def _contract_paths(handoff: Mapping[str, Any], field_name: str) -> Tuple[str, ...]:
    """Pull ``[{path: ...}, ...]`` entries out of an artifact-contract field."""
    value = handoff.get(field_name)
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field_name} must be a list of {{path: ...}} entries")
    out: List[str] = []
    for entry in value:
        if isinstance(entry, Mapping) and isinstance(entry.get("path"), str):
            out.append(str(entry["path"]))
        elif isinstance(entry, str):
            out.append(entry)
        else:
            raise ValueError(f"{field_name} entry must be an object with a path")
    return tuple(out)


def check_mapping(handoff: Any, manifest: LaneManifest) -> Dict[str, Any]:
    """Check the handoff -> lane-manifest mapping invariants M1-M3.

    * M1: ``artifacts_to_regenerate`` paths are all inside ``owned_write_globs``.
    * M2: ``artifacts_not_to_touch`` paths are disjoint from
      ``owned_write_globs`` and all inside ``forbidden_globs``.
    * M3: ``artifacts_to_regenerate`` and ``artifacts_not_to_touch`` are disjoint
      (protean-team checker rule 6; the gate re-derives the same disjointness
      live per call -- one rule, two enforcement points).

    Returns ``{"M1": {...}, "M2": {...}, "M3": {...}, "ok": bool}``.
    """
    if not isinstance(handoff, Mapping):
        raise ValueError("handoff must be a mapping")

    regenerate = _contract_paths(handoff, "artifacts_to_regenerate")
    frozen = _contract_paths(handoff, "artifacts_not_to_touch")

    m1_outside = paths_outside_globs(manifest.owned_write_globs, regenerate)
    m2_overlap = paths_inside_globs(manifest.owned_write_globs, frozen)
    m2_uncovered = paths_outside_globs(manifest.forbidden_globs, frozen)
    m3_overlap = tuple(sorted(set(regenerate) & set(frozen)))

    result: Dict[str, Any] = {
        "M1": {
            "ok": not m1_outside,
            "regenerate_outside_owned": list(m1_outside),
        },
        "M2": {
            "ok": not m2_overlap and not m2_uncovered,
            "frozen_inside_owned": list(m2_overlap),
            "frozen_outside_forbidden": list(m2_uncovered),
        },
        "M3": {"ok": not m3_overlap, "regenerate_and_frozen_overlap": list(m3_overlap)},
    }
    result["ok"] = bool(result["M1"]["ok"] and result["M2"]["ok"] and result["M3"]["ok"])
    return result
