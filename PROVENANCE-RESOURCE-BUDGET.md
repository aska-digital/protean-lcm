# Provenance — Resource Budget v0.1 (`protean-resource-budget`)

This file records where the Resource Budget slice came from, which upstream
files were copied byte-for-byte, what is original local code, and which upstream
behaviours were deliberately changed. It is the provenance half of the slice's
stop gate.

## 1. Upstream reference pin

```
repository:  keeltrace/hermesx-resource-budget
commit:      354deb3dc39ef1a39d294727742538da7afea499
license:     MIT
```

Derived from keeltrace/hermesx-resource-budget @ 354deb3 (MIT, Copyright (c)
2026 KeelTrace contributors); upstream checkout read-only, never written.

The pin is a commit, never a branch: upstream has a single commit at that sha,
so there is no release history to track and nothing to follow. Upstream was read
for this slice; it was never written to, never forked, and never pushed to.

## 2. File pin table (the two vendored files)

| Upstream path | Local path | Bytes | Git blob | sha256 |
|---|---|---|---|---|
| `__init__.py` | `plugins/resource_budget/hxrb_runtime.py` | 49,813 | `5b48228e54bc0a948da28380174492ddff888ee5` | `74761c84aa043cc72b207481296d0467cb08bfad700c0872f035705aceb2e293` |
| `LICENSE` | `plugins/resource_budget/UPSTREAM-LICENSE.md` | 1,079 | `1261258246fcc960647d4c772090d4017ec3db33` | `385aa23e6b7b7945458f4077849232802a95fbf06c45aed13dd7283edbcca9f7` |

Both files are byte-identical to their upstream originals at the pinned commit.
The local rename is the only difference. No other upstream file enters this
repository.

## 3. MIT notice (retained, as required)

```
MIT License

Copyright (c) 2026 KeelTrace contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```

The notice above is byte-identical to `plugins/resource_budget/UPSTREAM-LICENSE.md`,
which ships beside the vendored runtime and satisfies the sole MIT condition
that the notice be included in all copies.

## 4. Port nature

Vendored copy. **Upstream source lines ARE copied** — exactly one runtime file
(`hxrb_runtime.py`, byte-identical to `354deb3`) plus the LICENSE above. No
other upstream file enters this repository, and the pinned file is never edited.
The adapter is original local code: the entry point, the mode clamp, the
fail-closed wrapper and the settings resolution copy no upstream line. The
earlier nerve port's claim (that nothing was copied) is not made here; this
slice makes the opposite, hash-verifiable claim.

## 5. Adaptation map (local behaviour to local file)

| Local behaviour | Local file | Nature |
|---|---|---|
| Inert registration: with `mode` unset or `off`, `register()` returns before any `ctx.register_*` call | `plugins/resource_budget/__init__.py` — `register()` | original local code, no upstream line |
| Mode clamp: an unrecognized `mode` resolves to `observe` | `plugins/resource_budget/config.py` — `as_mode()` | original local code, no upstream line |
| Fail-closed wrapper: an internal error in the decision path takes the conservative outcome and never raises into the host | `plugins/resource_budget/__init__.py` — `_build_hook()`, `_fallback_directive()` | original local code, no upstream line |
| Config shim: serves the already-clamped value to the vendored policy and delegates the rest of the host context unchanged | `plugins/resource_budget/config.py` — `SettingsShim` | original local code, no upstream line |
| Settings resolution and defaults for the nine threshold/flag keys | `plugins/resource_budget/config.py` — `DEFAULTS`, `resolve_settings()` | original local code, no upstream line |
| The classifier, the host snapshot, the telemetry writer, the status payload, the blocking seam and the tool schema | `plugins/resource_budget/hxrb_runtime.py` | vendored byte-identical; not edited |

## 6. Deliberate divergences (G1 to G6)

- **G1 — inert activation.** Upstream registers unconditionally and defaults to
  `block_unattended`. This slice requires explicit activation and defaults to
  `off`, so an unconfigured install registers nothing at all.
- **G2 — unrecognized `mode` clamps to `observe`, never to a blocking mode.**
  Upstream's own clamp points at `block_unattended`. The vendored file keeps its
  behaviour byte-exact; the clamp lives in the adapter, and the adapter's shim
  serves the vendored policy an already-clamped, upstream-valid value, so the
  vendored clamp stays defensive depth.
- **G3 — fail-closed wrapper.** Upstream's hook lets exceptions propagate to the
  host. Here every operation that can throw sits inside one `try` whose fallback
  is the conservative closure of the mode predicate.
