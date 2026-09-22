# Changelog

## 0.1.0 — Initial release (2026-09-15)

Initial release of protean-lcm, an opt-in LCM DAG context engine plugin for Hermes Agent.

**What:** Add CHANGELOG.md and update README.md to reflect Protean product family naming.

**Why:** Provide release history surface and clarify the plugin's place within the Protean family.

**Files:** CHANGELOG.md, README.md.

## 0.1.0: Resource Budget slice (2026-09-22)

Added the optional `protean-resource-budget` ingredient: a vendored, hash-pinned copy of `keeltrace/hermesx-resource-budget` at commit `354deb3dc39ef1a39d294727742538da7afea499` (MIT) plus a local adapter that is inert by default (`mode: off` registers nothing), clamps unrecognized modes to `observe`, wraps the decision path in a fail-closed handler, and ships its full provenance in `PROVENANCE-RESOURCE-BUDGET.md`. Tests: `tests/resource_budget/`. No existing file outside `pyproject.toml`, `README.md`, and this changelog changed; the lane gate and context engine are untouched.
