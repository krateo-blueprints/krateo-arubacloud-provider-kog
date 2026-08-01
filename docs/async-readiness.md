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
      jq: {inline: '. | split("/") | last'}   # {operationId} ← trailing id of the URI
    poll:
      method: GET
      path: /projects/{projectId}/providers/Aruba.Baremetal/hpcs/monitor/{operationId}
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

> **Token note:** the poll `path` uses the literal `{operationId}` token (bound
> from `operationRef`), which maps onto the OAS's `…/monitor/{id}` segment.
> `scripts/validate.py` checks the poll endpoint exists in the OAS
> param-name-agnostically.

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
      path: /projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets/{operationId}
      statusPath: status.state
      successValues: [Active]
      failureValues: [Error, Failed]
      intervalSeconds: 3
      maxAttempts: 100
```

This pattern is **not** enabled by default on the metadata-wrapped resources
because the `state` value set is **not enumerated in the source OAS** — `Active`
is domain knowledge (from Aruba's docs/console), so success/failure values can't be
derived and validated automatically. Enable it per resource by adding an `async_`
entry to that resource's `OVERRIDES` in `scripts/generate_restdefinitions.py`
(mirroring the HPC entry) once you have confirmed the state values for it.

## Why this is better than a one-shot wait

| | Terraform blocking wait | Controller `async: requeue` |
|---|---|---|
| Blocks a worker while waiting | Yes (the CLI process) | No (requeues) |
| Detects terminal failure | Yes | Yes (`failureValues`) |
| Keeps watching after "ready" | No (run ends) | Yes — re-observes forever, corrects drift, re-reconciles if the resource degrades |
| Model | Imperative, one-shot | Declarative, level-based |

Readiness waiting is exactly what controllers are for. See
[oasgen-provider-evolution §C2](oasgen-provider-evolution.md) for the one residual
ergonomic gap (state enums absent from the OAS).
