# Changelog

## Unreleased — public distribution boundary (2026-09-22)

The distribution now ships the LCM context engine and nothing else, and every public identity it
carries is the org's.

**Boundary**

- `pyproject.toml` registers exactly one entry point, `protean-lcm`, and packages exactly one
  importable package, `protean_lcm`. `protean-lane-gate` and `protean-resource-budget` are no
  longer installed by any path.
- `plugins/lane_gate/` and `plugins/resource_budget/` are removed from the
  tree on this branch, with their tests. The lane gate now lives in the
  private `aska-digital/internal-lane-gate` repository; the resource-budget
  slice survives in git history, upstream-owned at
  `keeltrace/hermesx-resource-budget` (see `MIGRATION-RECEIPT.md`).
- `plugins/context_engine/lcm/plugin.yaml` carries `name: protean-lcm`, so a directory install and a
  pip install enable and disable under the same name.

**Identity and attribution**

- `plugin.yaml` and the shipped skill declare `author: ASKA Digital`, replacing a false Nous
  Research attribution and the internal codenames then used by the two (since removed)
  unpackaged slices.
- `pyproject.toml` `authors` and both `[project.urls]` entries point at the org.
- `README.md` install commands, `install.sh`, and `INSTALL-PROOF.md` use the canonical org URLs.
- The provenance section states that this plugin is not by Nous Research, not by Voltropy PBC, and
  not by Stephen Schoettler, that no upstream author endorses it, and (historical) what the
  removed vendored resource-budget slice was.

**Packaging and compatibility**

- `protean_lcm/plugin.yaml` ships in the wheel through package data, so the directory drop-in and
  the installed package carry the same manifest.
- The shipped skill is renamed `hermes-lcm` to `protean-lcm`. The plugin no longer ships a skill
  named after another author's product.
- `requires-python` is `>=3.11,<3.14`, the range Hermes Agent 0.21 supports, and the README
  requirement line matches it.
- `INSTALL-PROOF.md` is re-run at this head: one entry point, the engine adopted, four tools, the
  suite green, and no personal absolute paths.

**Metadata**

- `catalog/protean-lcm.yaml` records the catalog entry this tree is submitted as. It is a draft:
  `sha` and `version` are set when the release is cut.

**Files:** `pyproject.toml`, `README.md`, `CHANGELOG.md`, `INSTALL-PROOF.md`, `install.sh`,
`plugins/context_engine/lcm/{plugin.yaml,README.md,__init__.py,recall.py}`,
`plugins/context_engine/lcm/skills/protean-lcm/**`, `plugins/lane_gate/plugin.yaml`,
`plugins/lane_gate/skills/lane-discipline/SKILL.md`, `plugins/resource_budget/plugin.yaml`,
`tests/resource_budget/test_resource_budget_provenance.py`, `catalog/protean-lcm.yaml`.

## Unreleased — resource budget slice (2026-09-22)

Added the optional `protean-resource-budget` ingredient: a vendored, hash-pinned copy of `keeltrace/hermesx-resource-budget` at commit `354deb3dc39ef1a39d294727742538da7afea499` (MIT) plus a local adapter that is inert by default (`mode: off` registers nothing), clamps unrecognized modes to `observe`, wraps the decision path in a fail-closed handler, and ships its full provenance in `PROVENANCE-RESOURCE-BUDGET.md`. Tests: `tests/resource_budget/`. No existing file outside `pyproject.toml`, `README.md`, and this changelog changed; the lane gate and context engine are untouched.

This slice is kept in the repository but is not part of the `protean-lcm` distribution, see the
entry above. (Historical: the slice has since been removed from the tree on this branch;
see `MIGRATION-RECEIPT.md`. Upstream `keeltrace/hermesx-resource-budget` was read, never
written.)

## 0.1.0 — Initial release (2026-09-15)

Initial release of protean-lcm, an opt-in LCM DAG context engine plugin for Hermes Agent.

**What:** Add CHANGELOG.md and update README.md to reflect Protean product family naming.

**Why:** Provide release history surface and clarify the plugin's place within the Protean family.

**Files:** CHANGELOG.md, README.md.
