# protean-lcm

Opt-in LCM DAG context engine plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent), shipped as a standalone repository. It installs through the standard Hermes plugin surface and touches no Hermes core files.

Inspired by [stephenschoettler/hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) (MIT), reimplemented as a bounded-recall, opt-in context engine with migration, backup, rollback, and lifecycle tests.

## What it does

When the live context approaches the model's limit, the engine compacts the middle of the conversation into a summary node and leaves a marker message behind. The raw messages are not deleted. Every node records one lineage edge per message it absorbed, so the compacted range can be reconstructed later in bounded pages.

The engine is inert until it is selected with `context.engine: lcm`. Unset, misspelled, or failing to load, Hermes stays on its built-in compressor and the `lcm_*` tools are absent. See `plugins/context_engine/lcm/README.md` for the full engine guide.

## Install

One command installs the plugin into the interpreter Hermes runs in, enables it, and selects the engine:

```sh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/aska-digital/protean-lcm/main/install.sh)"
```

The same three steps, if you prefer to run them yourself:

```sh
pip install git+https://github.com/aska-digital/protean-lcm.git
hermes plugins enable protean-lcm
hermes config set context.engine lcm
```

Notes

- Install into the environment that runs Hermes, which is normally its virtualenv. `install.sh` locates that interpreter and installs there, and `PYTHON=/path/to/venv/bin/python` overrides the choice.
- `pip install` on its own registers the plugin but does not activate it. Hermes plugins are opt-in through `plugins.enabled`, so `hermes plugins enable protean-lcm` is required.
- Requirements: Python 3.11 or newer and below 3.14, the range Hermes Agent 0.21 itself supports, plus a Hermes Agent release that discovers pip plugins through the `hermes_agent.plugins` entry-point group and gates them with `plugins.enabled`. Hermes 0.21 and newer do this.

## What ships

This distribution is the LCM context engine and nothing else. `pip install` registers exactly one plugin, `protean-lcm`, and the wheel carries exactly one manifest, `plugins/context_engine/lcm/plugin.yaml`.

Historical note: this repository previously also carried `plugins/lane_gate/`
(per-worker lane governance) and `plugins/resource_budget/` (a vendored copy
of another author's plugin), unpackaged and unregistered. Both slices were
removed from the tree on this branch (see `MIGRATION-RECEIPT.md`): the lane
gate now lives in the private `aska-digital/internal-lane-gate` repository,
and the resource-budget slice survives only in git history, upstream-owned at
`keeltrace/hermesx-resource-budget`. Neither slice is packaged, installed, or
present here.

## Provenance

This plugin is by ASKA Digital, not by Nous Research, not by Voltropy PBC, and not by Stephen Schoettler.

The architecture follows the LCM paper, "LCM: Lossless Context Management" (Clint Ehrlich and Theodore Blackman, Voltropy PBC, arXiv:2605.04050, 14 February 2026). The running Hermes plugin that first applied the idea is [stephenschoettler/hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) (MIT). protean-lcm draws from its design (DAG compaction with lossless pointers, a plugin-local SQLite store, bounded recall tools) but shares no code with it: a line-level comparison found zero copied source lines and no shared text outside the class and method names the Hermes `ContextEngine` interface requires. Neither upstream author has reviewed or endorses this plugin.

Historical note: this repository previously carried a `protean-resource-budget`
slice, a different case from the engine above: it was a vendored, hash-pinned,
byte-identical copy of one file from [keeltrace/hermesx-resource-budget](https://github.com/keeltrace/hermesx-resource-budget) at commit `354deb3dc39ef1a39d294727742538da7afea499` (MIT, Copyright (c) 2026 KeelTrace contributors), wrapped in an original local adapter. The slice was removed from the tree on this branch (see `MIGRATION-RECEIPT.md`); its full provenance record survives in git history via `PROVENANCE-RESOURCE-BUDGET.md` at any pre-removal commit. Upstream was read, never written. KeelTrace does not endorse or maintain this distribution, and the slice is not part of it.

## Use

Once enabled, the engine is available and selected by `context.engine: lcm`. It adds four agent-facing tools:

- `lcm_search` finds a retained message in the current session
- `lcm_expand` pages one summary node's lineage, or reads a single message by id
- `lcm_page` walks the session's retained messages one bounded page at a time
- `lcm_status` reports store and bound diagnostics

`page_size`, `max_search_results`, and `body_chars` are enforced in the storage layer with hard ceilings. No call returns a whole session.

The recall policy and the exact tool arguments and response shapes ship with the plugin under `plugins/context_engine/lcm/skills/protean-lcm/`.

## Rollback

```sh
hermes config set context.engine compressor
hermes plugins disable protean-lcm   # keeps the package installed
pip uninstall protean-lcm            # removes the package
```

A disabled, missing, or failing plugin leaves Hermes on the built-in compressor. The store is a read-only record of what happened, so nothing needs repairing.

## Layout

- `plugins/context_engine/lcm/` engine, storage, recall, compaction, config, and the recall skill
- `tests/plugins/context_engine/` registration, lifecycle, migration and backup, recall, rollback, compaction, storage
- `tests/conftest.py` per-test isolation for the process-global plugin manager
- `pyproject.toml` packaging and the `hermes_agent.plugins` entry point
- `install.sh` one-command install
- `scripts/run_tests.sh` runs the suite against a Hermes Agent checkout

(Previously the tree also held `plugins/lane_gate/`,
`plugins/resource_budget/`, `tests/lane_gate/`, and
`tests/resource_budget/`; all four were removed on this branch, see
`MIGRATION-RECEIPT.md`. They were never in the distribution.)

The engine source keeps the in-tree path (`plugins/context_engine/lcm/`), so the same directory also works as a directory plugin dropped into a Hermes source tree, and the distribution maps it to the importable `protean_lcm` package.

## Tests

The suite is an integration suite. It drives the real Hermes context-engine loader, so it runs against a Hermes Agent checkout:

```sh
sh scripts/run_tests.sh /path/to/hermes-agent
```

The script symlinks the checkout's context-engine loader into a temporary overlay together with this repository's engine, then runs pytest from the repository root with that overlay first on the path. The checkout is not modified.

## License

MIT. See LICENSE. Upstream inspiration attributed above.

## Resource budget (historical — slice removed)

The `protean-resource-budget` optional-ingredient slice no longer lives in
this tree: it was removed on this branch (see `MIGRATION-RECEIPT.md`). It was
an opt-in Hermes plugin guarding the native `terminal` tool, a vendored,
hash-pinned copy of `keeltrace/hermesx-resource-budget` at commit
`354deb3dc39ef1a39d294727742538da7afea499` (MIT) wrapped in a local adapter;
upstream was read, never written. The full record survives in git history via
`PROVENANCE-RESOURCE-BUDGET.md` at any pre-removal commit. It was never part
of the `protean-lcm` distribution.
