"""Acceptance tests 3-4 plus manifest unit coverage: schema closure, binding, tamper."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.lane_gate import gate, manifest
from support import LANE_ROOT, binding_of, fixture_json, valid_manifest, valid_manifest_dict

INSIDE_OWNED = f"{LANE_ROOT}/plugins/lane_gate/gate.py"

BAD_FIXTURES = (
    "lane-manifest.missing-key.json",
    "lane-manifest.unknown-key.json",
    "lane-manifest.empty-lane-id.json",
    "lane-manifest.bad-schema.json",
)


# --------------------------------------------------------------------------- #
# 3. closed schema
# --------------------------------------------------------------------------- #


def test_manifest_closed_schema(tmp_path, ledger):
    """Missing key, unknown key, empty lane_id and a bad schema each fail the manifest."""
    for name in BAD_FIXTURES:
        data = fixture_json(name)
        with pytest.raises(manifest.ManifestInvalid) as failure:
            manifest.LaneManifest.from_obj(data, source=name)
        assert failure.value.reason == "manifest_invalid"

        # ... and through the resolver, exactly as the hook sees it.
        target = tmp_path / name
        target.write_text(json.dumps(data), encoding="utf-8")
        resolver = manifest.ManifestResolver(target)
        assert resolver.binding().status == "invalid"

    # In enforce a mutating call is denied and no exception escapes the hook.
    target = tmp_path / BAD_FIXTURES[1]
    resolver = manifest.ManifestResolver(target)
    outcome = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": INSIDE_OWNED, "content": "x"},
        mode="enforce",
        manifest=resolver.binding(),
        receipts_path=ledger,
    )
    assert (outcome.decision, outcome.reason) == ("APPROVAL", "manifest_invalid")
    assert outcome.directive is not None and outcome.directive["action"] == "block"

    advisory = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": INSIDE_OWNED, "content": "x"},
        mode="advisory",
        manifest=resolver.binding(),
        receipts_path=ledger,
    )
    assert advisory.directive is None


def test_manifest_schema_is_closed_and_exact(tmp_path):
    """Each field is type-checked and unknown keys are refused, not ignored."""
    good = valid_manifest_dict()

    with pytest.raises(manifest.ManifestInvalid, match="unknown key"):
        manifest.LaneManifest.from_obj({**good, "typo_owned_write_globs": []})
    with pytest.raises(manifest.ManifestInvalid, match="missing required key"):
        manifest.LaneManifest.from_obj({key: value for key, value in good.items() if key != "external_writes"})
    with pytest.raises(manifest.ManifestInvalid, match="external_writes"):
        manifest.LaneManifest.from_obj({**good, "external_writes": "false"})
    with pytest.raises(manifest.ManifestInvalid, match="owned_write_globs"):
        manifest.LaneManifest.from_obj({**good, "owned_write_globs": "not-a-list"})
    with pytest.raises(manifest.ManifestInvalid, match="forbidden_tools\\[0\\]"):
        manifest.LaneManifest.from_obj({**good, "forbidden_tools": [""]})

    loaded = manifest.LaneManifest.from_obj(good)
    assert loaded.lane_id == "mozi-nerve-lane-gate"
    assert loaded.external_writes is False
    assert len(loaded.sha256) == 64
    assert set(loaded.as_dict()) == set(manifest.REQUIRED_KEYS)


def test_manifest_hash_is_content_bound(tmp_path):
    """``manifest_sha256`` is sha256 over canonical JSON: formatting is free, content is not."""
    data = valid_manifest_dict()
    pretty = tmp_path / "pretty.json"
    compact = tmp_path / "compact.json"
    pretty.write_text(json.dumps(data, indent=4, sort_keys=True), encoding="utf-8")
    compact.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")

    first = manifest.load_manifest_file(pretty)
    second = manifest.load_manifest_file(compact)
    assert first.sha256 == second.sha256 == manifest.sha256_hex(manifest.validate_manifest(data))
    assert first.sha256 == manifest.LaneManifest.from_obj(data).sha256


# --------------------------------------------------------------------------- #
# 4. tamper detection
# --------------------------------------------------------------------------- #


def test_manifest_tamper_detected(tmp_path, ledger):
    """A manifest edited after load is ``tampered``: manifest_invalid, denied in enforce."""
    target = tmp_path / "lane-manifest.json"
    target.write_text(json.dumps(valid_manifest_dict()), encoding="utf-8")

    resolver = manifest.ManifestResolver(target)
    first = resolver.binding()
    assert first.status == "ok"
    assert first.sha256 == first.manifest.sha256

    # A whitespace-only rewrite keeps the binding: the digest is over canonical JSON.
    target.write_text(json.dumps(valid_manifest_dict(), indent=8), encoding="utf-8")
    assert resolver.binding().status == "ok"

    # A semantic edit is detected on the next call.
    tampered_data = valid_manifest_dict(lane_id="someone-else", owned_write_globs=["/lane/**"])
    target.write_text(json.dumps(tampered_data), encoding="utf-8")
    second = resolver.binding()
    assert second.status == "tampered"
    assert second.sha256 != first.sha256
    assert second.manifest.sha256 == first.sha256, "the bound manifest is still the one that was loaded"

    outcome = gate.evaluate_tool_call(
        tool="write_file",
        args={"path": INSIDE_OWNED, "content": "x"},
        mode="enforce",
        manifest=second,
        receipts_path=ledger,
    )
    assert (outcome.decision, outcome.reason) == ("APPROVAL", "manifest_invalid")
    assert outcome.directive["action"] == "block"


def test_manifest_missing_and_unreadable(tmp_path, ledger):
    """A missing or unparseable manifest is manifest_invalid, never an allow."""
    resolver = manifest.ManifestResolver(tmp_path / "absent.json")
    absent = resolver.binding()
    assert absent.status == "missing"
    assert absent.sha256 == manifest.ABSENT_SHA256

    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    resolver.set_path(broken)
    invalid = resolver.binding()
    assert invalid.status == "invalid"
    assert len(invalid.sha256) == 64

    outcome = gate.evaluate_tool_call(
        tool="read_file",
        args={"path": INSIDE_OWNED},
        mode="enforce",
        manifest=invalid,
        receipts_path=ledger,
    )
    assert (outcome.decision, outcome.reason) == ("APPROVAL", "manifest_invalid")


def test_resolver_env_binding(tmp_path, monkeypatch):
    """``PROTEAN_LANE_MANIFEST`` binds the resolver without any config file."""
    target = tmp_path / "env-manifest.json"
    target.write_text(json.dumps(valid_manifest_dict()), encoding="utf-8")
    monkeypatch.setenv("PROTEAN_LANE_MANIFEST", str(target))

    resolver = manifest.ManifestResolver()
    binding = resolver.binding()
    assert binding.status == "ok"
    assert binding.manifest.source == str(target)

    monkeypatch.delenv("PROTEAN_LANE_MANIFEST")
    assert manifest.ManifestResolver().binding().status == "missing"


# --------------------------------------------------------------------------- #
# path extraction and glob matching (the mechanics the decision procedure rests on)
# --------------------------------------------------------------------------- #


def test_extract_paths_recognizes_path_like_arguments():
    paths = manifest.extract_paths(
        {
            "path": f"{LANE_ROOT}/plugins/lane_gate/gate.py",
            "workdir": f"{LANE_ROOT}/plugins",
            "paths": [f"{LANE_ROOT}/tests/lane_gate/a.py", f"{LANE_ROOT}/tests/lane_gate/a.py"],
            "url": "https://example.invalid/api/attach/file.txt",
            "content": f"{LANE_ROOT}/not-a-path-field.txt",
            "count": 3,
        }
    )
    assert f"{LANE_ROOT}/plugins/lane_gate/gate.py" in paths
    assert f"{LANE_ROOT}/plugins" in paths
    assert paths.count(f"{LANE_ROOT}/tests/lane_gate/a.py") == 1, "paths are de-duplicated"
    assert "/api/attach/file.txt" in paths, "a URL contributes its path component"
    assert not any("not-a-path-field" in path for path in paths), "content is never read as a path"


def test_extract_paths_normalizes_relative_paths(tmp_path):
    paths = manifest.extract_paths({"path": "plugins/lane_gate/gate.py"}, base=str(tmp_path))
    assert paths == (str(tmp_path / "plugins/lane_gate/gate.py"),)


def test_glob_matching_semantics():
    owned = valid_manifest_dict()["owned_write_globs"]
    assert manifest.matches_any(owned, f"{LANE_ROOT}/plugins/lane_gate/gate.py")
    assert manifest.matches_any(owned, f"{LANE_ROOT}/plugins/lane_gate/skills/lane-discipline/SKILL.md")
    assert not manifest.matches_any(owned, f"{LANE_ROOT}/plugins/lane_gate")
    assert not manifest.matches_any(owned, "/tmp/elsewhere.txt")

    forbidden = valid_manifest_dict()["forbidden_globs"]
    assert manifest.forbidden_hits(valid_manifest(), [f"{LANE_ROOT}/LICENSE"]) == (f"{LANE_ROOT}/LICENSE",)
    assert manifest.forbidden_hits(valid_manifest(), [INSIDE_OWNED]) == ()


def test_binding_helper_accepts_a_manifest_or_a_binding():
    bound = valid_manifest()
    assert gate.decide(tool="read_file", args={"path": INSIDE_OWNED}, manifest=bound, mode="enforce")["decision"] == "ALLOW"
    assert gate.decide(tool="read_file", args={"path": INSIDE_OWNED}, manifest=binding_of(bound), mode="enforce")["decision"] == "ALLOW"
    assert gate.decide(tool="read_file", args={"path": INSIDE_OWNED}, manifest=None, mode="enforce")["reason"] == "manifest_invalid"


def test_manifest_invalid_is_an_exception_type_not_a_decision():
    """The gate reports manifest_invalid as a reason; the module raises a typed error."""
    assert manifest.ManifestInvalid.reason == "manifest_invalid"
    assert "manifest_invalid" in gate.REASONS
    with pytest.raises(manifest.ManifestInvalid):
        manifest.LaneManifest.from_obj({"schema": manifest.SCHEMA})


def test_fixture_manifest_matches_the_owned_set():
    """The valid fixture describes this very slice: the owned files are all inside it."""
    owned = valid_manifest()
    for path in (
        "plugins/lane_gate/__init__.py",
        "plugins/lane_gate/gate.py",
        "plugins/lane_gate/manifest.py",
        "plugins/lane_gate/receipts.py",
        "plugins/lane_gate/config.py",
        "plugins/lane_gate/skills/lane-discipline/SKILL.md",
        "tests/lane_gate/test_lane_gate_gate.py",
        "pyproject.toml",
        "PROVENANCE-LANE-GATE.md",
    ):
        assert manifest.matches_any(owned.owned_write_globs, f"{LANE_ROOT}/{path}"), path


def test_manifest_file_round_trip(tmp_path):
    """A manifest written by ops loads, validates and binds."""
    target: Path = tmp_path / "lane-manifest.json"
    target.write_text(json.dumps(valid_manifest_dict(), indent=2), encoding="utf-8")
    loaded = manifest.load_manifest_file(target)
    assert loaded.lane_id == valid_manifest_dict()["lane_id"]
    assert loaded.sha256 == manifest.sha256_hex(manifest.validate_manifest(valid_manifest_dict()))
