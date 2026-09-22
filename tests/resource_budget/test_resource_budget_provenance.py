"""Provenance and wording tests for the Resource Budget slice (§1, §7).

Pins the two vendored files to the upstream commit hashes, checks the
provenance record carries the pin/holder/nature statements and the verbatim MIT
notice, checks the plugin.yaml floor (no manifest_version, no config_schema),
and audits every boundary word in the slice's own text files.
"""

from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from conftest import (  # noqa: E402
    CHANGELOG_PATH,
    PLUGIN_DIR,
    PLUGIN_YAML_PATH,
    PROVENANCE_PATH,
    README_PATH,
    RUNTIME_PATH,
    S_A,
    S_A_PLAIN,
    S_B,
    S_C,
    S_D_FILE_FRAGMENT,
    S_D_PLAIN,
    FALSE_FLAGS,
    SLICE_TEXT_PATHS,
    UPSTREAM_LICENSE_PATH,
    unapproved_boundary_occurrences,
)

import plugins.resource_budget as resource_budget  # noqa: E402
from plugins.resource_budget import hxrb_runtime  # noqa: E402
from plugins.resource_budget.config import SettingsShim  # noqa: E402

# The locked pins (leo-arch §1.1 / §1.4, independently re-verified by the
# dispatcher against the read-only upstream checkout).
RUNTIME_SHA256 = "74761c84aa043cc72b207481296d0467cb08bfad700c0872f035705aceb2e293"
RUNTIME_BYTES = 49813
LICENSE_SHA256 = "385aa23e6b7b7945458f4077849232802a95fbf06c45aed13dd7283edbcca9f7"
LICENSE_BYTES = 1079
UPSTREAM_COMMIT = "354deb3dc39ef1a39d294727742538da7afea499"
UPSTREAM_REPO = "keeltrace/hermesx-resource-budget"
COPYRIGHT_LINE = "Copyright (c) 2026 KeelTrace contributors"
MIT_NOTICE_LINES = 21


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _provenance_text() -> str:
    return PROVENANCE_PATH.read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# T5/T6: file pins
# --------------------------------------------------------------------------- #


def test_runtime_file_hash_matches_pin():
    assert _sha256(RUNTIME_PATH) == RUNTIME_SHA256
    assert RUNTIME_PATH.stat().st_size == RUNTIME_BYTES


def test_upstream_license_file_hash_matches_pin():
    assert _sha256(UPSTREAM_LICENSE_PATH) == LICENSE_SHA256
    assert UPSTREAM_LICENSE_PATH.stat().st_size == LICENSE_BYTES


def test_runtime_imports_expose_the_pinned_identity():
    assert hxrb_runtime.PLUGIN_ID == "hermesx-resource-budget"
    assert hxrb_runtime.PLUGIN_VERSION == "1.0.0"
    assert hxrb_runtime.SUPPORTED_TOOL_NAMES == {"terminal"}
    assert hxrb_runtime.VALID_MODES == {
        "observe",
        "block_unattended",
        "block_all",
        "pressure_only",
    }
    assert "off" not in hxrb_runtime.VALID_MODES  # divergence G4: off is adapter-only


def test_no_upstream_file_outside_the_two_pins_was_vendored():
    """Exactly the 5 locked files live in plugins/resource_budget."""
    names = {p.name for p in PLUGIN_DIR.iterdir() if p.name != "__pycache__"}
    assert names == {
        "UPSTREAM-LICENSE.md",
        "__init__.py",
        "config.py",
        "hxrb_runtime.py",
        "plugin.yaml",
    }


# --------------------------------------------------------------------------- #
# T7: provenance record contents
# --------------------------------------------------------------------------- #


def test_provenance_record_pins_commit_holder_and_nature():
    text = _provenance_text()

    assert UPSTREAM_REPO in text
    assert UPSTREAM_COMMIT in text
    assert "license:     MIT" in text
    assert COPYRIGHT_LINE in text
    assert "Derived from keeltrace/hermesx-resource-budget @ 354deb3" in text
    assert "read-only, never written" in text

    # The §1.1 claim set, in the mandated direction.
    assert "Upstream source lines ARE copied" in text
    assert "byte-identical" in text
    assert "adapter is original local code" in text
    assert "other upstream file enters this repository" in text

    # Divergences G1-G6, locked gaps.
    for gap in ("G1", "G2", "G3", "G4", "G5", "G6"):
        assert f"**{gap}" in text or f"- **{gap}" in text

    # The nerve-style "no line copied" claim must NEVER appear here; this
    # slice makes the opposite claim instead.
    assert "no upstream source line was copied" not in text.lower()


def test_provenance_record_carries_the_file_pin_table():
    text = _provenance_text()
    for value in (
        RUNTIME_SHA256,
        LICENSE_SHA256,
        "5b48228e54bc0a948da28380174492ddff888ee5",
        "1261258246fcc960647d4c772090d4017ec3db33",
        "49,813",
        "1,079",
    ):
        assert value in text


def test_provenance_adaptation_map_covers_every_local_behaviour():
    text = _provenance_text()
    for behaviour in (
        "Inert registration",
        "Mode clamp",
        "Fail-closed wrapper",
        "Config shim",
    ):
        assert behaviour in text
    assert "original local code, no upstream line" in text


