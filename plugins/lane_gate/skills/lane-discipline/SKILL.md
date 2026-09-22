---
name: lane-discipline
description: Use when a lane manifest is bound to your worker session (lane gate advisory or enforce). Explains what the lane owns, what a denial looks like, and how to cite lane receipts as evidence.
version: 0.1.0
author: ASKA Digital
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [lane-gate, governance, receipts, provenance]
    related_skills: []
---

# Lane discipline

A lane manifest is bound to your worker session. It names one thing: where your
writes may land, what is frozen, which tools you may not call, and whether you
are authorized to write off the machine.

```
{
  "schema": "protean/lane-manifest/v1",
  "lane_id": "lane-example-01",
  "owned_write_globs": ["/repo/tests/lane_gate/*", "/repo/plugins/lane_gate/*"],
  "forbidden_globs": ["/repo/protean-team/*", "/repo/LICENSE"],
  "forbidden_tools": ["delegate_task"],
  "external_writes": false
}
```

The manifest is checked on every tool call, before the tool runs, against every
path-like argument of that call. Matching is `fnmatch` over absolute paths: `*`
spans `/`, so `.../lane_gate/*` covers the whole subtree. Include a directory's
own path as a glob when the call addresses the directory itself.

## What the gate decides

| Decision | Meaning |
|---|---|
| `ALLOW` / `read_only_fastpath` | A read-only tool or an obviously read-only shell shape (`git status`, `ls`, `grep`, ...). |
| `ALLOW` / `in_lane` | A write whose every touched path is inside your owned globs. |
| `ALLOW` / `external_write` | An external-write-class call, explicitly authorized by `external_writes: true`. |
| `BLOCK` / `forbidden_tool` | You called a tool the manifest forbids. |
| `BLOCK` / `forbidden_target` | A touched path hits a forbidden glob. |
| `BLOCK` / `outside_owned` | A write touched a path outside your owned globs. |
| `APPROVAL` / `external_write` | A call that leaves the machine while `external_writes` is false. |
| `APPROVAL` / `unknown_tool` | The classifier cannot place the call (an unclassified tool, or a shell/external call whose touched paths cannot be derived). |
| `APPROVAL` / `manifest_invalid` | No valid manifest is bound: missing, unparseable, or edited after load. |
| `APPROVAL` / `gate_error` | The gate itself failed. |

## What a denial looks like

In `enforce` mode the call does not run. The tool result carries the gate's
message instead:

```
Lane Gate BLOCK: outside_owned (contract protean/lane-gate/v1, mode enforce,
manifest 3f9c1a02b7d4). This call was denied before execution.
```

In `advisory` mode the call runs and only the receipt records the decision. A
denial is not a judgement on your work: it means "not in this lane". The
remedies are to write inside your owned globs, to ask the lane's owner for a
wider manifest, or to report the block as evidence that the lane is too narrow.

Do not retry a denied call through a different tool to get the same effect. That
is exactly the straying the gate exists to catch, and the second attempt leaves
its own receipt.

## Receipts

Every decision writes one line to the ledger (`PROTEAN_LANE_RECEIPTS`, default
`<HERMES_HOME>/lane_gate/receipts.jsonl`). Argument contents are hashed by
default; only `PROTEAN_LANE_RECEIPT_DETAIL=sanitized` adds a redacted rendering.
The ledger is append-only and content-bound: `receipt_id` recomputes from the
record's own fields, and `created_at` never goes backwards.

Cite one in your handoff like this:

```
evidence_refs:
  - ref: "lane-receipt/v1 6d1b0f...c8 (write_file -> ALLOW/in_lane); proves: every write of this lane landed inside its owned globs"
    proves: "lane discipline held for the whole lane"
```

## When there is no gate

No manifest bound means no fastpath and no in-lane allowance: every call
escalates. If you see `manifest_invalid` on every call, stop and report it --
the lane's manifest path or its contents are wrong, and no work can proceed under
`enforce` until the lane owner fixes it.
