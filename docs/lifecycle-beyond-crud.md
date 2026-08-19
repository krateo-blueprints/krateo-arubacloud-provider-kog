---
type: Architecture
title: Lifecycle beyond CRUD
description: Actions that are not create/read/update/delete and how they are modelled.
tags: [aruba, kog]
timestamp: 2026-08-19T00:00:00Z
---

# Lifecycle beyond the five CRUD verbs — the proxy-free solution

`oasgen-provider` models exactly five verbs (findby/get/create/update/delete).
Several Aruba resources need more than that: their lifecycle is a **multi-call
sequence** or a set of **imperative action endpoints** (power on/off,
associate/disassociate subnets·security-groups·elastic-IPs, attach/detach
volumes, rename, reset password, automatic renew…). The old blueprint would have
solved this with a bespoke proxy web service. This repository solves it two ways,
**both without a proxy**:

1. **Per-resource** → a RestDefinition that **delegates** its mutating verbs to
   **Snowplow RESTActions** via the fork's `createApiRef` / `updateApiRef` /
   `deleteApiRef`, while observe stays native (`get`/`findby`).
2. **Cross-resource** → a **Krateo Composition** that provisions a whole
   environment of single-purpose CRs together.

They compose: the environment Composition (approach 2) contains a CloudServer CR
whose own multi-call lifecycle is driven by approach 1.

---

## 1. Per-resource: RestDefinition + `*ApiRef` → Snowplow RESTAction

The flagship case is `compute/CloudServer`. Its RestDefinition keeps native
observe and delegates the mutating verbs:

```yaml
resource:
  kind: CloudServer
  identifiers: [metadata.name]
  additionalStatusFields: [metadata.id]
  createApiRef: {name: arubacloud-compute-cloudserver-create, namespace: krateo-system, extras: {api-version: "1.0"}}
  updateApiRef: {name: arubacloud-compute-cloudserver-update, namespace: krateo-system, extras: {api-version: "1.0"}}
  deleteApiRef: {name: arubacloud-compute-cloudserver-delete, namespace: krateo-system, extras: {api-version: "1.0"}}
  verbsDescription:            # observe only — this is what verifies convergence
    - {action: findby, method: GET, path: .../cloudServers}
    - {action: get,    method: GET, path: .../cloudServers/{cloudServerId},
       requestFieldMapping: [{inPath: cloudServerId, inCustomResource: status.metadata.id}]}
```

- The controller invokes the **create** RESTAction every reconcile until the
  native observe reports the server exists (level-based convergence), so the
  RESTAction MUST be **idempotent**.
- **update** fires when native Observe reports drift; it re-applies the desired
  associations/power state.
- **delete** is held by the finalizer until its RESTAction succeeds.

The RESTActions live in [`restactions/compute/`](../restactions/compute/):
`cloudserver-create`, `cloudserver-update`, `cloudserver-delete`, plus the
`arubacloud-endpoint` Secret they call through.

### How idempotency is expressed without a conditional keyword

A Snowplow `spec.api[]` step runs **once per element of its
`dependsOn.iterator`** array. An iterator that evaluates to an **empty array
therefore runs the step zero times** — that is the "do this only if needed"
guard. Every mutating step uses it:

```yaml
# create only when no server with this name exists yet
dependsOn:
  name: existing
  iterator: >-
    .spec.metadata.name as $n
    | ((.existing.values // []) | map(select(.metadata.name == $n)) | length) as $count
    | if $count > 0 then [] else [ .spec ] end
```

```yaml
# associate/disassociate only the delta (desired - current) / (current - desired)
iterator: >-
  (.server.self // {}) as $s
  | ((.spec.properties.subnets // []) | map(.uri // .)) as $desired
  | (($s.properties.subnets // []) | map(.uri // .)) as $current
  | ($desired - $current) as $add | ($current - $desired) as $del
  | if $s.id != null and (($add|length)>0 or ($del|length)>0) then [{id:$s.id, add:$add, del:$del}] else [] end
```

Combined with `continueOnError: true` on the action calls, re-running the whole
RESTAction is always safe.

### Generic recipe for the other action-only resources

The same pattern dissolves every action endpoint dropped from the generated
RestDefinitions (see `oasgen-provider-evolution.md` §C1). Author one RESTAction
that:

1. resolves the resource by name/id (a `GET` step, `filter` to the item);
2. for each imperative action, adds a step guarded by an iterator that yields a
   non-empty array only when the action is actually needed;
3. wire it to the resource's RestDefinition via `updateApiRef` (day-2 actions) or
   `createApiRef`/`deleteApiRef`.

