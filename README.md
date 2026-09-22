# protean-lcm

Opt-in LCM DAG context engine plugin for [Hermes Agent](https://github.com/NousResearch/hermes-agent), shipped as a standalone repository. It installs through the standard Hermes plugin surface and touches no Hermes core files.

Inspired by [stephenschoettler/hermes-lcm](https://github.com/stephenschoettler/hermes-lcm) (MIT), reimplemented as a bounded-recall, opt-in context engine with migration, backup, rollback, and lifecycle tests.

## What it does

When the live context approaches the model's limit, the engine compacts the middle of the conversation into a summary node and leaves a marker message behind. The raw messages are not deleted. Every node records one lineage edge per message it absorbed, so the compacted range can be reconstructed later in bounded pages.

The engine is inert until it is selected with `context.engine: lcm`. Unset, misspelled, or failing to load, Hermes stays on its built-in compressor and the `lcm_*` tools are absent. See `plugins/context_engine/lcm/README.md` for the full engine guide.

## Install

One command installs the plugin into the interpreter Hermes runs in, enables it, and selects the engine:

```sh
sh -c "$(curl -fsSL https://raw.githubusercontent.com/ahrazzle/protean-lcm/main/install.sh)"
```

The same three steps, if you prefer to run them yourself:

```sh
pip install git+https://github.com/ahrazzle/protean-lcm.git
hermes plugins enable protean-lcm
hermes config set context.engine lcm
```

Notes

- Install into the environment that runs Hermes, which is normally its virtualenv. `install.sh` locates that interpreter and installs there, and `PYTHON=/path/to/venv/bin/python` overrides the choice.
- `pip install` on its own registers the plugin but does not activate it. Hermes plugins are opt-in through `plugins.enabled`, so `hermes plugins enable protean-lcm` is required.
- Requirements: Python 3.10 or newer, and a Hermes Agent release that discovers pip plugins through the `hermes_agent.plugins` entry-point group and gates them with `plugins.enabled`. Hermes 0.21 and newer do this.

## Provenance

protean-lcm is a member of the Protean product family: the ASKA Consulting plugin set for Hermes Agent. It draws from the LCM paper concept (Ehrlich & Blackman, Voltropy PBC) and the design of stephenschoettler/hermes-lcm. We pulled the DAG-based compaction idea, the plugin-local SQLite store with FTS metadata, and the recall tools with recall-policy skill.

We improved on that design by making it a proper third-party plugin with migration, backup, and rollback support. It uses bounded recall pages instead of unbounded loads. We added a one-line pip install where the upstream uses shell scripts.

We deliberately omitted vector embeddings, the evidence compiler, model routing, OpenClaw imports, CLI commands, and the benchmark harness to keep the plugin minimal.

## Use

Once enabled, the engine is available and selected by `context.engine: lcm`. It adds four agent-facing tools:

- `lcm_search` finds a retained message in the current session
- `lcm_expand` pages one summary node's lineage, or reads a single message by id
- `lcm_page` walks the session's retained messages one bounded page at a time
- `lcm_status` reports store and bound diagnostics

`page_size`, `max_search_results`, and `body_chars` are enforced in the storage layer with hard ceilings. No call returns a whole session.

The recall policy and the exact tool arguments and response shapes ship with the plugin under `plugins/context_engine/lcm/skills/hermes-lcm/`.

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

`protean-resource-budget` is the third, independent entry point in this
repository: an opt-in Hermes plugin that guards the native `terminal` tool
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