- **G4 — one `mode` key with `off` as an additional value.** Upstream has four
  modes and no `off`. `off` exists only in the adapter and means the vendored
  runtime is never invoked.
- **G5 — local `plugin.yaml` is the LCM lane-gate shape, not the upstream
  manifest.** Upstream's `config_schema` block is not carried; the settings are
  resolved in `config.py` with the same defaults. `manifest_version` is absent in
  both worlds, so the v1 install floor is preserved.
- **G6 — plugin id `resource-budget` (local) versus upstream
  `PLUGIN_ID = "hermesx-resource-budget"`.** The upstream id is untouched inside
  the pinned file and appears only in the status payload's `plugin` field, which
  reports the truthful upstream identity.

## 7. Re-verification commands

```sh
# the pin
git -C <upstream-checkout> rev-parse HEAD
#   -> 354deb3dc39ef1a39d294727742538da7afea499

# the runtime copy is byte-identical to the pin
git -C <upstream-checkout> show 354deb3dc39ef1a39d294727742538da7afea499:__init__.py | shasum -a 256
#   -> 74761c84aa043cc72b207481296d0467cb08bfad700c0872f035705aceb2e293
shasum -a 256 plugins/resource_budget/hxrb_runtime.py
git -C <upstream-checkout> show 354deb3dc39ef1a39d294727742538da7afea499:__init__.py | cmp - plugins/resource_budget/hxrb_runtime.py

# the license copy is byte-identical to the pin
git -C <upstream-checkout> show 354deb3dc39ef1a39d294727742538da7afea499:LICENSE | shasum -a 256
#   -> 385aa23e6b7b7945458f4077849232802a95fbf06c45aed13dd7283edbcca9f7
shasum -a 256 plugins/resource_budget/UPSTREAM-LICENSE.md
git -C <upstream-checkout> show 354deb3dc39ef1a39d294727742538da7afea499:LICENSE | cmp - plugins/resource_budget/UPSTREAM-LICENSE.md

# the upstream checkout was read, never written
git -C <upstream-checkout> status --short

# the slice touched only its owned files
git diff <base>..HEAD --stat

# the slice's own suite
python3 -m pytest tests/resource_budget -q
```

## 8. Boundary language and enforcement flags

Boundary wording is the source's own, carried byte-exact and never softened or
strengthened. From the source's own Boundary block:

> It is **not a sandbox or complete host-resource security boundary**.

The status payload reports these enforcement-boundary flags as false, and this
slice makes no claim in the other direction either:

```
sandbox: false
hard_cpu_cap: false
hard_memory_cap: false
hard_gpu_cap: false
hard_disk_io_cap: false
hard_network_cap: false
```

## 9. Recognized command categories (carried verbatim from upstream)

- Ollama model `pull`, `create`, and `run`;
- Docker/Podman image `pull`, `build`, `buildx build`, compose `build`, and legacy `docker-compose` / `podman-compose build`;
- Git LFS `fetch`/`pull`, including normal Git global-option forms;
- Hugging Face CLI downloads;
- active `fio`, `stress-ng`, `stress`, and `sysbench ... run` workloads;
- raw/bulk `dd` device I/O;
- obvious large archive/model downloads through curl/wget/aria2c;
- bulk OS package upgrades across apt/apt-get/dnf/yum/zypper/pacman forms, including DNF/YUM `update`/`distro-sync`, Zypper `dup`, and mixed short/long pacman sync+sysupgrade flags;
- Hermes gateway restart/stop through default CLI, profile flags, **proven on-disk Hermes profile aliases**, and matching user-systemd service names with or without an explicit `.service` suffix.

Ordinary `git clone` is **not blocked by default**. Operators can opt into that
category with `block_git_clone: true`.

The classifier also follows common shell launchers such as `sudo bash -c '...'` and `env sh -c '...'`. Help/simulation forms such as `docker build --help`, `apt-get --simulate upgrade`, and `zypper --dry-run update` are deliberately treated as non-executing. It is intentionally not advertised as a complete shell AST, malware detector, PowerShell parser, or process sandbox.

The list is finite and declared, and it is not a shell parser. An invocation
outside it is **allowed**: this is a categorical policy rule over a declared
grammar, not an execution whitelist, and Hermes owns human authorization for
arbitrary tool calls. Forms the classifier does not recognize and therefore
allows include `pip install`, `npm install`, `cargo build`, `make`,
`python3 x.py`, `node server.js`, and `bash script.sh`.
