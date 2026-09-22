# Provenance — Lane Gate v1 (`protean-lane-gate`)

This file records where the Lane Gate slice's ideas came from, what was
re-implemented rather than copied, and what was deliberately left out. It is the
provenance half of the slice's stop gate (locked architecture §6).

## Upstream reference pin

```
repository:  keeltrace/hermes-nerve
commit:      de219b1875a9943cb81de406c6853caf53496aaf
license:     MIT
```

Any adapted text in this repository derives from that commit and that commit
only. The upstream checkout was read, never written, for the whole slice.

## MIT notice (retained, as required)

```
MIT License

Copyright (c) 2026 Nerve contributors

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

## Adapted ideas, and where they landed

| Upstream idea | Where it lives here | Nature |
|---|---|---|
| Read-only prefilter: a narrow read-only tool set, a set of obviously read-only shell commands, a set of read-only `git` subcommands, a shell-metacharacter guard, refusal of environment-prefixed/path-qualified executables, and the `rg --pre` / `git --ext-diff` / `--textconv` escape guards | `plugins/lane_gate/gate.py` — `READ_ONLY_TOOLS`, `READ_ONLY_SHELL_COMMANDS`, `SAFE_GIT_SUBCOMMANDS`, `SHELL_META_RE`, `read_only_shell()` | Re-implemented against our own closed contract. No upstream source line was copied. |
| The `ALLOW \| APPROVAL \| BLOCK` choice contract (`contracts/tool-gate-v1.json`) | `plugins/lane_gate/gate.py` — `protean/lane-gate/v1`, `DECISIONS`, `REASONS` | Re-declared as a closed schema with every field required and a closed reason enum. |
| Content-bound receipt identity (upstream receipt v3: a hash over the record's own identity fields, privacy-minimized by default) | `plugins/lane_gate/receipts.py` — `protean/lane-receipt/v1`, `compute_receipt_id()`, `args_sha256`, hash-by-default detail mode | Adapted idea, independently implemented; the field set, the separator convention and the ledger location are ours. |
| Hash-bound manifests (bind once, detect later edits) | `plugins/lane_gate/manifest.py` — `ManifestResolver`, `manifest_sha256` over canonical JSON | Adapted idea, independently implemented. |

## Deliberate divergence (locked gap G1)

* **No copy of `hermes_nerve/schemas.py` in any form.** Upstream issue #8
  (under-specified required keys, `HTTP 400 invalid_union`) is the reason. Our
  three schemas — `protean/lane-manifest/v1`, `protean/lane-gate/v1`,
  `protean/lane-receipt/v1` — are self-defined and closed: required keys are
  explicit and unknown keys fail.
* **No model in the decision path.** The upstream gate's prefilter is followed by
  a Jev (model) round-trip; that round-trip is where issue #8 bit. Lane Gate v1 is
  deterministic and local: regex, allowlists and glob matching only, no provider
  call, no network client (`tests/lane_gate/test_lane_gate_gate.py::
  test_no_model_in_the_decision_path`).
* **No kanban dispatch, supervisor, `remote/*`, reflex backends or OpenJev.**
  Locked out of the slice (§7 G2, upstream issue #10). The gate never dispatches
  `kanban_*` and never mutates task state; Kanban remains the sole lifecycle
  authority.
* **External-write classification is ours.** The upstream prefilter classifies
  nothing beyond read-only; the local-mutation / external-write split and the
  fail-closed treatment of unauditable shell calls are Lane Gate v1 decisions,
  recorded in `gate.py`'s module docstring.

## protean-team is read-only for this slice

The handoff format stays `artifact-contract` v1 exactly as protean-team defines
it (contract_version 1, its checker, its enums). The slice adds mapping
invariants M1–M4 and verifies them against local fixtures inside
`tests/lane_gate/`; it writes no code and no documentation to protean-team
(locked gap G5). A choreography-doc update there is a separate, later,
separately-authorized lane.

## How to verify these claims

```sh
# the pin
git -C <hermes-nerve-checkout> rev-parse HEAD
#   -> de219b1875a9943cb81de406c6853caf53496aaf

# no upstream schema file copied into this repository
git grep -n "invalid_union\|jev_assess" -- . || true

# the slice touched only its owned files
git diff 9d3c4cb8dd7c58670ea9525b08391390ca8c0521..HEAD --stat

# the gate is local and model-free
python -m pytest tests/lane_gate -q
```
