---
type: Runbook
title: Live cluster test
description: The end-to-end create/observe/drift/delete validation and how to repeat it.
tags: [aruba, kog]
timestamp: 2026-08-19T00:00:00Z
---

# Live-cluster test

Everything in this repository had been validated statically until this run. This
page records what a real cluster proved against the **live Aruba Cloud API**,
and — more usefully — the **five defects static validation could not have
caught**.

**Environment:** kind (Kubernetes v1.36.1) · chart 0.9.20 · oasgen-provider 0.19.0 ·
rest-dynamic-controller 0.19.0 · all 34 RestDefinitions applied · authenticated
against the live Aruba Cloud API with an OAuth2 client-credentials token
(`scripts/get-aruba-token.sh`).

## Result

| Check | Result |
|---|---|
| RestDefinitions accepted | **34 / 34** |
| RestDefinitions `Ready` | **34 / 34** |
| CRDs generated | **68** (34 resources + 34 Configurations) |
| RDC controllers running | **34 / 34** |
| `<Kind>Configuration` samples apply | **34 / 34** |
| Sample CRs apply | **34 / 34** |
| Reconcile against the **real API, real credential** | **`Synced=True` / `Ready=True`** — see below |
| Full **create → observe → drift → delete** lifecycle | **passed**, account left exactly as found |

### Authenticated end-to-end run

