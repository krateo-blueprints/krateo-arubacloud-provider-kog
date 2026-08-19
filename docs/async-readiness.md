---
type: Architecture
title: Async readiness
description: How long-running Aruba operations map onto async polling and readiness.
tags: [aruba, kog]
timestamp: 2026-08-19T00:00:00Z
---

# Async readiness — the controller's home turf

Aruba provisions most resources **asynchronously**: a create returns immediately
and the resource becomes usable only once it reaches a terminal state. A CLI like
Terraform handles this with a one-shot blocking `WaitForResourceActive`. A
Kubernetes controller does it better and more naturally: it **requeues** and
re-observes on a level-based loop, so "wait for ready" is just reconciliation —
continuous, non-blocking, and self-healing if the resource later degrades.

The braghettos oasgen-provider fork exposes this directly with the per-verb
**`async`** block. This repo wires it where the API is genuinely asynchronous, so
readiness is **not** something we cede to Terraform — it is a feature we get from
the controller model.

## Two async shapes in the Aruba APIs

### 1. Operation handle (wired: `baremetal/Hpc`)

Some endpoints return an operation handle and expose a monitor endpoint. HPC is
the textbook case, and it is wired in `restdefinitions/baremetal/hpc.yaml`:

- `POST …/hpcs` → `201 { "monitorUri": "…/hpcs/monitor/<id>" }`
- `GET …/hpcs/monitor/{id}` → `{ "status": "InProgress|Succeeded|Failed", "resourceUri": … }`

```yaml
- action: create
  method: POST
  path: /projects/{projectId}/providers/Aruba.Baremetal/hpcs
  async:
    mode: requeue                     # non-blocking, level-based — the controller way
    operationRef:
      in: body
      path: monitorUri
      jq: {inline: '. | split("/") | last'}   # handle = trailing id of the URI
    poll:
      method: GET
      path: /projects/{projectId}/providers/Aruba.Baremetal/hpcs/monitor/{id}
      handleParam: id                 # Aruba names it {id}; no OAS patching needed
      statusPath: status
      successValues: [Succeeded]
      failureValues: [Failed]
      intervalSeconds: 5
      maxAttempts: 120
    postGet: true                     # re-run findby/get after success to populate status
```

`mode: requeue` means the reconcile fires the operation, records the handle, and
returns; each subsequent reconcile polls once and requeues until terminal — it
never pins a worker, and it adds terminal-**failure** detection (`Failed`) that a
blind "wait until it appears" loop lacks.

> **Path contract (oasgen/RDC >= 0.18.0).** Two things must hold together, and
> oasgen now checks both at admission
> (`restdefinition/helper.go: validateAsyncPollPaths`) instead of letting them
> fail on the first poll:
>
> 1. the poll path must be an **exact key** of the OAS `paths` object — paths are
>    resolved by exact string lookup (`restclient.go: PathItems.Get`);
> 2. it must contain the `{handleParam}` token — the path parameter the extracted
>    handle binds to.
>
> **`handleParam` is why Aruba's spec is used unmodified.** It names that
> parameter (default `operationId`), so `path: …/monitor/{id}` +
> `handleParam: id` works against the published document. Before 0.18.0 the name
> was hardcoded, so the OAS itself had to be patched — a workaround this repo has
> now removed. `scripts/validate.py` mirrors the same two checks locally.
> Background: [adversarial-review](adversarial-review.md) finding #1.

### 2. Status field on the resource (pattern for the rest)

Most resources have no operation handle: create returns the resource with
`status.state = InCreation`, transitioning to `Active`. Readiness is then "poll the
resource's own GET until `status.state` is terminal". The same `async` block
expresses it — point the poll at the item endpoint and read `status.state`:

```yaml
- action: create
  method: POST
  path: /projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets
  async:
    mode: requeue
    operationRef: {in: body, path: metadata.id}      # the created resource id
    poll:
      method: GET
      path: /projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets/{id}
      handleParam: id
      statusPath: status.state
      successValues: [Active]
      failureValues: [Error, Failed]
      intervalSeconds: 3
      maxAttempts: 100
```

This pattern is **not** enabled by default on the metadata-wrapped resources
because the terminal `state` values (`Active`, …) must be supplied by hand rather
than derived from the schema. That is not an oversight in this repo — it is how
the Aruba API is modelled (see [why below](#why-the-state-field-has-no-enum)). Enable
it per resource by adding an `async_` entry to that resource's `OVERRIDES` in
`scripts/generate_restdefinitions.py` (mirroring the HPC entry) once you have
confirmed the state values for it.

### Why the state field has no enum

The Aruba specs are generated from the service's ASP.NET server models (the
`Microsoft.AspNetCore.Mvc.ProblemDetails` schema and the C#-namespaced schema
names are the fingerprint), and the generator emits `enum:` **only** when the
backing property is a real C# enum. It does so for 18 schemas — including a full
lifecycle enum `ResourceProviderClaimStatus` (`InCreation`, `Completed`,
`Deleted`, …). But `status.state` is declared as a bare
`{"type": "string", "nullable": true}`: the backend models it as an **open
string**, a common status-field pattern that lets the service add new states
without a breaking schema change. Tellingly, the one lifecycle enum that exists is
referenced by a single *failure* DTO (`ResourceProviderProvisioningFailed.claimStatus`)
and its vocabulary doesn't even match the values `state` returns at runtime
(`Active`/`InCreation`/`Updating`/`Deleted`) — so `state` is a deliberately
separate, loosely-typed projection, not an enum that slipped through.

By contrast the **operation-handle** case is self-describing: HPC's monitor
endpoint types its `status` as an enum (`InProgress`/`Succeeded`/`Failed`), which
is why `baremetal/Hpc` is wired with no hand-entered value set.

## Why this is better than a one-shot wait

| | Terraform blocking wait | Controller `async: requeue` |
|---|---|---|
| Blocks a worker while waiting | Yes (the CLI process) | No (requeues) |
| Detects terminal failure | Yes | Yes (`failureValues`) |
| Keeps watching after "ready" | No (run ends) | Yes — re-observes forever, corrects drift, re-reconciles if the resource degrades |
| Model | Imperative, one-shot | Declarative, level-based |

Readiness waiting is exactly what controllers are for. See
[oasgen-provider-evolution §C2](oasgen-provider-evolution.md) for the one residual
ergonomic gap (the open-string `status.state` above).
