"""Acceptance tests 12-13: receipt integrity and the privacy default."""

from __future__ import annotations

import json
import re
from datetime import datetime

import pytest

from plugins.lane_gate import gate, manifest, receipts
from support import LANE_ROOT, binding_of, valid_manifest

INSIDE_OWNED = f"{LANE_ROOT}/plugins/lane_gate/gate.py"
OUTSIDE_OWNED = "/tmp/not-my-lane/notes.md"
SECRET_MARKER = "SUPER-SECRET-ARGUMENT-CONTENT"
_ISO_UTC_SECONDS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _call(tool: str, args: dict, *, mode: str = "enforce", ledger=None, detail: str = "hash"):
    return gate.evaluate_tool_call(
        tool=tool,
        args=args,
        mode=mode,
        manifest=binding_of(valid_manifest()),
        receipts_path=ledger,
        receipt_detail=detail,
        session_id="sess-1",
        request_id="call-1",
    )


def _lines(path):
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


# --------------------------------------------------------------------------- #
# 12. integrity
# --------------------------------------------------------------------------- #


def test_receipt_integrity(ledger):
    """Every line is a valid, self-consistent ``protean/lane-receipt/v1`` record."""
    first = _call("write_file", {"path": INSIDE_OWNED, "content": "x = 1"}, ledger=ledger)
    second = _call("write_file", {"path": OUTSIDE_OWNED, "content": "x"}, ledger=ledger)
    assert first.receipt_written and second.receipt_written

    lines = _lines(ledger)
    assert len(lines) == 2, "one line per decision"

    stamps = []
    for line, (tool, args) in zip(
        lines,
        (
            ("write_file", {"path": INSIDE_OWNED, "content": "x = 1"}),
            ("write_file", {"path": OUTSIDE_OWNED, "content": "x"}),
        ),
    ):
        record = json.loads(line)
        assert set(receipts.REQUIRED_KEYS) <= set(record), "all required keys present"
        assert receipts.verify_receipt(record) == [], "receipt recomputes from its own fields"
        assert record["schema"] == "protean/lane-receipt/v1"
        assert record["args_sha256"] == receipts.args_sha256(args), "args are content-bound"
        assert record["tool"] == tool
        assert record["session_id"] == "sess-1"
        assert record["request_id"] == "call-1"
        assert record["manifest_sha256"] == valid_manifest().sha256
        assert record["lane_id"] == "mozi-nerve-lane-gate"
        assert _ISO_UTC_SECONDS.match(record["created_at"]), record["created_at"]
        assert isinstance(record["latency_ms"], (int, float)) and record["latency_ms"] >= 0
        stamps.append(datetime.strptime(record["created_at"], "%Y-%m-%dT%H:%M:%SZ"))

    assert stamps == sorted(stamps), "created_at is non-decreasing across the file"
    assert [json.loads(line)["decision"] for line in lines] == ["ALLOW", "BLOCK"]
    assert [json.loads(line)["reason"] for line in lines] == ["in_lane", "outside_owned"]


def test_receipt_file_is_append_only(ledger):
    """Two runs append: the earlier bytes are untouched and the file only grows."""
    _call("write_file", {"path": INSIDE_OWNED, "content": "one"}, ledger=ledger)
    before = ledger.read_bytes()
    before_count = len(_lines(ledger))

    _call("write_file", {"path": INSIDE_OWNED, "content": "two"}, ledger=ledger)
    _call("read_file", {"path": INSIDE_OWNED}, ledger=ledger)

    after = ledger.read_bytes()
    assert after.startswith(before), "the ledger is never rewritten"
    assert len(_lines(ledger)) == before_count + 2


def test_receipt_identity_is_content_bound(ledger):
    """The same call twice gives the same identity only when every field matches."""
    first = _call("write_file", {"path": INSIDE_OWNED, "content": "x"}, ledger=ledger)
    second = _call("write_file", {"path": INSIDE_OWNED, "content": "x"}, ledger=ledger)
    third = _call("write_file", {"path": INSIDE_OWNED, "content": "y"}, ledger=ledger)

    assert first.receipt["receipt_id"] == second.receipt["receipt_id"]
    assert third.receipt["receipt_id"] != first.receipt["receipt_id"]
    assert third.receipt["args_sha256"] != first.receipt["args_sha256"]

    tampered = dict(first.receipt, decision="ALLOW" if first.decision != "ALLOW" else "BLOCK")
    assert "receipt_id does not recompute" in receipts.verify_receipt(tampered)


