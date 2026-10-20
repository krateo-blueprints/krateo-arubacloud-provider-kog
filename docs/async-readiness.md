---
type: Architecture
title: Async readiness
description: How long-running Aruba operations map onto async polling and readiness.
tags: [aruba, kog]
timestamp: 2026-08-31T00:00:00Z
---

# Async readiness — the controller's home turf

Aruba provisions most resources **asynchronously**: a create returns immediately
and the resource becomes usable only once it reaches a terminal state. A CLI like
Terraform handles this with a one-shot blocking `WaitForResourceActive`. A
Kubernetes controller does it better and more naturally: it **requeues** and
re-observes on a level-based loop, so "wait for ready" is just reconciliation —
continuous, non-blocking, and self-healing if the resource later degrades.

oasgen-provider exposes this directly with the per-verb
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

## A changed RestDefinition does not restart its controller

Adding the async block to `Dbaas` appeared to do nothing. The RestDefinition applied
cleanly, oasgen reported `Ready=True` within six seconds, the block was visibly
present in-cluster on the create verb — and a full chain run behaved exactly as
before: `Ready` in seconds, drift refused with `400 DBaaS.Read.InvalidStatus`.

The controller pod was thirteen days old. Applying the RestDefinition does not roll
the generated deployment, so the running process kept serving the configuration it
started with. After `kubectl rollout restart`, the same unchanged RestDefinition
produced the intended behaviour immediately:

```
t=10s … t=120s   Ready=False/Pending
                 krateo.io/async-operation-id: 6ad689bae40e2c610d13ef9a
```

The failure mode is quiet in the worst way: every surface you would check says the
change landed. `kubectl apply` reports `configured`, the RestDefinition reports Ready,
and `kubectl get restdefinition -o yaml` shows the new block. Only the behaviour is
stale, and nothing logs a reason.

**After changing a RestDefinition's verb configuration, restart its controller:**

```
kubectl rollout restart deploy <provider>-<resource>-controller -n krateo-system
```

This applies to verb-level configuration — `async`, `fieldMapping`, `compareScope`.
A change to `identifiers` is a separate matter: those are immutable once the CRD
exists, so the RestDefinition must be deleted and recreated, which replaces the
deployment anyway and does not need this.

## Deletion is refused mid-provision too

`DBaaS.Read.InvalidStatus` has a delete counterpart, `DBaaS.Delete.InvalidStatus`,
with the same cause and the same wording. A Dbaas cannot be deleted while it is
`InCreation`; the controller retries and succeeds once the resource reaches `Active`.

A teardown that gives up after a fixed interval will therefore leave a billable
resource behind — observed with a 180s timeout against a provisioning window that has
measured anywhere from 7.5 to 14+ minutes. `scripts/ga_chain.py` allows for async
deletion and re-checks residue against the API rather than trusting the delete call,
which is the behaviour to copy. Do not clear the finalizer to force it: that removes
the controller's delete path and orphans the remote resource, which has happened here
before.

## Every write is refused mid-transition, not just Dbaas's

The `InvalidStatus` family is not specific to DBaaS. The same refusal appears across
providers, in the generic validation envelope rather than a typed error code:

```
400 {"type": ".../400", "title": "One or more validation errors occurred.",
     "status": 400,
     "errors": [{"field": "Status", "message": "invalid status"}]}
```

Observed by hand against a live account, all in one sitting:

| call | state at the time | answer |
|------|-------------------|--------|
| `PUT` securityRule | `Active` | 200 |
| `PUT` securityRule, same body, immediately after | `Updating` | 400 `Status: invalid status` |
| `PUT` securityRule, same body, once `Active` again | `Active` | 200 |
| `DELETE` vpc while its securityGroup was still deleting | — | 400, then 202 on retry |

Two consequences for anything that writes to this API:

**A 400 is not evidence about the body.** The identical payload earns 200 or 400
depending only on when it arrives. `scripts/ga_chain.py` chased this as a body-shape
bug through two wrong explanations because it only looked at the status code; it now
waits for a settled state before writing, and retries rather than concluding when the
refusal names the `Status` field. Read the `errors` array before theorising.

**`Ready` on the CR does not mean writable upstream.** The controller reports Ready
once its observe succeeds, which can be while the resource is still `Updating`. Code
that reconciles immediately after Ready must tolerate this refusal and retry.

Aruba's error envelope is `errors: [{field, message}]` — **not**
`{fieldName, errorMessage}`. Reading the wrong keys turns a precise, actionable
message into `None: None`, which is how the above went unexplained for a whole
campaign.
