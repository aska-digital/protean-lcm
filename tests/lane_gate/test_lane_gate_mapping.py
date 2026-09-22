"""Acceptance test 14: the artifact-contract v1 mapping (M1-M4).

Kanban stays the sole lifecycle authority and the handoff file stays
``artifact-contract`` v1 exactly as protean-team defines it.  This slice adds
mapping invariants an auditor can check -- and nothing is written to
protean-team: the fixtures are local files and the checks are pure functions.
"""

from __future__ import annotations

import pytest

from plugins.lane_gate import gate, manifest, receipts
from support import LANE_ROOT, binding_of, fixture_json, valid_manifest

INSIDE_OWNED = f"{LANE_ROOT}/plugins/lane_gate/gate.py"


def _valid_handoff():
    return fixture_json("handoff.valid.json")


# --------------------------------------------------------------------------- #
# 14. M1-M4
# --------------------------------------------------------------------------- #


def test_artifact_contract_mapping():
    """The valid example holds M1-M3; each violating fixture is rejected with its reason."""
    owned = valid_manifest()

    result = manifest.check_mapping(_valid_handoff(), owned)
    assert result["ok"] is True, result
    assert result["M1"]["ok"] and result["M2"]["ok"] and result["M3"]["ok"]

    # M1: a handoff that asks for a rebuild the lane cannot perform.
    regenerate_outside = manifest.check_mapping(fixture_json("handoff.regenerate-outside-owned.json"), owned)
    assert regenerate_outside["ok"] is False
    assert regenerate_outside["M1"]["ok"] is False
    assert f"{LANE_ROOT}/protean-team/choreography/artifact-contract.md" in regenerate_outside["M1"]["regenerate_outside_owned"]
    assert regenerate_outside["M2"]["ok"] is True, "the other invariants are independent"

    # M2: a "frozen" path that the lane may actually write to.
    frozen_inside = manifest.check_mapping(fixture_json("handoff.frozen-inside-owned.json"), owned)
    assert frozen_inside["ok"] is False
    assert frozen_inside["M2"]["ok"] is False
    assert f"{LANE_ROOT}/plugins/lane_gate/config.py" in frozen_inside["M2"]["frozen_inside_owned"]

    # M3: the regenerate and frozen sets overlap (protean-team checker rule 6).
    overlapping = _valid_handoff()
    overlapping["artifacts_not_to_touch"] = [{"path": f"{LANE_ROOT}/tests/lane_gate/test_lane_gate_gate.py"}]
    overlap_result = manifest.check_mapping(overlapping, owned)
    assert overlap_result["ok"] is False
    assert overlap_result["M3"]["ok"] is False
    assert overlap_result["M3"]["regenerate_and_frozen_overlap"] == [
        f"{LANE_ROOT}/tests/lane_gate/test_lane_gate_gate.py"
    ]


def test_mapping_reads_the_contract_v1_fields():
    """The mapping is defined over artifact-contract v1 field names, not a second schema."""
    handoff = _valid_handoff()
    assert handoff["contract_version"] == 1
    for field in ("artifacts_to_regenerate", "artifacts_not_to_touch", "evidence_refs", "expected_artifacts"):
        assert field in handoff
    assert all("path" in entry for entry in handoff["artifacts_to_regenerate"])
    assert all("ref" in entry and "proves" in entry for entry in handoff["evidence_refs"])

    with pytest.raises(ValueError):
        manifest.check_mapping({"artifacts_to_regenerate": "not-a-list"}, valid_manifest())


def test_mapping_uses_the_gate_s_own_glob_semantics():
    """One rule, two enforcement points: the checker and the live gate agree."""
    owned = valid_manifest()
    inside = f"{LANE_ROOT}/tests/lane_gate/test_lane_gate_gate.py"
    assert manifest.matches_any(owned.owned_write_globs, inside)

    # The live gate agrees about the same path.
    outcome = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": inside, "content": "x"},
        mode="enforce",
        manifest=binding_of(owned),
        receipts_path=None,
    )
    assert (outcome.decision, outcome.reason) == ("ALLOW", "in_lane")

    # ... and about a frozen path.
    frozen = f"{LANE_ROOT}/protean-team/choreography/artifact-contract.md"
    assert manifest.forbidden_hits(owned, [frozen]) == (frozen,)
    denied = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": frozen, "content": "x"},
        mode="enforce",
        manifest=binding_of(owned),
        receipts_path=None,
    )
    assert (denied.decision, denied.reason) == ("BLOCK", "forbidden_target")


def test_m4_receipt_citation_convention(ledger):
    """M4: receipts are cited in ``evidence_refs`` as ``lane-receipt/v1 <id> (<tool> -> <decision>/<reason>)``."""
    outcome = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": INSIDE_OWNED, "content": "x"},
        mode="enforce",
        manifest=binding_of(valid_manifest()),
        receipts_path=ledger,
    )
    citation = receipts.format_citation(outcome.receipt, proves="every write of this lane landed inside its owned globs")
    assert citation.startswith(f"lane-receipt/v1 {outcome.receipt['receipt_id']} (write_file -> ALLOW/in_lane)")
    assert citation.endswith("; proves: every write of this lane landed inside its owned globs")

    parsed = receipts.parse_citation(citation)
    assert parsed == {
        "receipt_id": outcome.receipt["receipt_id"],
        "tool": "write_file",
        "decision": "ALLOW",
        "reason": "in_lane",
        "proves": "every write of this lane landed inside its owned globs",
    }

    # The citation the fixture carries in evidence_refs follows the same convention.
    fixture_ref = _valid_handoff()["evidence_refs"][0]["ref"]
    fixture_parsed = receipts.parse_citation(fixture_ref)
    assert fixture_parsed is not None, fixture_ref
    assert fixture_parsed["decision"] == "ALLOW"
    assert fixture_parsed["reason"] == "in_lane"
    assert fixture_parsed["proves"], "M4 requires the achieved state to be named"

    # Anything that is not a lane receipt is not mistaken for one.
    assert receipts.parse_citation("git diff 9d3c4cb..HEAD --stat") is None
    assert receipts.parse_citation("lane-receipt/v1 nothex (write_file -> ALLOW/in_lane)") is None
    assert receipts.parse_citation("lane-receipt/v1 " + "0" * 64 + " (write_file -> MAYBE/in_lane)") is None