| Resource | Action endpoint(s) | Wire via |
|----------|--------------------|----------|
| `container/Registry` | `resetAdminPassword` | `updateApiRef` (guard: run when a rotation is requested) |
| `container/Kaas` | `nodePools/{name}` update, `nodes/.../attach|detach`, `detach` | `updateApiRef` (reconcile nodepool/volumes) |
| `database/DatabaseUser` | `.../password` | `updateApiRef` (guard on a password-generation marker) |
| `database/DatabaseBackup` | `download`, `restore` | dedicated RESTAction / on-demand |
| `project/Folder`, `baremetal/Hpc` | `automaticrenew`, `name` (rename) | `updateApiRef` |

Because these are `updateApiRef` on resources that already have native
create/get/delete, only the day-2 action layer is delegated — the rest stays pure
OAS.

---

## 2. Cross-resource: a Krateo Composition

For "give me a whole environment", use the **composition concept**: a
`CompositionDefinition` registers a Helm chart whose templates render the
single-purpose Aruba CRs together, from one set of inputs.

- Chart: [`compositions/aruba-cloudserver-environment/`](../compositions/aruba-cloudserver-environment/)
  renders a `Vpc`, `Subnet`, `SecurityGroup` and `CloudServer` plus their
  `*Configuration` resources, all parameterised by shared values
  (`projectId`, `location`, `name`, CIDRs, flavor…).
- Registration: [`compositions/compositiondefinition.yaml`](../compositions/compositiondefinition.yaml)
  (`core.krateo.io/v1alpha1`) points at the packaged OCI chart, exactly like
  `braghettos/krateo-oasgen-provider-chart`'s own `compositiondefinition.yaml`.

Applying a Composition instance provisions the entire stack; the CloudServer
inside it is managed by approach 1, so power/associations/volumes converge
without any proxy.

### Cross-resource references (known gap)

Aruba refers to a VPC/subnet/security-group by a **runtime URI** that only exists
after the resource is created. Helm cannot know it at template-render time, so the
composition provisions the networking and leaves the concrete
`vpcId`/`subnets`/`securityGroups` wiring marked `REPLACE_...`. Fully automatic
wiring needs Krateo's cross-resource reference resolution (read the created VPC's
`status.metadata.id` into the Subnet's `spec.vpcId`, etc.). This is an
orchestration-layer capability, not a proxy — tracked here so operators know to
supply the ids (or use Krateo composition expressions) until it lands.

---

## Runtime contracts (verified against RDC source)

Two non-obvious contracts govern `*ApiRef` delegation — both found by the
[adversarial review](adversarial-review.md) and now honoured here:

1. **Snowplow must be wired.** RDC resolves RESTActions via its `-snowplow-url`
   flag (`URL_SNOWPLOW` env; empty = disabled, with `URL_AUTHN` for
   authenticated calls). The oasgen chart's RDC templates do **not** set these,
   so a stock install fails delegation with *"no snowplow client is configured"*
   until you add `URL_SNOWPLOW` (and typically `URL_AUTHN`) to the generated
   RDC deployment's environment.
2. **The spec is forwarded on every direction — from RDC 0.18.0 only.**
   `buildExtras` passes the whole CR spec to create, update **and** delete
   invocations (`writesDesiredState` now governs only whether the RESTAction's
   *result* is projected into status). So all three CloudServer RESTActions read
   `.spec.*` uniformly. Also available: `name`/`namespace`/`uid` and each
   identifier dot-keyed (`.["metadata.name"]`).

   **On RDC < 0.18.0 this silently breaks**: delete received no spec, so
   `.spec.projectId` is null, every guard skips, snowplow reports success, and
   the finalizer never releases. That older behaviour is why this repo used to
   carry a static `deleteApiRef.extras.projectId` — now removed
   ([rdc#41](https://github.com/krateo-platformops/rest-dynamic-controller/issues/41)).

## Caveats

- The Composition chart is validated with `helm lint` and `helm template`
  (renders 8 well-formed resources — the 4 CRs + their 4 Configurations). The
  RESTActions are a **reviewed reference implementation**: `api.arubacloud.com`
  and a Snowplow runtime are unreachable from the build environment, so they are
  validated for shape, not executed. Validate the JQ payloads, the extras key
  layout (`.spec.*` assumption), and Aruba's action payloads against a live
  cluster before production use.
- `updateApiRef` fires on drift detected by native Observe. Desired power state
  is not an OAS field, so these RESTActions assume "keep it powered on"; a
  declarative `spec.powerState` would need an OAS-declared field (evolution report
  §B4/§C1).
