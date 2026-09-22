"""Lane receipts: append-only JSONL decision records (``protean/lane-receipt/v1``).

One line per gate decision, in ``advisory`` and ``enforce`` modes (in ``off``
the hook is not registered, so no lines exist).

Required keys (values are ``null`` only where noted):

    schema            "protean/lane-receipt/v1"
    receipt_id        sha256 over the content-bound identity fields
    created_at        ISO-8601 UTC, second precision
    mode              "advisory" | "enforce"
    lane_id           bound lane id, or null when no manifest is bound
    manifest_sha256   hex64 (the empty-input digest when nothing is bound)
    session_id        string
    tool              string
    args_sha256       sha256 of the call args' canonical JSON
    decision          ALLOW | APPROVAL | BLOCK
    reason            one value of the closed reason enum
    latency_ms        number
    request_id        string or null
    args_sanitized    redacted rendering, or null in the default ("hash") detail mode

Privacy default is **hash**: argument contents never land in the ledger unless
``PROTEAN_LANE_RECEIPT_DETAIL=sanitized``.  The writer opens in append mode and
never seeks or rewrites, so ``created_at`` is non-decreasing across the file --
an invariant an auditor can check.

Provenance: the content-bound receipt-identity idea is adapted from
``keeltrace/hermes-nerve`` @ ``de219b1875a9943cb81de406c6853caf53496aaf``
(MIT, ``Copyright (c) 2026 Nerve contributors``).  See PROVENANCE-LANE-GATE.md.
No upstream source file is copied.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from .manifest import ABSENT_SHA256, canonical_json_bytes

SCHEMA = "protean/lane-receipt/v1"

REQUIRED_KEYS: Tuple[str, ...] = (
    "schema",
    "receipt_id",
    "created_at",
    "mode",
    "lane_id",
    "manifest_sha256",
    "session_id",
    "tool",
    "args_sha256",
    "decision",
    "reason",
    "latency_ms",
    "request_id",
    "args_sanitized",
)

#: Keys whose value may legitimately be ``null``.
NULLABLE_KEYS = frozenset({"lane_id", "request_id", "args_sanitized"})

DETAIL_MODES: Tuple[str, ...] = ("hash", "sanitized")

DEFAULT_RECEIPT_DIRNAME = "lane_gate"
DEFAULT_RECEIPT_FILENAME = "receipts.jsonl"

_IDENTITY_SEPARATOR = "|"

#: Keys whose argument values are never rendered, even in ``sanitized`` detail.
_SECRET_KEY_RE = re.compile(r"(token|secret|password|passwd|api[_-]?key|credential|cookie|authorization)", re.I)
_MAX_SANITIZED_VALUE_CHARS = 120
_MAX_SANITIZED_ARG_KEYS = 40


# --------------------------------------------------------------------------- #
# paths and clock
# --------------------------------------------------------------------------- #


def hermes_home() -> Path:
    """``$HERMES_HOME`` or ``~/.hermes``."""
    env = str(os.environ.get("HERMES_HOME") or "").strip()
    return Path(env).expanduser() if env else Path.home() / ".hermes"


def default_receipt_path() -> Path:
    """``<HERMES_HOME>/lane_gate/receipts.jsonl`` (storage default from the locked contract)."""
    return hermes_home() / DEFAULT_RECEIPT_DIRNAME / DEFAULT_RECEIPT_FILENAME


def receipt_path(explicit: Any = None) -> Path:
    """Resolve the ledger path: explicit argument, then ``PROTEAN_LANE_RECEIPTS``, then the default."""
    for candidate in (explicit, os.environ.get("PROTEAN_LANE_RECEIPTS")):
        if candidate is None:
            continue
        text = str(candidate).strip()
        if text:
            return Path(text).expanduser()
    return default_receipt_path()


def utc_now_iso() -> str:
    """ISO-8601 UTC, second precision (``2026-09-22T13:05:07Z``)."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------------- #
# hashing and identity
# --------------------------------------------------------------------------- #