With a real OAuth2 token (client-credentials, per Aruba's docs) the `Subnet`
controller matched a **pre-existing** subnet and populated status from the live
response:

```
conditions:  Synced=True ReconcileSuccess   Ready=True Available
status.metadata = {"id": "69a560ce2312085699426c28", "name": "automatic-subnet-01"}
log: "External resource is up to date"
```

That single result exercises the whole chain against Aruba's **unmodified**
specification: `apiKey` auth (#49) with `valuePrefix: 'Bearer '`, `findby` over
the `{total, values[]}` envelope, **nested-identifier matching on
`metadata.name`**, and `additionalStatusFields: [metadata.id]` populated with the
real server id — verified identical to what the raw API returns.

**Nothing was mutated.** The run was deliberately scoped to observation: the CR
was pointed at an existing subnet, and the RestDefinition was patched to
`compareScope: identifiersAndStatus` first so a field difference could not trigger
a `PUT` on a real resource. Confirmed after the fact — subnet count unchanged (1),
`updateDate` still months old.

### The three upstream fixes, verified in the cluster

| Fix | Evidence from the running cluster |
|---|---|
| [#49](https://github.com/krateo-platformops/oasgen-provider/issues/49) apiKey auth | `SubnetConfiguration` CRD exposes `authentication.apiKey` = `{tokenRef, header, valuePrefix}`, with `header.default: "Authorization"` and the description *"Defaulted from security scheme "Bearer", which declares "Authorization""*. `DbaasConfiguration` (an `http`/`bearer` spec) exposes only `authentication.bearer` — the per-document derivation is real |
| [#46](https://github.com/krateo-platformops/oasgen-provider/issues/46) `handleParam` | `arubacloud-baremetal-hpc` passes admission with `poll.path: …/monitor/{id}` + `handleParam: id` against Aruba's **unmodified** document |
| [#45](https://github.com/krateo-platformops/oasgen-provider/issues/45) typed maps | Correct but **latent here** — see the scope correction below |

## Defects found (all fixed)

### 1. `oasPath` rejected for every hyphenated ConfigMap key — 31 of 34 RestDefinitions

```
spec.oasPath: Invalid value: "configmap://krateo-system/arubacloud-network-openapi/network-provider.json":
should match '^(configmap:\/\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-zA-Z0-9.-_]+)|https?:\/\/\S+)$'
```

The key segment's class is `[a-zA-Z0-9.-_]` — `.-_` is a **range** (`.` 0x2E → `_`
0x5F), so it *excludes* `-` while accidentally admitting `/`, `:`, `@`, `\`. The
intended form is `[a-zA-Z0-9._-]`. Using Aruba's published filenames as ConfigMap
keys therefore failed; only `project.json` and `metering.json` (no hyphen) got in.

**Fixed here** by using a hyphen-free ConfigMap *key* (`network.json`) — the key is
ours to choose, so the vendored file keeps its published name and its checksum.
`validate.py` now checks every `oasPath` against the CRD's own pattern.

### 2. Sample `apiVersion` wrong on all 34 CRs

Resource CRDs are **not** `v1alpha1`. oasgen derives the version from the OAS
`info.version` (`crdgen.NormalizeVersionName`), so Aruba's `1.0.0` → **`v1-0-0`**
and metering's `1.0` → **`v1-0`**. Every sample CR was rejected with *"no matches
for kind … in version"*.

The companion `<Kind>Configuration` CRD is **always `v1alpha1`** regardless — so
the two halves of each sample pair need *different* versions.

**Fixed here**: samples derive the resource version from the spec; `validate.py`
enforces both rules.

### 3. `<Kind>Configuration` samples rejected on 18 of 34 resources

```
strict decoding error: unknown field "spec.configuration.query.get.ignoreDeletedStatus"
```

The generator hardcoded `ignoreDeletedStatus` on every `get`, but that query
parameter only exists on the endpoints that declare it. A Configuration's schema
admits *only* the parameters its own RestDefinition declared.

**Fixed here**: the per-verb query block is derived from the RestDefinition's own
`configurationFields`, and only `api-version` (required by every Aruba operation)
is given a value.

### 4. A trailing newline in the token Secret breaks auth opaquely — upstream bug

The first authenticated reconcile failed with:

```
net/http: invalid header field value for "Authorization"
```

The token had been produced the way Aruba's own docs lead you to —
`curl ... | jq -r .access_token > file` — and `jq -r` appends a **newline**.
`kubectl create secret --from-file` preserves it, RDC concatenates it into the
header verbatim, and Go rejects the request. The Secret looks perfect under every
normal inspection; the trailing `0a` is visible only under `xxd`. The error names
the URL, not the credential, so the natural first suspicion is the endpoint.

**Fixed here** in `scripts/get-aruba-token.sh` (`jq -j`, plus a whitespace assert
that refuses to write an unusable token). **Filed upstream** as
[rdc#45](https://github.com/krateo-platformops/rest-dynamic-controller/issues/45):
credentials should be `TrimSpace`d, since leading/trailing whitespace is never
meaningful in a bearer token or API key.

*Correction:* an earlier revision of this page said RDC **caches** the resolved
credential, because the fix only took effect after a controller restart. That was
wrong — see "Credential rotation" below. The delay was controller-runtime's
exponential backoff after consecutive failures, which the restart happened to
reset.

### 5. Dynamic Configuration watch forbidden — upstream chart bug

```
subnetconfigurations.arubacloud.ogen.krateo.io is forbidden: User
"system:serviceaccount:krateo-system:oasgen-provider" cannot watch resource
"subnetconfigurations" ... at the cluster scope
```

**88 occurrences in two minutes**, one per generated Kind, retried forever. The
chart's ClusterRole grants `get`+`list` on `apiGroups: ["*"]` but not `watch`,
while the provider registers a dynamic watch on every generated Configuration.

Quiet in the worst way: RestDefinitions still reach `Ready` and controllers still
run, so nothing looks broken — the feature just degrades to resync-only.

**Fixed upstream**: [chart#31](https://github.com/krateo-platformops/oasgen-provider/pull/31).
Verified by patching the live ClusterRole and restarting: **88 errors → 0**.

## Full lifecycle against the live API

A real subnet was created, observed, drift-tested and deleted in project
`cloudburst`. The account finished in exactly the state it started.

| Phase | Result |
|---|---|
| **create** | `status.metadata.id = 6a7061ac45076d045a41643e`, identical to the API's own id. Async provisioning: `Ready=True` after ~150s (`InCreation` → `Active`) |
| **observe** | matched by nested identifier `metadata.name`; a second CR pointed at a pre-existing subnet reported *"External resource is up to date"* |
| **drift** (`compareScope: fullSpec`) | detected, `PUT` issued — **and it can never succeed**, see below |
| **delete** | finalizer released in <15s; subnet went `Deleting` → gone. Back to 1 subnet |

### Finding: an unfixable drift loop (upstream)

I asked for `192.168.99.0/24` and got `192.168.0.0/24`. That is **not** a KOG bug —
the OAS says so explicitly: with `type: Basic`, *"every configuration settings of
the subnet will be automatically handled by the CMP"*. Use `Advanced` to choose
your own CIDR.

The real problem is what happens next. Aruba's two schemas are asymmetric:

| Schema | Fields |
|---|---|
| create — `SubnetPropertiesDto` | `type`, `default`, `network`, `dhcp` |
| update — `SubnetUpdatePropertiesDto` | **`default` only** |

So under `fullSpec` the controller sees `network.address` differ, issues an
update — and the update body **cannot carry `network` at all**. Nothing changes,
and drift is detected again on the next reconcile, forever.

Drift detection is otherwise accurate: the CR whose spec genuinely matched
reality reported "up to date" in the very same log. This is specifically the
*create-only field* case. Filed as
[oasgen-provider#51](https://github.com/krateo-platformops/oasgen-provider/issues/51):
drift comparison should exclude fields the update verb's request body cannot
express — the OAS already carries that information.

`compareScope: identifiersAndStatus` avoids the loop (and is what made this test
safe), but it is blunt — it stops comparing everything except identifiers and
status, so genuinely fixable drift stops being corrected too.

### Minor: delete does not wait for terminal state

The finalizer was released while the remote resource was still `Deleting` (it
completed ~20s later). Harmless here, but recreating the same name immediately
after a delete could collide.

## Credential rotation works without a restart (ESO is viable)

Because the intended credential lifecycle here is **External Secrets Operator**
rather than anything built into oasgen/RDC, the load-bearing question is whether a
rotated Secret takes effect on its own. Tested directly on the live cluster, with
no restart and no reload signal:

| Step | Result |
|---|---|
| baseline | `Synced=True` |
| Secret swapped to a bogus token | **fails after 20s** |
| Secret restored to the valid token | **recovers after 160s** |

So the credential is re-read on **every reconcile** (`GetSecret` sits inside
`processConfigurationRef`, which `Get()` calls per reconcile — there is no cache),
and an ESO-driven rotation propagates by itself.

One practical consequence: recovery took 160s, not 20s, because consecutive
failures back off exponentially. **Refresh well before expiry** — with Aruba's
~1h token, an ESO `refreshInterval` of ~30m means the credential never actually
lapses and no backoff window is ever entered. Letting it expire first is what
turns a seamless rotation into a multi-minute outage.

## Scope correction: #45 (typed maps)

The generated CRDs contain **zero** `additionalProperties` — because all 42 of
Aruba's typed maps sit in **response/catalog DTOs** and *none* appears in a create
request body (verified by resolving `$ref`s across all 12 documents). The CRD spec
is built from request bodies, so the fix is correct but **latent** for this API.
This document previously claimed the maps "keep their value type in the generated
CRDs" — overstated, now corrected in §A2.

## Install note

The `RestDefinition` CRD ships as a **separate chart** (`krateo-oasgen-provider-crd`).
Installing only `krateo-oasgen-provider` leaves you with a running provider and no
CRD. Both are now in the README install steps.

## Not covered

Auth, observe, create, drift and delete are all now proven against the live API.
What remains unexercised:

- **The HPC async poll loop** reaching `Succeeded` (`baremetal/Hpc` — bare metal,
  expensive) and the **CloudServer RESTAction** sequences (`createApiRef`/
  `updateApiRef`/`deleteApiRef` via Snowplow, which also needs snowplow deployed
  and `URL_SNOWPLOW` set).
- **ESO** end to end — the premise (rotation without restart) is verified, the
  manifests in [authentication](authentication.md) are not.
- The other 30 resource kinds: only `Subnet` was driven through a full lifecycle.

## Reproducing

```sh
kind create cluster --name aruba-kog-test
helm install oasgen-provider-crds oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds --version 0.21.1 -n krateo-system --create-namespace --wait
helm install oasgen-provider      oci://ghcr.io/krateo-platformops/charts/oasgen-provider      --version 0.21.1 -n krateo-system --wait
kubectl apply -n krateo-system -f configmaps/
kubectl apply -n krateo-system -R -f restdefinitions/
kubectl get restdefinitions.ogen.krateo.io -n krateo-system   # wait for Ready
kubectl apply -f samples/arubacloud-token-secret.yaml         # edit the token first
kubectl apply -f samples/network/subnet-configuration.yaml -f samples/network/subnet.yaml
```