def test_mit_notice_is_verbatim():
    license_text = UPSTREAM_LICENSE_PATH.read_text(encoding="utf-8")
    provenance_text = _provenance_text()

    notice_lines = license_text.rstrip("\n").split("\n")
    assert len(notice_lines) == MIT_NOTICE_LINES
    assert notice_lines[2] == COPYRIGHT_LINE

    # The fenced block in the provenance record matches byte-for-byte.
    match = re.search(r"```\n(MIT License\n.*?)```", provenance_text, re.S)
    assert match is not None, "no fenced MIT notice found"
    assert match.group(1).rstrip("\n") + "\n" == license_text


def test_provenance_carries_the_recognized_categories_and_gap_examples():
    text = _provenance_text()
    for category in (
        "Ollama model",
        "Git LFS",
        "Hugging Face CLI downloads",
        "raw/bulk `dd` device I/O",
        "bulk OS package upgrades",
        "proven on-disk Hermes profile aliases",
    ):
        assert category in text
    for gap_example in (
        "`pip install`",
        "`npm install`",
        "`cargo build`",
        "`make`",
        "`python3 x.py`",
        "`node server.js`",
        "`bash script.sh`",
    ):
        assert gap_example in text
    assert "is **allowed**" in text or "is **ALLOWED**" in text or "**allowed**" in text


def test_readme_and_changelog_carry_the_slice():
    readme = README_PATH.read_text(encoding="utf-8")
    changelog = CHANGELOG_PATH.read_text(encoding="utf-8")
    assert "Resource budget (optional ingredient)" in readme
    assert S_A in readme
    assert "protean-resource-budget" in readme
    assert "PROVENANCE-RESOURCE-BUDGET.md" in readme
    assert "protean-resource-budget" in changelog or "Resource Budget" in changelog


# --------------------------------------------------------------------------- #
# plugin.yaml shape (§8.4, G5)
# --------------------------------------------------------------------------- #


def test_plugin_yaml_has_no_manifest_version():
    text = PLUGIN_YAML_PATH.read_text(encoding="utf-8")
    assert not re.search(r"^\s*manifest_version", text, re.M)


def test_plugin_yaml_matches_the_lane_gate_shape():
    text = PLUGIN_YAML_PATH.read_text(encoding="utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[0] == "name: resource-budget"
    assert lines[1] == "version: 0.1.0"
    assert lines[2] == (
        'description: "Best-effort Hermes terminal policy guard with '
        'privacy-preserving aggregate CPU, memory, I/O, and disk headroom for '
        'recognized host-heavy invocations."'
    )
    assert lines[3] == 'author: "Mozi"'
    assert lines[4] == "license: MIT"
    assert lines[5] == "provides_hooks:"
    assert lines[6] == "  - pre_tool_call"
    assert lines[7] == "provides_tools:"
    assert lines[8] == "  - resource_budget_status"
    assert "config_schema" not in text  # divergence G5
    assert len(lines) == 9


# --------------------------------------------------------------------------- #
# W1: boundary language (locked §7)
# --------------------------------------------------------------------------- #


def test_boundary_language_is_verbatim():
    prov = _provenance_text()
    readme = README_PATH.read_text(encoding="utf-8")
    runtime = RUNTIME_PATH.read_text(encoding="utf-8")

    assert S_A in prov
    assert S_A in readme
    assert S_B in runtime  # inside the hash-pinned file
    assert S_C in prov
    assert S_D_FILE_FRAGMENT in runtime  # inside the vendored block message

    # S-d survives verbatim in the live directive the host sees.
    assert S_D_PLAIN in resource_budget._FALLBACK_MESSAGE


def test_only_approved_boundary_words():
    offenders = []
    for path in SLICE_TEXT_PATHS:
        if not path.exists():
            continue
        for offset, word in unapproved_boundary_occurrences(
            path.read_text(encoding="utf-8")
        ):
            offenders.append((path.name, word, offset))
    assert offenders == []


def test_status_payload_reports_no_sandbox_and_no_hard_caps(enabled_ctx):
    ctx = enabled_ctx({"mode": "observe"})
    payload = ctx.status_json()
    assert payload["enforcement"]["sandbox"] is False
    for flag in FALSE_FLAGS[1:]:
        assert payload["enforcement"][flag] is False
    assert payload["enforcement"]["tool_gate"] is True
    assert payload["enforcement"]["supported_tool_names"] == ["terminal"]


def test_status_telemetry_is_labeled_approximate(enabled_ctx):
    ctx = enabled_ctx({"mode": "observe"})
    payload = ctx.status_json()
    assert payload["telemetry"]["counts_are_approximate"] is True
    assert payload["telemetry"]["enforcement_depends_on_counts"] is False


def test_status_privacy_contract_is_truthful(enabled_ctx):
    ctx = enabled_ctx({"mode": "observe"})
    privacy = ctx.status_json()["privacy"]
    assert privacy["aggregate_host_signals_only"] is True
    assert privacy["command_contents_persisted"] is False
    for key in ("process_enumeration", "network_inspection", "background_sampling"):
        assert privacy[key] is False