def args_sha256(args: Any) -> str:
    """sha256 of the canonical JSON of a call's args (privacy-preserving default)."""
    payload = args if isinstance(args, Mapping) else {}
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def compute_receipt_id(
    *,
    schema: str,
    created_at: str,
    manifest_sha256: str,
    tool: str,
    args_sha256_value: str,
    decision: str,
    reason: str,
    request_id: Optional[str],
) -> str:
    """``sha256`` over the content-bound identity fields, ``|`` separated."""
    fields: Sequence[str] = (
        str(schema),
        str(created_at),
        str(manifest_sha256),
        str(tool),
        str(args_sha256_value),
        str(decision),
        str(reason),
        "" if request_id is None else str(request_id),
    )
    joined = _IDENTITY_SEPARATOR.join(fields)
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# sanitized rendering (opt-in)
# --------------------------------------------------------------------------- #


def _sanitize_value(key: str, value: Any) -> Any:
    if _SECRET_KEY_RE.search(key):
        return "<redacted>"
    if isinstance(value, str):
        text = value
        if len(text) > _MAX_SANITIZED_VALUE_CHARS:
            return text[:_MAX_SANITIZED_VALUE_CHARS] + f"...<{len(text) - _MAX_SANITIZED_VALUE_CHARS} more chars>"
        return text
    if isinstance(value, bool) or value is None or isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_sanitize_value(key, item) for item in list(value)[:10]]
    if isinstance(value, Mapping):
        return "<object>"
    return "<" + type(value).__name__ + ">"


