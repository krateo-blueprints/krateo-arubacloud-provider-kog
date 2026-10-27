---
type: Architecture
title: Lifecycle beyond CRUD
description: Actions that are not create/read/update/delete and how they are modelled.
tags: [aruba, kog]
timestamp: 2026-09-05T00:00:00Z
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

> **Correction (2026-09).** This page long claimed CloudServer "has NO single create
> endpoint". That is wrong, and the mistake was ours. `POST .../Aruba.Compute/cloudServers`
> exists and is fully specified — but **only in `compute-provider_v1.1.json`**
> (`info.version: 1.1.0`), a document whose entire content is that one path. Everything
> else — `findby`, `get`, `delete` and all eight action endpoints — lives in
> `compute-provider.json` (`1.0.0`).
>
> A RestDefinition carries a **single `oasPath`**, so its verbs cannot span two
> documents. That, not the absence of an endpoint, is why create is delegated here.
> Worse, the 1.1 document was vendored but never generated into a ConfigMap, so oasgen
> could not have seen the endpoint even if the API allowed it. That is now fixed
> (`configmaps/compute11-openapi.yaml`).
>
> The create body is complete: `metadata.{name,location,tags}` plus
> `properties.{flavorName, vpc.uri, subnets, securityGroups, bootVolume.uri,
> keyPair.uri, elasticIp.uri, dataCenter, userData, vpcPreset}`.
>
> **And create really is a single call.** All four of Aruba's first-party
> implementations — `sdk-go`, the Terraform provider, `arubacloud-resource-operator` and
> `acloud-cli` — create a server with **one POST** carrying vpc, bootVolume, keyPair,
> elasticIp, subnets and securityGroups in the body. There is no power-on step, no
> associate step and no attach step at create time. The action endpoints this page
> describes are **day-2 operations**, not part of provisioning.
>
> Filed upstream as
> [oasgen-provider#108](https://github.com/krateo-platformops/oasgen-provider/issues/108):
> allow a verb to override `oasPath`. That is the whole blocker — not the shape of the
> API.
>
> Three preconditions Aruba documents nowhere in the OAS, all found in their own code:
>
> - Dependencies must be settled first, and the settled state differs by kind: VPC,
>   Subnet, SecurityGroup and KeyPair must be `Active`, while an unattached ElasticIp or
>   BlockStorage must be **`NotUsed`** (`sdk-go` defines `WaitUntilNotUsed` as exactly
>   that state).
> - **The boot volume must be created with `bootable: true` and an `image` code.** A
>   plain `BlockStorage` — which is what this repository's storage wave creates — will
>   never boot.
> - Creating before the boot volume or subnets reach a final state does not fail, it
>   **stalls**. Aruba's own operator requeues instead of POSTing, with
>   `// TODO: Remove this block once the CMP Infra Team fixes the root cause`. A create
>   issued too early hangs *and bills*.

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

- Chart: [`compositions/aruba-network-environment/`](../compositions/aruba-network-environment/)
  renders a `Vpc`, `Subnet`, `SecurityGroup` and `CloudServer` plus their
  `*Configuration` resources, all parameterised by shared values
  (`projectId`, `location`, `name`, CIDRs, flavor…).
- Registration: [`compositions/compositiondefinition.yaml`](../compositions/compositiondefinition.yaml)
  (`core.krateo.io/v1alpha1`) points at the packaged OCI chart, the same shape
  oasgen-provider's own chart uses.

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

3. **Snowplow must be >= 1.12.57**, a version floor rather than a preference, for two
   defects confirmed in snowplow's own code on 2026-10-26.

   **[snowplow#904](https://github.com/krateo-platformops/snowplow/issues/904) — a
   failed stage does not stop a later write, and `/call` still answers 200.** Verified
   upstream against a read → detach (409) → delete sequence: the DELETE is sent anyway,
   the stage error is recorded under `errorKey`, and a filter can hide it completely.
   No header marks it today.

   For CloudServer the consequence is specific, and **our existing safety net does not
   catch it**. RDC's delete verification checks the *server* via the observe verb, so a
   failed volume detach followed by a successful server delete verifies as clean: the
   server is genuinely gone, the finalizer correctly releases, and the data volumes are
   orphaned with nothing referencing them. The net catches "the delete did not happen",
   not "the delete happened and left the wrong things behind".

   Note that #904's fix does **not** automatically cover
   `cloudserver-delete.restaction.yaml`. It skips a write step whose `dependsOn` step
   failed, unless `continueOnError` is set — and here `delete` depends on `server`, not
   on `detachVolumes` (they are siblings off the same read), with `continueOnError:
   true` on all three stages. Both exemptions apply. The correction is three lines, not
   the two an earlier version of this note claimed: `continueOnError` has to come off
   `detachVolumes` as well, or a **failed server read** still deletes the server —
   detach runs anyway, its iterator over an absent server yields zero items, zero items
   succeeds, and the delete is waved through. That was measured on
   [snowplow#911](https://github.com/krateo-platformops/snowplow/pull/911) (POST 0,
   DELETE 1). The exact diff, the outcome table and the tests to add with it are
   recorded in that manifest, along with why it must not be made before the floor.

   **[snowplow#903](https://github.com/krateo-platformops/snowplow/issues/903) —
   background work can send write stages.** Below 1.12.57 Snowplow's background workers
   can run a RESTAction's write stages with the endpointRef credential; the "background
   only reads" guard covers only snowplow's own service account. CloudServer's three
   RESTActions are not exposed in normal operation — no widget references them, they
   carry no `userAccessFilter`, and a resolve that reached the Aruba API is never
   cached — but if the endpointRef Secret is absent during a resolve the result can be
   cached truncated, and a later refresh would run the full read → detach → delete
   against a live server with no user action. **Never let a widget reference these
   RESTActions.**

   There is no runtime assertion for this floor: a RestDefinition cannot read
   snowplow's version, and `/call` exposes none. It is documented and not enforced.

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
