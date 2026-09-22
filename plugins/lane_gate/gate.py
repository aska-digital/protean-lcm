"""Lane Gate decision procedure: ``protean/lane-gate/v1`` at ``pre_tool_call``.

A deterministic, local, model-free gate.  There is no provider call and no LLM in
the decision path; the procedure is a closed rule list over the tool name, the
call's arguments, and the bound lane manifest.

Decision procedure (order is normative, first match wins; locked contract §3.2):

0. No valid manifest bound ............................ APPROVAL / manifest_invalid
1. ``forbidden_tools`` contains the tool .............. BLOCK    / forbidden_tool
2. A touched path hits ``forbidden_globs`` ............ BLOCK    / forbidden_target
3. Read-only tool or obviously read-only shell shape .. ALLOW    / read_only_fastpath
4. Local mutation, every path inside owned globs ...... ALLOW    / in_lane
5. Local mutation with a path outside owned globs ..... BLOCK    / outside_owned
6. External-write-class tool .......................... ALLOW (authorized) or
                                                        APPROVAL / external_write
7. Tool the classifier cannot place ................... APPROVAL / unknown_tool
8. Any step raises .................................... APPROVAL / gate_error

Steps 3-7 are unreachable without a valid manifest: an unbound worker gets no
fastpath and no in-lane allowance, nothing but escalation.

Two implementation notes the locked contract leaves to the implementation, both
chosen in the fail-closed direction:

* **Shell calls are not path-checkable.**  ``terminal`` (and its aliases) that is
  not an obviously read-only shape has no statically extractable touched path, so
  the classifier cannot place it in the path-based contract: step 7 escalates it
  (APPROVAL / unknown_tool).  It is never auto-allowed.  A read-only shell shape
  still takes the step-3 fastpath.
* **A local mutation with no extractable path** cannot be shown to be in-lane
  either, so it escalates the same way rather than passing vacuously.

Both notes widen nothing: an unlisted tool escalates, never executes.

Provenance: the ALLOW/APPROVAL/BLOCK choice contract re-declared here, and the
read-only prefilter set (read-only tools, simple read-only shell commands, safe
git subcommands, and the shell-metacharacter guard) are adapted from
``keeltrace/hermes-nerve`` gate.py @
``de219b1875a9943cb81de406c6853caf53496aaf`` -- MIT, ``Copyright (c) 2026 Nerve
contributors``.  See PROVENANCE-LANE-GATE.md.  The upstream text is
re-implemented against our own closed contract; nothing is copied verbatim, and
upstream's model round-trip (issue #8) is deliberately absent.
"""

from __future__ import annotations

import re
import shlex
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from . import receipts as _receipts
from .manifest import (
    ABSENT_SHA256,
    ManifestBinding,
    LaneManifest,
    extract_paths,
    forbidden_hits,
    paths_outside_globs,
)

CONTRACT = "protean/lane-gate/v1"

DECISIONS: Tuple[str, ...] = ("ALLOW", "APPROVAL", "BLOCK")

#: The closed reason enum.  Exactly one reason per decision.
REASONS: Tuple[str, ...] = (
    "read_only_fastpath",
    "in_lane",
    "forbidden_target",
    "outside_owned",
    "forbidden_tool",
    "unknown_tool",
    "external_write",
    "manifest_invalid",
    "gate_error",
)

MODES: Tuple[str, ...] = ("off", "advisory", "enforce")

# --------------------------------------------------------------------------- #
# classifier sets
# --------------------------------------------------------------------------- #

#: Read-only fastpath tools -- exactly the four the locked contract names.
READ_ONLY_TOOLS = frozenset({"read_file", "search_files", "tool_search", "tool_describe"})

#: Shell tools whose argument is a command string (upstream prefilter shape).
SHELL_TOOLS = frozenset({"terminal", "shell", "bash"})