def sanitize_args(args: Any) -> Optional[str]:
    """A redacted, bounded rendering of the call args (used only in ``sanitized`` detail mode)."""
    if not isinstance(args, Mapping) or not args:
        return None
    rendered: Dict[str, Any] = {}
    for index, (key, value) in enumerate(args.items()):
        if index >= _MAX_SANITIZED_ARG_KEYS:
            rendered["<truncated>"] = len(args) - _MAX_SANITIZED_ARG_KEYS
            break
        rendered[str(key)] = _sanitize_value(str(key), value)
    return json.dumps(rendered, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


# --------------------------------------------------------------------------- #
# record construction
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ReceiptInput:
    """Everything a receipt needs, before identity and timestamp are computed."""

    mode: str
    tool: str
    args: Mapping[str, Any]
    decision: str
    reason: str
    manifest_sha256: str = ABSENT_SHA256
    lane_id: Optional[str] = None
    session_id: str = ""
    latency_ms: float = 0.0
    request_id: Optional[str] = None


def build_receipt(
    data: ReceiptInput,
    *,
    detail: str = "hash",
    created_at: Optional[str] = None,
    receipt_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Build one ``protean/lane-receipt/v1`` record (all required keys present)."""
    timestamp = created_at or utc_now_iso()
    digest = args_sha256(data.args)
    sanitized = sanitize_args(data.args) if detail == "sanitized" else None
    identifier = receipt_id or compute_receipt_id(
        schema=SCHEMA,
        created_at=timestamp,
        manifest_sha256=data.manifest_sha256,
        tool=data.tool,
        args_sha256_value=digest,
        decision=data.decision,
        reason=data.reason,
        request_id=data.request_id,
    )
    return {
        "schema": SCHEMA,
        "receipt_id": identifier,
        "created_at": timestamp,
        "mode": data.mode,
        "lane_id": data.lane_id,
        "manifest_sha256": data.manifest_sha256,
        "session_id": str(data.session_id or ""),
        "tool": str(data.tool or ""),
        "args_sha256": digest,
        "decision": data.decision,
        "reason": data.reason,
        "latency_ms": round(float(data.latency_ms or 0.0), 3),
        "request_id": data.request_id,
        "args_sanitized": sanitized,
    }


def verify_receipt(record: Mapping[str, Any]) -> List[str]:
    """Return the list of integrity violations for one receipt (empty list = intact)."""
    problems: List[str] = []
    if not isinstance(record, Mapping):
        return ["record is not an object"]
    for key in REQUIRED_KEYS:
        if key not in record:
            problems.append(f"missing key: {key}")
    if problems:
        return problems
    if record["schema"] != SCHEMA:
        problems.append(f"schema is {record['schema']!r}, expected {SCHEMA!r}")
    for key in ("receipt_id", "manifest_sha256", "args_sha256"):
        value = record.get(key)
        if not isinstance(value, str) or len(value) != 64 or not _is_hex64(value):
            problems.append(f"{key} is not a hex64 digest")
    for key in REQUIRED_KEYS:
        if key in NULLABLE_KEYS:
            continue
        if record.get(key) is None:
            problems.append(f"{key} must not be null")
    expected = compute_receipt_id(
        schema=record["schema"],
        created_at=record["created_at"],
        manifest_sha256=record["manifest_sha256"],
        tool=record["tool"],
        args_sha256_value=record["args_sha256"],
        decision=record["decision"],
        reason=record["reason"],
        request_id=record.get("request_id"),
    )
    if expected != record.get("receipt_id"):
        problems.append("receipt_id does not recompute")
    if not isinstance(record.get("latency_ms"), (int, float)):
        problems.append("latency_ms is not a number")
    return problems


def _is_hex64(text: str) -> bool:
    return all(char in "0123456789abcdef" for char in text.lower())


# --------------------------------------------------------------------------- #
# append-only writer
# --------------------------------------------------------------------------- #


class ReceiptWriteError(RuntimeError):
    """The ledger could not be appended to."""


def append_receipt(record: Mapping[str, Any], path: Any = None) -> Path:
    """Append one receipt line to the ledger, creating parent directories.

    Opens with ``O_APPEND`` and never seeks or rewrites: append-only by
    construction.  Raises :class:`ReceiptWriteError` on failure so the caller can
    decide the fail-closed response.
    """
    target = receipt_path(path)
    line = json.dumps(dict(record), sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(target), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            os.write(descriptor, line.encode("utf-8"))
        finally:
            os.close(descriptor)
    except OSError as exc:
        raise ReceiptWriteError(f"{exc.__class__.__name__}: could not append receipt to {target}") from None
    return target


def read_receipts(path: Any = None) -> List[Dict[str, Any]]:
    """Read the ledger back (auditor/CLI path only -- the gate never reads receipts)."""
    target = receipt_path(path)
    if not target.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in target.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


# --------------------------------------------------------------------------- #
# M4 citation convention
# --------------------------------------------------------------------------- #

_CITATION_RE = re.compile(
    r"^lane-receipt/v1 (?P<receipt_id>[0-9a-f]{64}) "
    r"\((?P<tool>[^()]+) -> (?P<decision>ALLOW|APPROVAL|BLOCK)/(?P<reason>[a-z_]+)\)"
    r"(?:; proves: (?P<proves>.+))?$"
)


def format_citation(
    record: Mapping[str, Any],
    *,
    proves: Optional[str] = None,
) -> str:
    """Render the artifact-contract ``evidence_refs`` citation for a receipt (M4).

    ``lane-receipt/v1 <receipt_id> (<tool> -> <decision>/<reason>)`` with an
    optional ``; proves: <state>`` clause naming the achieved state.
    """
    text = "lane-receipt/v1 {rid} ({tool} -> {decision}/{reason})".format(
        rid=record.get("receipt_id"),
        tool=record.get("tool"),
        decision=record.get("decision"),
        reason=record.get("reason"),
    )
    if proves:
        text += f"; proves: {str(proves).strip()}"
    return text


def parse_citation(text: Any) -> Optional[Dict[str, str]]:
    """Parse a citation back to its fields, or ``None`` when it does not match the convention."""
    if not isinstance(text, str):
        return None
    match = _CITATION_RE.match(text.strip())
    if match is None:
        return None
    return {
        "receipt_id": match.group("receipt_id"),
        "tool": match.group("tool"),
        "decision": match.group("decision"),
        "reason": match.group("reason"),
        "proves": (match.group("proves") or "").strip(),
    }
