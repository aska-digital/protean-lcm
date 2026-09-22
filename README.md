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

Two other slices live in this repository and are not part of the distribution: `plugins/lane_gate/` (per-worker lane governance, which needs lane manifests that this package has no use for) and `plugins/resource_budget/` (a vendored copy of another author's plugin). Neither is packaged and neither is registered by an entry point, so no install path ships them. They stay in the tree pending a separate home, which is a repository-level decision.

## Provenance

This plugin is by ASKA Digital, not by Nous Research, not by Voltropy PBC, and not by Stephen Schoettler.

The architecture follows the LCM paper, "LCM: Lossless Context Management" (Clint Ehrlich and Theodore Blackman, Voltropy PBC, arXiv:2605.04050, 14 February 2026). The running Hermes plugin that first applied the idea is [stephenschoettler/hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) (MIT). protean-lcm draws from its design (DAG compaction with lossless pointers, a plugin-local SQLite store, bounded recall tools) but shares no code with it: a line-level comparison found zero copied source lines and no shared text outside the class and method names the Hermes `ContextEngine` interface requires. Neither upstream author has reviewed or endorses this plugin.

The `protean-resource-budget` slice kept in this repository is a different case: it is a vendored, hash-pinned, byte-identical copy of one file from [keeltrace/hermesx-resource-budget](https://github.com/keeltrace/hermesx-resource-budget) at commit `354deb3dc39ef1a39d294727742538da7afea499` (MIT, Copyright (c) 2026 KeelTrace contributors), wrapped in an original local adapter. The upstream license text ships beside the code in `plugins/resource_budget/UPSTREAM-LICENSE.md`; the pin table and the verification commands are in `PROVENANCE-RESOURCE-BUDGET.md`. KeelTrace does not endorse or maintain this slice, and it is not part of this distribution.

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
- `plugins/lane_gate/`, `plugins/resource_budget/`, `tests/lane_gate/`, `tests/resource_budget/` are in the repository but not in the distribution, see What ships

The engine source keeps the in-tree path (`plugins/context_engine/lcm/`), so the same directory also works as a directory plugin dropped into a Hermes source tree, and the distribution maps it to the importable `protean_lcm` package.

## Tests

The suite is an integration suite. It drives the real Hermes context-engine loader, so it runs against a Hermes Agent checkout:

```sh
sh scripts/run_tests.sh /path/to/hermes-agent
```

The script symlinks the checkout's context-engine loader into a temporary overlay together with this repository's engine, then runs pytest from the repository root with that overlay first on the path. The checkout is not modified.

## License

MIT. See LICENSE. Upstream inspiration attributed above.

## Resource budget (optional ingredient)

This slice is not part of the `protean-lcm` distribution: it is not packaged and no entry point
registers it, so no install path ships it. It is kept in this repository pending a separate home,
which is a repository-level decision. See What ships above.

`protean-resource-budget` is an
opt-in Hermes plugin that guards the native `terminal` tool
against recognized host-heavy invocations (model pulls, container builds, bulk
package upgrades, gateway restarts) using aggregate CPU, memory, I/O, and disk
headroom. It is a vendored, hash-pinned copy of
`keeltrace/hermesx-resource-budget` at commit `354deb3dc39ef1a39d294727742538da7afea499`
(MIT) wrapped in a local adapter; the full provenance record is
[`PROVENANCE-RESOURCE-BUDGET.md`](PROVENANCE-RESOURCE-BUDGET.md).

From the source's own boundary statement, carried verbatim:

> **Boundary:** this is a best-effort guard for the native Hermes `terminal`
> tool. It is **not a sandbox or complete host-resource security boundary**.
> The classifier recognizes a finite set of executable command forms;
> unrecognized launchers or other execution surfaces can fall outside it. Its
> shell grammar is intentionally **POSIX/Bash-oriented**; Windows PowerShell and
> `cmd.exe` semantics are not modeled or claimed.

Inert by default: unlike the upstream package, this slice registers nothing
until you enable it and set a mode. With the plugin enabled, the mode setting
decides what happens:

```yaml
plugins:
  entries:
    resource-budget:
      settings:
        mode: "off"   # off | observe | block_unattended | block_all | pressure_only
```

An unset or misspelled mode can only mean `off` or `observe`, never a blocking
mode; the gate resolves the setting on every call. Invocations the classifier
does not recognize are allowed, because Hermes owns human authorization for
everything else and a local default-deny layer would duplicate that authority.

Rollback: set `mode: "off"` (inert on the very next call), then
`hermes plugins disable protean-resource-budget` to remove the hook and the
read-only `resource_budget_status` tool. Nothing in Hermes core is touched, the
slice writes no files, and its only state is three approximate telemetry keys
that never drive a decision. See `tests/resource_budget/` for the executable
proofs.
