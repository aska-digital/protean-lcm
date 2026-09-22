# Migration receipt — lane gate and resource budget leave protean-lcm

Date: 2026-09-22 (UTC). Implementer: mozi (stage 4, implementation).
Branch: `fix/lcm-public-distribution` (PR #10 head, base `main`).
Prior head: `dad94ed54cad56f0b81d886c178325e78091331e42`.

## What this commit does

Removes the lane-gate slice and the resource-budget slice from this branch so
the distribution ships the LCM context engine only:

- `plugins/lane_gate/**`, `tests/lane_gate/**`, `PROVENANCE-LANE-GATE.md`
- `plugins/resource_budget/**`, `tests/resource_budget/**`,
  `PROVENANCE-RESOURCE-BUDGET.md`

Adds this receipt. No other path is touched: `README.md`, `CHANGELOG.md`,
`INSTALL-PROOF.md`, `catalog/`, `install.sh`, `pyproject.toml`,
`plugins/context_engine/**`, `tests/plugins/**`, and `tests/conftest.py` are
byte-identical to the prior head. (Stale mentions of the removed slices may
remain in `README.md`/`CHANGELOG.md`/`INSTALL-PROOF.md`; updating them is a
follow-up docs decision, deliberately out of this commit's owned set.)

## Where the lane gate went

Preserved with filtered history in the private internal repository
`aska-digital/internal-lane-gate` (`history/lane-gate-base` =
path-filtered `plugins/lane_gate/**`, `tests/lane_gate/**`,
`PROVENANCE-LANE-GATE.md` from PR #10 head `dad94ed`, plus README/receipt).

## Resource-budget history preservation record (nothing deleted unpreserved)

Every removed byte remains recoverable in this repository's git history; the
removal commit deletes working-tree paths only:

- `268118e feat(resource-budget): add vendored resource-budget slice v0.1` —
  added `plugins/resource_budget/` (`__init__.py`, `config.py`,
  `hxrb_runtime.py`, `plugin.yaml`, `UPSTREAM-LICENSE.md`),
  `tests/resource_budget/` (`conftest.py`,
  `test_resource_budget_authority.py`, `test_resource_budget_config.py`,
  `test_resource_budget_gate.py`, `test_resource_budget_provenance.py`),
  and `PROVENANCE-RESOURCE-BUDGET.md` (plus CHANGELOG/README/pyproject
  entries that stay).
- `1fb34fe fix: ship one plugin, under the org's name` — touched
  `plugins/resource_budget/plugin.yaml` and
  `tests/resource_budget/test_resource_budget_provenance.py` among distribution
  renames.

Upstream ownership: `keeltrace/hermesx-resource-budget` at commit
`354deb3dc39ef1a39d294727742538da7afea499` (MIT, Copyright (c) 2026 KeelTrace
contributors). The vendored runtime (`hxrb_runtime.py`, 49,813 bytes) and the
license text (`UPSTREAM-LICENSE.md`, 1,079 bytes) were byte-identical to
upstream at that pin; pin table and verification commands survive in history
via `PROVENANCE-RESOURCE-BUDGET.md` at any pre-removal commit.

## Explicit no-write decision

No write of any kind was made to `keeltrace/hermesx-resource-budget`: no
commit, push, fork, PR, issue, or tag. Upstream was read-only before this
migration and remains untouched after it. No merge, tag, release, catalog PR,
public announcement, or direct push to `main` was made from this work.

## Test boundary

Remaining suite: `tests/plugins/context_engine/**` via
`sh scripts/run_tests.sh <hermes-agent-checkout>`; the removed
`tests/lane_gate/**` and `tests/resource_budget/**` no longer exist in this
tree. `tests/conftest.py` is generic (Hermes home/plugin-state isolation) and
unchanged.