#: Deliberately narrow read-only shell commands (upstream prefilter set).
READ_ONLY_SHELL_COMMANDS = frozenset(
    {
        "pwd",
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "stat",
        "du",
        "df",
        "file",
        "readlink",
        "basename",
        "dirname",
        "realpath",
        "which",
        "whereis",
        "whoami",
        "id",
        "uname",
        "uptime",
        "free",
        "ps",
        "printenv",
        "grep",
        "rg",
        "jq",
    }
)

#: Git subcommands that only read (upstream prefilter set).
SAFE_GIT_SUBCOMMANDS = frozenset(
    {"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree", "describe", "grep", "blame"}
)

#: Shell metacharacters that make a command not provably read-only.
SHELL_META_RE = re.compile(r"(?:&&|\|\||[;|><`]|\$\()")

#: Local mutations whose touched paths are declared in the args (path-checkable).
LOCAL_MUTATING_TOOLS = frozenset({"write_file", "patch", "skill_manage"})

#: External-write class: task-board writes, messaging, and remote/network
#: mutations.  ``external_writes: true`` in the manifest is the only thing that
#: turns these into ALLOW.  Names come from the host's own tool registry.
EXTERNAL_WRITE_TOOLS = frozenset(
    {
        # task-board writes
        "kanban_attach",
        "kanban_attach_url",
        "kanban_block",
        "kanban_comment",
        "kanban_complete",
        "kanban_create",
        "kanban_heartbeat",
        "kanban_link",
        "kanban_request_changes",
        "kanban_request_review",
        "kanban_unblock",
        # messaging
        "feishu_drive_add_comment",
        "feishu_drive_reply_comment",
        "react_to_message",
        "yb_send_dm",
        "yb_send_sticker",
        # network posts / remote mutations / live-desktop drive
        "browser_cdp",
        "browser_click",
        "browser_dialog",
        "browser_exec",
        "browser_press",
        "browser_type",
        "browser_vault_enter_code",
        "browser_vault_fill",
        "browser_vault_save_login",
        "browser_vault_unlock",
        "computer_use",
        "gui_tour",
        "ha_call_service",
    }
)

#: Classification outcomes.
CLASS_READ_ONLY = "read_only"
CLASS_LOCAL_MUTATION = "local_mutation"
CLASS_EXTERNAL_WRITE = "external_write"
CLASS_UNKNOWN = "unknown"


# --------------------------------------------------------------------------- #
# classification
# --------------------------------------------------------------------------- #


