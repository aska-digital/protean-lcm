# Install proof

Evidence that the plugin installs from the package and is found, enabled, and adopted by Hermes
with no manual file copying, and that both supported install paths land on the same plugin name.

Environment used

- Hermes Agent v0.21.4 (2026.9.21), git install at `<HERMES_CHECKOUT>` (local `743ee725`),
  interpreter `<HERMES_CHECKOUT>/venv/bin/python`, Python 3.11.15
- a fresh Hermes home (`HERMES_HOME` pointing at an empty directory)
- the package built to a wheel and installed into a directory that is not the Hermes install and
  not the repository, reached through `PYTHONPATH`, so the host tree and the developer virtualenv
  are left untouched
- one unrelated plugin (`keenable`, shipped by the `hermes-keenable-web` distribution) is already
  installed in that interpreter, so `hermes plugins list` prints it. No line of it comes from this
  repository.

The one command a user runs is documented in the README:

```sh
pip install git+https://github.com/aska-digital/protean-lcm.git
hermes plugins enable protean-lcm
hermes config set context.engine lcm
```

The transcript below installs from the working tree instead of the git URL. The wheel, the entry
point, and the discovery path are identical to the git URL form.

## 1. Build and install

```
$ pip wheel --no-deps -w "$DIST" .
Successfully built protean-lcm

$ ls "$DIST"
protean_lcm-0.1.0-py3-none-any.whl     # 35409 bytes; wheel bytes shift with the embedded build timestamp

$ pip install --no-deps --target "$SITE" "$DIST"/protean_lcm-0.1.0-py3-none-any.whl
ok

$ ls "$SITE"
protean_lcm
protean_lcm-0.1.0.dist-info
```

One distribution, one entry point, one manifest:

```
$ cat "$SITE"/protean_lcm-0.1.0.dist-info/entry_points.txt
[hermes_agent.plugins]
protean-lcm = protean_lcm

$ ls "$SITE"/protean_lcm/plugin.yaml "$SITE"/protean_lcm/skills/protean-lcm/SKILL.md
protean_lcm/plugin.yaml
protean_lcm/skills/protean-lcm/SKILL.md
```

The wheel carries no `protean_lane_gate` and no `protean_resource_budget` path, and the manifest
above is the only `plugin.yaml` in it.

## 2. Discovered, before it is enabled

```
$ HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_CHECKOUT/hermes" plugins list --plain --no-bundled
not enabled  entrypoint 0.1.0    protean-lcm
not enabled  entrypoint 0.1.1    keenable
```

Source is `entrypoint`, which is the pip plugin path. Nothing was copied into the Hermes home.
(`keenable` is the unrelated plugin noted above.)

## 3. Enabled and selected

```
$ HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_CHECKOUT/hermes" plugins enable protean-lcm
✓ Plugin protean-lcm enabled. Takes effect on next session.

$ HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_CHECKOUT/hermes" config set context.engine lcm
✓ Set context.engine = lcm in $HOME/config.yaml

$ HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_CHECKOUT/hermes" plugins list --plain --no-bundled
enabled      entrypoint 0.1.0    protean-lcm
not enabled  entrypoint 0.1.1    keenable
```

## 4. The host loads it and an agent adopts the engine

`scripts/smoke_check.py`, run against the same fresh home:

```
$ HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" scripts/smoke_check.py
HERMES_HOME: $HOME
context.engine: lcm
plugins.enabled: ['protean-lcm']
discovered: protean-lcm | source: entrypoint | enabled: True | error: None
plugin context engine: protean_lcm.engine | name: lcm
adopted engine: protean_lcm.engine.LCMEngine
engine name: lcm
context_length: 204800
threshold_tokens: 153600
engine tools: ['lcm_expand', 'lcm_page', 'lcm_search', 'lcm_status']
```

Read out: the plugin is found from the entry point, it loads with no error, it registers its engine
with the host, and an agent built with `context.engine: lcm` adopts `protean_lcm.engine.LCMEngine`,
receives the model context length, and gets the four `lcm_*` tools. The engine module name proves
it came from the installed package rather than from a source tree.

## 5. Test suite and capability gate after packaging

```
$ sh scripts/run_tests.sh "$HERMES_CHECKOUT" -q
56 passed, 4 warnings in 2.07s
```

The suite runs against the real context-engine loader of the Hermes checkout passed to the script.
Its scope is `tests/plugins/context_engine/`; the unpackaged slices' suites are not covered by this
command. The warnings come from the host checkout's own plugin-compat notices (the Sep 2026
`run_agent.*` import move), not from this plugin, and their count moves between runs (3 or 4 on this
host).

