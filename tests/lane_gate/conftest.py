"""Suite setup for the Lane Gate tests.

Two jobs:

* put the repository root on ``sys.path`` so ``plugins.lane_gate`` imports
  without an installed distribution (the layout mirrors the LCM suite, which
  imports ``plugins.context_engine.lcm`` the same way);
* isolate process-global state between tests: the gate's module-level hook
  registry, the manifest resolver, and the three ``PROTEAN_LANE_*`` environment
  variables.

``HERMES_HOME`` and the host plugin-manager reset already come from
``tests/conftest.py``.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HERE = Path(__file__).resolve().parent
for _entry in (str(_REPO_ROOT), str(_HERE)):
    if _entry not in sys.path:
        sys.path.insert(0, _entry)

from plugins.lane_gate import config as lane_config  # noqa: E402
from plugins.lane_gate import manifest as lane_manifest  # noqa: E402

import plugins.lane_gate as lane_gate  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_lane_gate_state(monkeypatch):
    """Reset gate state and clear the lane environment around every test."""
    for name in ("PROTEAN_LANE_MANIFEST", "PROTEAN_LANE_RECEIPTS", "PROTEAN_LANE_RECEIPT_DETAIL"):
        monkeypatch.delenv(name, raising=False)
    lane_gate._reset_for_tests()
    yield
    lane_gate._reset_for_tests()
    lane_manifest.reset_default_resolver()


@pytest.fixture
def lane_settings(monkeypatch):
    """Patch config resolution for registration tests (no Hermes config file needed)."""

    def _apply(mode: str = "advisory", **values) -> lane_config.LaneGateConfig:
        settings = lane_config.LaneGateConfig({"mode": mode, **values})
        monkeypatch.setattr(lane_config, "load_lane_gate_config", lambda config=None: settings)
        return settings

    return _apply


@pytest.fixture
def ledger(tmp_path):
    """A per-test receipt ledger path."""
    return tmp_path / "lane_gate" / "receipts.jsonl"


@pytest.fixture
def manifest_file(tmp_path):
    """Write a manifest fixture into the test's tmp dir and return its path."""

    def _write(data, name: str = "lane-manifest.json") -> Path:
        import json

        target = tmp_path / name
        target.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        return target

    return _write