def _shell_command(args: Mapping[str, Any]) -> str:
    for key in ("command", "cmd", "script"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def read_only_shell(command: str) -> bool:
    """True only for deliberately narrow, obvious read-only shell shapes.

    Re-implemented from the upstream prefilter: no shell metacharacters, no
    environment-prefixed or path-qualified executables (``PATH=`` and
    ``LD_PRELOAD=`` shapes can execute non-read-only code), and no
    preprocessor-invoking flags (``rg --pre``, ``git --ext-diff``/``--textconv``).
    """
    if not command or SHELL_META_RE.search(command):
        return False
    try:
        parts = shlex.split(command, posix=True)
    except ValueError:
        return False
    if not parts:
        return False
    if "=" in parts[0] or "/" in parts[0]:
        return False

    executable = parts[0]
    if executable in READ_ONLY_SHELL_COMMANDS:
        if executable == "rg" and any(
            arg == "--pre" or arg.startswith("--pre=") or arg == "--pre-glob" or arg.startswith("--pre-glob=")
            for arg in parts[1:]
        ):
            return False
        return True
    if executable == "git" and len(parts) >= 2:
        if parts[1] not in SAFE_GIT_SUBCOMMANDS:
            return False
        if any(
            arg in {"--ext-diff", "--textconv"}
            or arg.startswith("--ext-diff=")
            or arg.startswith("--textconv=")
            for arg in parts[2:]
        ):
            return False
        return True
    return False


def classify_tool(tool: str, args: Mapping[str, Any]) -> str:
    """Place one tool call in the classifier's closed set of classes."""
    name = str(tool or "").strip()
    if not name:
        return CLASS_UNKNOWN
    if name in READ_ONLY_TOOLS:
        return CLASS_READ_ONLY
    if name in SHELL_TOOLS:
        return CLASS_READ_ONLY if read_only_shell(_shell_command(args)) else CLASS_UNKNOWN
    if name in LOCAL_MUTATING_TOOLS:
        return CLASS_LOCAL_MUTATION
    if name in EXTERNAL_WRITE_TOOLS:
        return CLASS_EXTERNAL_WRITE
    return CLASS_UNKNOWN


# --------------------------------------------------------------------------- #
# decision
# --------------------------------------------------------------------------- #


def _as_binding(manifest: Any) -> ManifestBinding:
    """Accept a :class:`ManifestBinding`, a :class:`LaneManifest`, or ``None``."""
    if isinstance(manifest, ManifestBinding):
        return manifest
    if isinstance(manifest, LaneManifest):
        return ManifestBinding(status="ok", sha256=manifest.sha256, manifest=manifest)
    return ManifestBinding.absent()


def _decide_bound(
    tool: str,
    args: Mapping[str, Any],
    bound: LaneManifest,
    base_dir: Optional[str],
) -> Tuple[str, str]:
    """Steps 1-7 over a valid manifest."""
    if tool in bound.forbidden_tools:
        return "BLOCK", "forbidden_tool"

    paths = extract_paths(args, base=base_dir)
    if forbidden_hits(bound, paths):
        return "BLOCK", "forbidden_target"

    tool_class = classify_tool(tool, args)

    if tool_class == CLASS_READ_ONLY:
        return "ALLOW", "read_only_fastpath"

    if tool_class == CLASS_LOCAL_MUTATION:
        if not paths:
            # Not placeable: a path-based contract cannot vouch for this call.
            return "APPROVAL", "unknown_tool"
        if paths_outside_globs(bound.owned_write_globs, paths):
            return "BLOCK", "outside_owned"
        return "ALLOW", "in_lane"

    if tool_class == CLASS_EXTERNAL_WRITE:
        return ("ALLOW", "external_write") if bound.external_writes else ("APPROVAL", "external_write")

    return "APPROVAL", "unknown_tool"


def decide(
    *,
    tool: str,
    args: Any = None,
    manifest: Any = None,
    mode: str = "advisory",
    base_dir: Optional[str] = None,
) -> Dict[str, Any]:
    """Pure decision procedure.  Returns one ``protean/lane-gate/v1`` record.

    ``manifest`` is a :class:`~.manifest.ManifestBinding` (preferred) or a
    :class:`~.manifest.LaneManifest`; anything else counts as unbound.  ``mode``
    is clamped to ``advisory`` unless it is exactly ``enforce`` (the ``off``
    no-hook behaviour lives in the plugin entry point, not here).
    """
    mode_value = "enforce" if mode == "enforce" else "advisory"
    binding = _as_binding(manifest)
    tool_name = str(tool or "")
    payload: Mapping[str, Any] = args if isinstance(args, Mapping) else {}

    try:
        if not binding.ok:
            decision, reason = "APPROVAL", "manifest_invalid"
        else:
            decision, reason = _decide_bound(tool_name, payload, binding.bound(), base_dir)
    except Exception:
        # Fail closed: an exception in the gate denies, it never executes.
        decision, reason = "APPROVAL", "gate_error"

    return {
        "contract": CONTRACT,
        "decision": decision,
        "reason": reason,
        "manifest_sha256": binding.sha256 or ABSENT_SHA256,
        "mode": mode_value,
    }


def host_directive(record: Mapping[str, Any], *, receipt_note: str = "") -> Optional[Dict[str, Any]]:
    """Map a decision to the host's ``pre_tool_call`` directive.

    * ``advisory``: ``None`` -- record-only, the call proceeds.
    * ``enforce`` + ``ALLOW``: ``None`` -- the only way a call executes.
    * ``enforce`` + ``APPROVAL``/``BLOCK``: a ``block`` directive.  In this slice
      APPROVAL means deny-and-escalate: the receipt is the escalation record and
      there is no interactive approval transport (locked §4 / gap G4).
    """
    if record.get("mode") != "enforce" or record.get("decision") == "ALLOW":
        return None
    return {"action": "block", "message": block_message(record, receipt_note=receipt_note)}


def block_message(record: Mapping[str, Any], *, receipt_note: str = "") -> str:
    """The tool-result message shown when the gate denies a call."""
    message = (
        "Lane Gate {decision}: {reason} (contract {contract}, mode {mode}, "
        "manifest {sha}). This call was denied before execution.".format(
            decision=record.get("decision"),
            reason=record.get("reason"),
            contract=record.get("contract"),
            mode=record.get("mode"),
            sha=str(record.get("manifest_sha256"))[:12],
        )
    )
    if receipt_note:
        message += f" {receipt_note}"
    return message


# --------------------------------------------------------------------------- #
# orchestration: decide -> receipt -> directive
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class GateOutcome:
    """One gated call: the decision record, the receipt, and the host directive."""

    record: Dict[str, Any]
    receipt: Dict[str, Any]
    directive: Optional[Dict[str, Any]]
    receipt_written: bool
    receipt_error: str = ""

    @property
    def decision(self) -> str:
        return str(self.record.get("decision"))

    @property
    def reason(self) -> str:
        return str(self.record.get("reason"))


def evaluate_tool_call(
    *,
    tool: str,
    args: Any = None,
    mode: str = "advisory",
    manifest: Any = None,
    session_id: str = "",
    request_id: Optional[str] = None,
    receipt_detail: str = "hash",
    receipts_path: Any = None,
    base_dir: Optional[str] = None,
) -> GateOutcome:
    """Decide, record, and translate one call into the host directive.

    Fail-closed rule: in ``enforce`` the only executing decision is a *recorded*
    ALLOW.  If the ledger cannot be appended to, an ALLOW is downgraded to
    APPROVAL / gate_error and denied -- there is no path where a missing record
    still executes (locked §4, invariant I3).
    """
    started = time.monotonic()
    binding = _as_binding(manifest)
    tool_name = str(tool or "")
    payload: Mapping[str, Any] = args if isinstance(args, Mapping) else {}

    record = decide(tool=tool_name, args=payload, manifest=binding, mode=mode, base_dir=base_dir)

    def _make_receipt(active: Mapping[str, Any]) -> Dict[str, Any]:
        settled = time.monotonic() - started
        return _receipts.build_receipt(
            _receipts.ReceiptInput(
                mode=str(active.get("mode") or "advisory"),
                tool=tool_name,
                args=payload,
                decision=str(active.get("decision") or ""),
                reason=str(active.get("reason") or ""),
                manifest_sha256=str(active.get("manifest_sha256") or ABSENT_SHA256),
                lane_id=binding.manifest.lane_id if binding.manifest is not None else None,
                session_id=session_id,
                latency_ms=settled * 1000.0,
                request_id=request_id,
            ),
            detail=receipt_detail,
        )

    receipt = _make_receipt(record)
    receipt_written = True
    receipt_error = ""
    try:
        _receipts.append_receipt(receipt, receipts_path)
    except Exception as exc:  # noqa: BLE001 - the gate never raises into the host
        receipt_written = False
        receipt_error = str(exc)
        if record.get("mode") == "enforce" and record.get("decision") == "ALLOW":
            record = dict(record, decision="APPROVAL", reason="gate_error")
            receipt = _make_receipt(record)
            try:
                _receipts.append_receipt(receipt, receipts_path)
                receipt_written = True
            except Exception as retry_exc:  # noqa: BLE001
                receipt_error = f"{receipt_error}; retry failed: {retry_exc}"

    note = "" if receipt_written else "Receipt ledger unavailable; no ALLOW is recorded for this call."
    directive = host_directive(record, receipt_note=note)

    return GateOutcome(
        record=record,
        receipt=receipt,
        directive=directive,
        receipt_written=receipt_written,
        receipt_error=receipt_error,
    )