```
$ "$PY" "$HERMES_CHECKOUT/hermes" plugins validate plugins/context_engine/lcm
✓ manifest — plugin.yaml parses
✓ manifest fields — name, version, description present
✓ loadable — entry: __init__.py
✓ capability probe — register() ran in isolation
✓ declared tools — matches registrations
✓ declared hooks — matches registrations
✓ declared middleware — matches registrations
✓ security scan — safe
Validation passed.

$ "$PY" "$HERMES_CHECKOUT/hermes" plugins doctor plugins/context_engine/lcm --ci
Plugin Doctor: <repo>/plugins/context_engine/lcm
  manifest: protean-lcm 0.1.0 (standalone)
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 0 tool(s), 0 hook(s)
```

`register(ctx)` registers no tools, no hooks and no middleware; the four `lcm_*` tools are
delivered by the context engine once it is selected. That is why the manifest declares empty
`provides_tools` and `provides_hooks`, and why the catalog description names the four tools
instead.

## 6. The directory-install path lands on the same name

A catalog install clones the repository and copies the entry's `subdir` into the Hermes home as a
directory plugin. The same thing, run against this working tree:

```
$ HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_CHECKOUT/hermes" plugins install "file://<repo>#plugins/context_engine/lcm"
Cloning file://<repo> (subdir: plugins/context_engine/lcm)...
✓ Installed
Location: $HOME_DIR/plugins/protean-lcm
Plugin installed but not enabled. Run `hermes plugins enable protean-lcm` to activate.

$ ls "$HOME_DIR/plugins"
protean-lcm

$ HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_CHECKOUT/hermes" plugins list --plain --no-bundled
not enabled  user     0.1.0    protean-lcm

$ HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_CHECKOUT/hermes" plugins enable protean-lcm
✓ Plugin protean-lcm enabled. Takes effect on next session.

$ HERMES_HOME="$HOME_DIR" "$PY" scripts/smoke_check.py
discovered: protean-lcm | source: user | enabled: True | error: None
plugin context engine: hermes_plugins.protean_lcm.engine | name: lcm
adopted engine: hermes_plugins.protean_lcm.engine.LCMEngine
engine tools: ['lcm_expand', 'lcm_page', 'lcm_search', 'lcm_status']

$ HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_CHECKOUT/hermes" plugins disable protean-lcm
⊘ Plugin protean-lcm disabled. Takes effect on next session.
```

Both paths end at `hermes plugins enable protean-lcm` / `hermes plugins disable protean-lcm` and
both adopt `LCMEngine` with the four tools. The source label and the engine module name differ
(`entrypoint` on the pip path, `user` and `hermes_plugins.*` on the directory path), which is how
the host reports where the code came from.

## Reproduce

```sh
HERMES_SRC=/path/to/hermes-agent
PY="$HERMES_SRC/venv/bin/python"
REPO=/path/to/protean-lcm
DIST=/tmp/lcm_proof/dist
SITE=/tmp/lcm_proof/site
HOME=/tmp/lcm_proof/home
HOME_DIR=/tmp/lcm_proof/home-directory

rm -rf /tmp/lcm_proof && mkdir -p "$DIST" "$SITE" "$HOME" "$HOME_DIR"
cd "$REPO"

# pip path
"$PY" -m pip wheel --no-deps -w "$DIST" .
"$PY" -m pip install --no-deps --target "$SITE" "$DIST"/protean_lcm-0.1.0-py3-none-any.whl
cat "$SITE"/protean_lcm-0.1.0.dist-info/entry_points.txt

HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_SRC/hermes" plugins list --plain --no-bundled
HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_SRC/hermes" plugins enable protean-lcm
HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" "$HERMES_SRC/hermes" config set context.engine lcm
HERMES_HOME="$HOME" PYTHONPATH="$SITE" "$PY" scripts/smoke_check.py

# capability gate and suite
"$PY" "$HERMES_SRC/hermes" plugins validate plugins/context_engine/lcm
sh scripts/run_tests.sh "$HERMES_SRC" -q

# directory path (what a catalog install does)
HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_SRC/hermes" plugins install "file://$REPO#plugins/context_engine/lcm"
HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_SRC/hermes" plugins enable protean-lcm
HERMES_HOME="$HOME_DIR" "$PY" "$HERMES_SRC/hermes" config set context.engine lcm
HERMES_HOME="$HOME_DIR" "$PY" scripts/smoke_check.py
```

Notes on this host: the `hermes` launcher on `PATH` runs `unset PYTHONPATH`, which hides any
`PYTHONPATH`-style install, so the checkout's `hermes` is invoked through the checkout's own
interpreter. `file://` is accepted by `plugins install` and warns as an unreviewed local source;
a real install resolves the catalog entry's `repo` at its pinned SHA over https.
