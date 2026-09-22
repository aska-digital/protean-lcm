"""Acceptance tests 5-11: the decision procedure at ``pre_tool_call``.

Every test drives the real gate (no mocks of the decision path) through
``gate.evaluate_tool_call``, which is the same call the registered hook makes.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from plugins.lane_gate import gate, receipts
from support import (
    LANE_ROOT,
    binding_of,
    unbound,
    valid_manifest,
)

INSIDE_OWNED = f"{LANE_ROOT}/plugins/lane_gate/gate.py"
OUTSIDE_OWNED = "/tmp/not-my-lane/notes.md"
FORBIDDEN_PATH = f"{LANE_ROOT}/LICENSE"


def _run(
    tool: str,
    args: Optional[Dict[str, Any]] = None,
    *,
    mode: str = "enforce",
    manifest: Any = None,
    ledger: Any = None,
    **kwargs: Any,
):
    """Evaluate one call through the real gate against a bound valid manifest."""
    return gate.evaluate_tool_call(
        tool=tool,
        args=args or {},
        mode=mode,
        manifest=binding_of(valid_manifest()) if manifest is None else manifest,
        receipts_path=ledger,
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# 5. read-only fastpath
# --------------------------------------------------------------------------- #


def test_read_only_fastpath(ledger):
    """read_file / search_files / tool_search / tool_describe / safe git ⇒ ALLOW, recorded."""
    read_only_calls = [
        ("read_file", {"path": INSIDE_OWNED}),
        ("search_files", {"pattern": "def decide", "path": LANE_ROOT}),
        ("tool_search", {"queries": ["lane gate"]}),
        ("tool_describe", {"names": ["write_file"]}),
        ("terminal", {"command": "git status --porcelain", "workdir": LANE_ROOT}),
        ("terminal", {"command": "git log --oneline -3"}),
        ("terminal", {"command": "git diff 9d3c4cb..HEAD --stat"}),
        ("terminal", {"command": "grep -rn lane_gate plugins/"}),
    ]

    for tool, args in read_only_calls:
        outcome = _run(tool, args, ledger=ledger)
        assert outcome.decision == "ALLOW", f"{tool} should take the fastpath"
        assert outcome.reason == "read_only_fastpath"
        assert outcome.directive is None, "an ALLOW is the only directive-free path in enforce"

    rows = receipts.read_receipts(ledger)
    assert len(rows) == len(read_only_calls), "one receipt line per decision"
    assert {row["decision"] for row in rows} == {"ALLOW"}
    assert {row["reason"] for row in rows} == {"read_only_fastpath"}
    assert all(row["mode"] == "enforce" for row in rows)


def test_read_only_fastpath_is_narrow(ledger):
    """A shell shape that is not provably read-only never gets the fastpath."""
    not_read_only = [
        "git push origin main",
        "git checkout main",
        "rm -rf build",
        "ls | tee out.txt",
        "PATH=/tmp ls",
        "/bin/cat /etc/passwd",
        "rg --pre 'sh -c' pattern",
    ]
    for command in not_read_only:
        outcome = _run("terminal", {"command": command}, ledger=ledger)
        assert outcome.decision != "ALLOW", f"{command!r} must not be a fastpath"
        assert outcome.reason == "unknown_tool"


# --------------------------------------------------------------------------- #
# 6. forbidden target
# --------------------------------------------------------------------------- #


def test_forbidden_target_blocked(ledger):
    """A path arg hitting ``forbidden_globs`` is BLOCK / forbidden_target."""
    for mode in ("advisory", "enforce"):
        outcome = _run("patch", {"path": FORBIDDEN_PATH, "new_string": "x"}, mode=mode, ledger=ledger)
        assert outcome.decision == "BLOCK"
        assert outcome.reason == "forbidden_target"
        if mode == "enforce":
            assert outcome.directive is not None and outcome.directive["action"] == "block"
            assert "forbidden_target" in outcome.directive["message"]
        else:
            assert outcome.directive is None, "advisory records the block and lets the call proceed"

    rows = receipts.read_receipts(ledger)
    assert [row["decision"] for row in rows] == ["BLOCK", "BLOCK"]
    assert [row["mode"] for row in rows] == ["advisory", "enforce"]


# --------------------------------------------------------------------------- #
# 7. outside owned
# --------------------------------------------------------------------------- #


def test_outside_owned_blocked(ledger):
    """A write outside ``owned_write_globs`` is BLOCK / outside_owned."""
    for mode in ("advisory", "enforce"):
        outcome = _run("write_file", {"path": OUTSIDE_OWNED, "content": "x"}, mode=mode, ledger=ledger)
        assert outcome.decision == "BLOCK"
        assert outcome.reason == "outside_owned"
        assert (outcome.directive is not None) == (mode == "enforce")

    rows = receipts.read_receipts(ledger)
    assert {row["reason"] for row in rows} == {"outside_owned"}


def test_owned_globs_empty_means_no_writes(ledger):
    """``owned_write_globs: []`` is "no writes permitted", not "anything goes"."""
    manifest = binding_of(valid_manifest(owned_write_globs=[]))
    outcome = _run("write_file", {"path": INSIDE_OWNED, "content": "x"}, manifest=manifest, ledger=ledger)
    assert outcome.decision == "BLOCK"
    assert outcome.reason == "outside_owned"


# --------------------------------------------------------------------------- #
# 8. in lane
# --------------------------------------------------------------------------- #


def test_in_lane_write_allowed(ledger):
    """A write whose every touched path is owned is ALLOW / in_lane and executes in enforce."""
    outcome = _run("write_file", {"path": INSIDE_OWNED, "content": "x = 1"}, ledger=ledger)
    assert outcome.decision == "ALLOW"
    assert outcome.reason == "in_lane"
    assert outcome.directive is None, "the call executes in enforce"

    multi = _run(
        "patch",
        {"path": INSIDE_OWNED, "new_string": "y", "old_string": "x"},
        ledger=ledger,
    )
    assert (multi.decision, multi.reason) == ("ALLOW", "in_lane")


def test_multiple_paths_all_must_be_owned(ledger):
    """One path outside the lane blocks the whole call."""
    outcome = _run(
        "write_file",
        {"path": INSIDE_OWNED, "paths": [INSIDE_OWNED, OUTSIDE_OWNED], "content": "x"},
        ledger=ledger,
    )
    assert outcome.decision == "BLOCK"
    assert outcome.reason == "outside_owned"


# --------------------------------------------------------------------------- #
# 9. external writes
# --------------------------------------------------------------------------- #


def test_external_write_escalates(ledger):
    """An external-write-class call with ``external_writes: false`` escalates."""
    outcome = _run("kanban_complete", {"task_id": "t1", "summary": "done"}, ledger=ledger)
    assert outcome.decision == "APPROVAL"
    assert outcome.reason == "external_write"
    assert outcome.directive is not None and outcome.directive["action"] == "block"

    advisory = _run("kanban_complete", {"task_id": "t1"}, mode="advisory", ledger=ledger)
    assert (advisory.decision, advisory.reason) == ("APPROVAL", "external_write")
    assert advisory.directive is None


def test_external_write_authorized_allows(ledger):
    """``external_writes: true`` is the explicit authorization for those tools."""
    manifest = binding_of(valid_manifest(external_writes=True))
    outcome = _run("kanban_comment", {"task_id": "t1", "body": "x"}, manifest=manifest, ledger=ledger)
    assert outcome.decision == "ALLOW"
    assert outcome.reason == "external_write"
    assert outcome.directive is None


# --------------------------------------------------------------------------- #
# 10. unknown tools
# --------------------------------------------------------------------------- #


def test_unknown_tool_escalates(ledger):
    """A tool the classifier cannot place escalates, and is denied in enforce."""
    for tool, args in (
        ("memory", {"action": "add"}),
        ("web_search", {"query": "lane gate"}),
        ("terminal", {"command": "python -m pytest tests/lane_gate"}),
        ("", {}),
    ):
        outcome = _run(tool, args, ledger=ledger)
        assert outcome.decision == "APPROVAL", f"{tool!r} must not execute in enforce"
        assert outcome.reason == "unknown_tool"
        assert outcome.directive is not None and outcome.directive["action"] == "block"

    advisory = _run("web_search", {"query": "lane gate"}, mode="advisory", ledger=ledger)
    assert advisory.directive is None


def test_forbidden_tool_blocked(ledger):
    """``forbidden_tools`` is step 1 and blocks before anything else."""
    outcome = _run("delegate_task", {"goal": "x", "path": INSIDE_OWNED}, ledger=ledger)
    assert (outcome.decision, outcome.reason) == ("BLOCK", "forbidden_tool")


# --------------------------------------------------------------------------- #
# 11. gate errors fail closed
# --------------------------------------------------------------------------- #


def test_gate_error_fail_closed(monkeypatch, ledger):
    """An exception inside the gate is APPROVAL / gate_error: denied in enforce, never raised."""

    def _explode(*_args: Any, **_kwargs: Any) -> str:
        raise RuntimeError("injected classifier failure")

    monkeypatch.setattr(gate, "classify_tool", _explode)

    record = gate.decide(tool="write_file", args={"path": INSIDE_OWNED}, manifest=binding_of(valid_manifest()), mode="enforce")
    assert (record["decision"], record["reason"]) == ("APPROVAL", "gate_error")

    enforced = _run("write_file", {"path": INSIDE_OWNED}, ledger=ledger)
    assert (enforced.decision, enforced.reason) == ("APPROVAL", "gate_error")
    assert enforced.directive is not None and enforced.directive["action"] == "block"

    advisory = _run("write_file", {"path": INSIDE_OWNED}, mode="advisory", ledger=ledger)
    assert (advisory.decision, advisory.reason) == ("APPROVAL", "gate_error")
    assert advisory.directive is None

    rows = receipts.read_receipts(ledger)
    assert {row["reason"] for row in rows} == {"gate_error"}


def test_hook_never_raises(monkeypatch, ledger, manifest_file):
    """Even when the whole evaluation explodes, the hook returns a directive or None."""
    import plugins.lane_gate as lane_gate

    manifest_path = manifest_file(valid_manifest().as_dict())

    def _explode(**_kwargs: Any):
        raise RuntimeError("injected evaluation failure")

    monkeypatch.setattr(gate, "evaluate_tool_call", _explode)

    state = {
        "settings": {
            "mode": "enforce",
            "manifest": str(manifest_path),
            "receipts": str(ledger),
            "receipt_detail": "hash",
        }
    }
    hook = lane_gate.build_pre_tool_call_hook(state)
    directive = hook(tool_name="write_file", args={"path": INSIDE_OWNED}, session_id="s", tool_call_id="c")
    assert directive is not None and directive["action"] == "block"
    assert directive["message"], "a block directive must carry a message"

    state["settings"]["mode"] = "advisory"
    assert hook(tool_name="write_file", args={"path": INSIDE_OWNED}) is None


def test_unbound_worker_gets_no_fastpath(ledger):
    """Steps 3-7 are unreachable without a valid manifest: everything escalates."""
    for tool, args in (
        ("read_file", {"path": INSIDE_OWNED}),
        ("write_file", {"path": INSIDE_OWNED}),
        ("terminal", {"command": "git status"}),
    ):
        outcome = _run(tool, args, manifest=unbound(), ledger=ledger)
        assert (outcome.decision, outcome.reason) == ("APPROVAL", "manifest_invalid")
        assert outcome.directive is not None and outcome.directive["action"] == "block"

    rows = receipts.read_receipts(ledger)
    assert all(row["lane_id"] is None for row in rows)
    assert all(len(row["manifest_sha256"]) == 64 for row in rows)


def test_receipt_write_failure_denies_allow(monkeypatch, tmp_path, ledger):
    """In enforce a call only executes when its ALLOW was recorded (I3)."""

    def _fail(*_args: Any, **_kwargs: Any):
        raise receipts.ReceiptWriteError("ledger unavailable")

    monkeypatch.setattr(receipts, "append_receipt", _fail)
    outcome = _run("write_file", {"path": INSIDE_OWNED}, ledger=ledger)
    assert (outcome.decision, outcome.reason) == ("APPROVAL", "gate_error")
    assert outcome.receipt_written is False
    assert outcome.directive is not None and outcome.directive["action"] == "block"


def test_decision_record_shape(ledger):
    """The decision record is the closed ``protean/lane-gate/v1`` shape."""
    outcome = _run("write_file", {"path": INSIDE_OWNED}, ledger=ledger)
    assert set(outcome.record) == {"contract", "decision", "reason", "manifest_sha256", "mode"}
    assert outcome.record["contract"] == "protean/lane-gate/v1"
    assert outcome.record["decision"] in gate.DECISIONS
    assert outcome.record["reason"] in gate.REASONS
    assert len(outcome.record["manifest_sha256"]) == 64
    assert outcome.record["mode"] in ("advisory", "enforce")


def test_modes_are_clamped(ledger):
    """``decide`` reports only advisory/enforce; off lives in registration."""
    record = gate.decide(tool="read_file", args={"path": INSIDE_OWNED}, manifest=binding_of(valid_manifest()), mode="off")
    assert record["mode"] == "advisory"
    record = gate.decide(tool="read_file", args={"path": INSIDE_OWNED}, manifest=binding_of(valid_manifest()), mode="nonsense")
    assert record["mode"] == "advisory"
    assert gate.host_directive(record) is None


@pytest.mark.parametrize("mode", ["advisory", "enforce"])
def test_no_model_in_the_decision_path(mode, ledger):
    """The gate is local and deterministic: no provider/network client is touched."""
    import sys

    blocked = {"openai", "anthropic", "httpx", "requests", "urllib3", "socket"}
    before = {name for name in sys.modules if name.split(".")[0] in blocked}
    _run("write_file", {"path": INSIDE_OWNED}, mode=mode, ledger=ledger)
    after = {name for name in sys.modules if name.split(".")[0] in blocked}
    assert after == before, "the gate must not reach for a network client"