def test_receipt_records_the_decision_even_in_advisory(ledger):
    """Advisory is record-only for execution, never for the ledger."""
    outcome = _call("write_file", {"path": OUTSIDE_OWNED, "content": "x"}, mode="advisory", ledger=ledger)
    assert outcome.directive is None
    record = json.loads(_lines(ledger)[0])
    assert (record["decision"], record["reason"], record["mode"]) == ("BLOCK", "outside_owned", "advisory")


def test_receipt_storage_default_and_env_override(tmp_path, monkeypatch):
    """The ledger lives at ``PROTEAN_LANE_RECEIPTS`` or ``<HERMES_HOME>/lane_gate/receipts.jsonl``."""
    monkeypatch.delenv("PROTEAN_LANE_RECEIPTS", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / "home"))
    assert receipts.receipt_path() == tmp_path / "home" / "lane_gate" / "receipts.jsonl"

    monkeypatch.setenv("PROTEAN_LANE_RECEIPTS", str(tmp_path / "explicit.jsonl"))
    assert receipts.receipt_path() == tmp_path / "explicit.jsonl"

    outcome = _call("write_file", {"path": INSIDE_OWNED, "content": "x"}, ledger=None)
    assert outcome.receipt_written
    assert (tmp_path / "explicit.jsonl").exists()


# --------------------------------------------------------------------------- #
# 13. privacy default
# --------------------------------------------------------------------------- #


def test_receipt_privacy_default(ledger):
    """The default detail mode stores hashes only: no argument content reaches the ledger."""
    _call("write_file", {"path": INSIDE_OWNED, "content": SECRET_MARKER}, ledger=ledger)
    raw = ledger.read_text(encoding="utf-8")
    assert SECRET_MARKER not in raw, "argument content must never land in the ledger by default"
    assert INSIDE_OWNED not in raw, "paths are hashed too"

    record = json.loads(_lines(ledger)[0])
    assert record["args_sanitized"] is None
    assert record["args_sha256"] == receipts.args_sha256({"path": INSIDE_OWNED, "content": SECRET_MARKER})


def test_receipt_sanitized_detail_adds_only_redacted_text(ledger):
    """``sanitized`` adds a bounded, redacted rendering -- never the raw secret."""
    _call(
        "browser_vault_fill",
        {"path": INSIDE_OWNED, "password": SECRET_MARKER, "handle": "vault:item"},
        detail="sanitized",
        ledger=ledger,
    )
    raw = ledger.read_text(encoding="utf-8")
    assert SECRET_MARKER not in raw, "a secret-keyed argument is redacted, not rendered"

    record = json.loads(_lines(ledger)[0])
    assert isinstance(record["args_sanitized"], str)
    rendered = json.loads(record["args_sanitized"])
    assert rendered["password"] == "<redacted>"
    assert rendered["handle"] == "vault:item"


def test_sanitize_args_is_bounded():
    """A huge argument is truncated, not copied."""
    rendered = receipts.sanitize_args({"content": "x" * 5000})
    assert rendered is not None
    assert len(rendered) < 1000
    assert "more chars" in rendered


@pytest.mark.parametrize("detail", ["hash", "sanitized", "nonsense", ""])
def test_receipt_detail_modes_are_closed(detail, ledger):
    """Only the two declared detail modes change the record; anything else is the default."""
    outcome = _call("write_file", {"path": INSIDE_OWNED, "content": SECRET_MARKER}, detail=detail, ledger=ledger)
    record = outcome.receipt
    assert record["args_sanitized"] is None or detail == "sanitized"


def test_receipts_are_records_not_state(ledger):
    """Nothing in the decision path reads the ledger back."""
    assert not hasattr(gate, "read_receipts")
    assert not hasattr(manifest, "read_receipts")
    _call("write_file", {"path": INSIDE_OWNED, "content": "x"}, ledger=ledger)
    assert len(receipts.read_receipts(ledger)) == 1
