---
type: Runbook
title: Live cluster test
description: The end-to-end create/observe/drift/delete validation and how to repeat it.
tags: [aruba, kog]
timestamp: 2026-10-15T00:00:00Z
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

## Re-verified on 0.21.1 (2026-08-31)

Repeated after the monorepo migration, against the current release, to check that
the two-minor jump from 0.19.0 changed nothing in practice. It did not.

| Check | Result |
|---|---|
| RestDefinitions accepted / `Ready` | **34 / 34** |
| CRDs generated · RDC controllers ready | **68** · **34** |
| RDC image (derived from chart `appVersion`) | `rest-dynamic-controller:0.21.1` — lockstep works |
| Samples validated server-side against the generated CRDs | **68 / 68** |
| Provider `ERROR` lines in 2 min | **0** — the `watch` RBAC fix holds |
| Generated CRD versions | resource `v1-0-0` / `v1-0`, Configuration `v1alpha1` — unchanged |

This is the empirical counterpart to the source diff: the `apis/restdefinitions`
tree differs by two lines across 0.19.0..0.21.1 (both the Go module path move), and
the cluster agrees.

Still unfixed upstream: the `oasPath` key-segment regex, so hyphen-free ConfigMap
keys remain necessary
([#74](https://github.com/krateo-platformops/oasgen-provider/pull/74) open).

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

---

## GA lifecycle run — 2026-09-01

Driven with [`scripts/ga-lifecycle-test.sh`](../scripts/ga-lifecycle-test.sh) on
kind `aruba-ga`, oasgen-provider/RDC **0.21.1**, against the live Aruba API.

### `compute/KeyPair` — create → observe → delete

| Step | Evidence |
|------|----------|
| create | CR `Ready=True Synced=True`; upstream id `6a9659ebae7f206033039d45` |
| observe | forced re-reconcile returned the same id — a real round-trip, not stale status |
| delete | controller issued `DELETE .../keyPairs/6a9659…`; Aruba returned `state: Deleting` |
| residue | none — 0 keypairs in the project 45s later |

### `network/Vpc` — create → observe → **drift** → delete

| Step | Evidence |
|------|----------|
| create | upstream id `6a965b3f823ddef7e8717970`, `state: Active` |
| observe | id matched `status.metadata.id` |
| drift | `tags` changed **out of band** to `["drifted-by-hand"]`; controller restored `["ga-expected"]` within ~40s |
| delete | `state: Deleting`, then gone |
| residue | none — only the pre-existing `cloudburst-default` VPC remains, its subnet untouched (`updateDate` still 2026-03-07) |

Credential rotation was exercised incidentally and again needed **no restart**: the
controllers were failing `401` against an expired token and recovered on their own
once the Secret was updated. That is the third independent confirmation of the ESO
premise in [authentication](authentication.md).

### `project/Project` — create → observe → **drift** → delete

| Step | Evidence |
|------|----------|
| create | upstream id `6a965fdd4300bf195e381808` |
| drift | `properties.description` changed out of band to `DRIFTED-BY-HAND`; controller restored it in ~3 min |
| delete | `GET` returned **404** |

Note the reconcile latency difference: the Vpc slice drift corrected in ~40s, this
scalar in ~3 min. Both are within the backoff window; neither is a defect, but a
drift test that gives up after a minute would wrongly call this one a failure.

### `project/Folder` — create → observe → delete (beta, not GA)

Created as `6a9661334300bf195e381832` and cleanly removed, but **drift was
deliberately not exercised**: its only mutable spec fields are `name` — which is its
identifier, so perturbing it tests rename semantics rather than drift — and
`default`, an account-wide flag whose value affects other resources. It stays beta
rather than being promoted on a test that was never run.

Its status is also shaped differently from the metadata-wrapped resources: a flat
`status.id`, not `status.metadata.id`.

## Two defects this run found

### Empty arrays in a CR spec are unenforceable ([oasgen-provider#76](https://github.com/krateo-platformops/oasgen-provider/issues/76))

The first drift attempt — `tags: []` in spec versus `["drifted-by-hand"]` upstream —
was **never corrected**, while the controller logged *"External resource is up to
date"* and the CR reported `Ready=True`. The cause is in RDC's `compareSlices`:

```go
// If the second slice is longer, we ignore the extra elements because
// we only compare fields that exist in the first map.
if len(valueSlice) > len(rmSlice) { ...not equal... }
for i, v := range valueSlice { ... }
```

With an empty CR slice, `0 > 1` is false and the loop body never runs, so the
comparison returns equal. **An empty array in spec matches any remote array.**
Consequences beyond tags: emptying any list is invisible to drift detection, and
because comparison is positional, so is reordering with a shorter CR slice. The
same subset rule applies to maps, where it is deliberate — `configurationRef` must
be ignored — but for slices it silently weakens the declarative guarantee.

Verified by contrast: a **non-empty** mismatch (`["ga-expected"]` vs
`["drifted-by-hand"]`) was detected and corrected in ~40s, so the `update` verb
itself is sound.

### Delete finalizers release on "requested", not "completed"

`kubectl delete` returned in **0.388s** while Aruba still reported
`state: Deleting`. Deletion did complete here, but the finalizer released on the
DELETE being *accepted*. If the asynchronous deletion later failed — quota, a
dependency, an upstream error — the CR would already be gone with nothing left to
retry, leaving a billable orphan and no Kubernetes object to show for it. The
`async` block already models poll-until-complete for create; delete has no
equivalent.

---

## 0.22.1 verification — 2026-09-02

Upgraded from 0.21.1 to verify the three cross-cutting fixes against the live API
rather than trusting their unit tests. 34/34 RestDefinitions Ready; all 35 generated
controllers moved to `rest-dynamic-controller:0.22.1`.

| Fix | Verdict | Evidence |
|-----|---------|----------|
| [#76](https://github.com/krateo-platformops/oasgen-provider/issues/76) empty-array drift | **works** | The scenario that previously failed forever — spec `tags: []` vs upstream `["drifted-by-hand"]` — was corrected in ~3 min |
| [#75](https://github.com/krateo-platformops/oasgen-provider/issues/75) read-only identifier | **works** | `LoadBalancer` now generates a `name` field described as a *SELECTOR*; a CR carrying it is accepted, where it was previously rejected with `strict decoding error` |
| [#77](https://github.com/krateo-platformops/oasgen-provider/issues/77) delete verification | **regressed** | See below — filed as [#98](https://github.com/krateo-platformops/oasgen-provider/issues/98) |

### Regression: delete now hangs forever

Holding the finalizer until the resource is verified gone is right, but a **404 on
DELETE** is treated as a retryable error rather than as the success condition it is.
Once the deletion completes, every retry 404s and the finalizer never releases:

```
$ time kubectl delete vpc.arubacloud.ogen.krateo.io ga-drift80
vpc... "ga-drift80" deleted
error: timed out waiting for the condition        elapsed: 300s
```

The external resource was gone (`HTTP 404`) but the CR sat in `Deleting` with its
finalizer until removed by hand. `Observe()` already handles
`restclient.IsNotFoundError`; the delete path does not. On an API that deletes
asynchronously this means **resources cannot be deleted through Kubernetes at all**,
so 0.22.1 is not safe for the storage or database waves until it is fixed.

### Upgrading does not regenerate existing CRDs

`LoadBalancer` still had its old identifier-less schema after the upgrade, and the
provider logged *"External resource is up to date"*. The CRD dated from before the
upgrade. Only deleting and re-applying the RestDefinition produced the fixed schema.

Generated controller **Deployments** were updated (all 35 moved to 0.22.1), so the
staleness is specific to the CRD schema. A generator fix therefore does not reach
already-deployed resources on upgrade, which is a trap for anyone upgrading
specifically to obtain one.

### P0-1 closed: token rotation proven unattended

The client credential was stored `2026-09-01 06:25`. The token in use on
`2026-09-02 11:09` was issued by ESO that morning, ~29 hours and ~29 expiries later,
with no human involvement, and returns HTTP 200. Rotation is no longer sketched — the
install has demonstrably survived past expiry unattended.

### 0.22.2 — delete regression fixed, all three defects now verified

[#99](https://github.com/krateo-platformops/oasgen-provider/pull/99) treats a 404 on
DELETE as success. Re-tested on 0.22.2 with a fresh VPC (`6a98aacd823ddef7e871b08c`):

```
$ time kubectl delete vpc.arubacloud.ogen.krateo.io ga-del98
vpc... "ga-del98" deleted
elapsed: 17s
$ kubectl get vpc ga-del98            -> NotFound
$ GET .../vpcs/6a98aacd823ddef7e871b08c  -> HTTP 404
```

The 17 seconds are the point. The original defect returned in **0.388s** without
verifying anything; the 0.22.1 regression hung for the full **300s** timeout. Taking
17s and then releasing is the resource actually being confirmed gone.

All three cross-cutting defects — [#75](https://github.com/krateo-platformops/oasgen-provider/issues/75),
[#76](https://github.com/krateo-platformops/oasgen-provider/issues/76),
[#77](https://github.com/krateo-platformops/oasgen-provider/issues/77)/[#98](https://github.com/krateo-platformops/oasgen-provider/issues/98)
— are now fixed and verified against the live API. The storage and database waves are
no longer gated on an orphan risk.

### Wave 1 complete — dependent network chain, 0.22.2

`scripts/ga-chain-test.sh`, first clean end-to-end run:

```
vpc-a=6a98b403  vpc-b=6a98b440  sg=6a98b4ec  rule=6a98b57a  peering=6a98b79d
drift failures: 0
teardown: all 5 deleted in reverse dependency order
residue check: 0 — clean on first attempt
```

Drift was injected **out of band** on each of the three: the object was re-`PUT` with
`tags: ["drifted-by-hand"]` while the CR declared `tags: []`, and the controller
removed it. That is deliberately the scenario
[#76](https://github.com/krateo-platformops/oasgen-provider/issues/76) got wrong — an
empty list used to match any remote list, so this exact assertion would have passed
vacuously before 0.22.1. It is now a real test, and it passes.

`SecurityGroup`, `SecurityRule` and `VpcPeering` are promoted to **GA**. `VpcPeering`
is free: it declares no `billingPlan`, unlike `VpcPeeringRoute`, which stays opt-in.

Two harness defects were fixed to get here, both found by running it:

- **Teardown lost its registry to a subshell.** `VPC_A=$(apply_wait ...)` runs the
  function in a subshell, so `CREATED+=(...)` was discarded and teardown saw one
  resource of five. `apply_wait` now sets `LAST_ID` and is never called via `$( )`.
  This run is the first time the `EXIT` trap has actually done its job.
- **The scripts read a stale `/tmp/aruba.token`** and aborted with a 401 while the
  cluster itself was perfectly authenticated. They now read the ESO-managed Secret
  first, falling back to the file only for a manual bootstrap.

### Wave 3 — storage, on the declarative runner

Every resource in this chain **bills** (all four declare `billingPeriod`), so it was
run at the minimum size and torn down immediately.

```
BlockStorage 6a99c2e6  drift corrected
Snapshot     6a99c3f9  drift corrected
Backup       6a99c4f7  drift corrected
Restore      6a99c631  created and observed
teardown: 4/4 deleted in reverse dependency order
residue: 0 — verified again independently across all storage endpoints
```

`BlockStorage`, `Snapshot` and `Backup` are promoted to **GA**. `Restore` stays beta:
it has an `update` verb, so the GA bar requires drift, and drift was not exercised
on it.

Three things this wave found:

- **Async updates return 202, not 200.** The drift check treated anything but 200 as
  a failure and silently *skipped* itself on the first run — reporting SKIPPED rather
  than a pass, which is the only reason it was noticed. Storage updates are queued.
- **`Backup.type` is required by the API and optional in the OAS.** `Type is
  required`, with no enum declared anywhere in the spec.
- **`BackupPolicy.resourceType` must be exactly `"volume"`.** Also a bare string
  upstream.

That is now four separate cases of Aruba enforcing a constraint its OpenAPI does not
express — alongside the state enum and the `steps` cardinality rule. Each one is only
discoverable by sending a request and reading the 400.

### Wave 4 (security) — one defect of mine, one upstream

`Kms` created, observed, and **drift corrected** (`6a99c7c3`). Then the chain broke in
a way worth recording in full, because the two failures compounded.

**Mine.** The `Key` RestDefinition mapped `status.id`, but Aruba's findby item carries
the server id as **`keyId`**:

```json
{"keyId": "ebe348c2…", "name": "ga-key", "algorithm": "Aes", "status": "Active"}
```

So `status` was never populated and the CR never went Ready — while the key **was
created**. On teardown its delete path had an unresolvable `{keyId}`, and RDC's
documented behaviour for that case is to release the finalizer *without calling the
API* (correctly — nothing can re-derive the identifier). The result is a real key with
no Kubernetes object left pointing at it.

The generator now takes the status field from the API's own response key
(`statusField`), so `Key` maps `status.keyId` and `Kmip` maps `status.kmipId`. My
override table even carried the note *"item keyed by server id {keyId}"* — the fact
was recorded and the mapping still said `id`.

**Upstream.** The orphaned key then blocked deleting its parent: Aruba refuses with
`Some kms keys are not deleted`. After I removed the key by hand and the KMS was gone —
`GET` returning **404** — the controller's `DELETE` kept returning **400** with that
same stale message, and the CR hung in `Deleting` until the finalizer was stripped.

That is [#101](https://github.com/krateo-platformops/oasgen-provider/issues/101):
#99 special-cased 404 on DELETE, but the `externalResourceStillExists` check is
unreachable whenever DELETE errors at all, so a non-404 error for an absent resource
still hangs. The observe verbs are the ground truth and the code already knows how to
consult them.

`Kms` is **beta**: create, observe and drift are proven, delete is not.

#### Security, second run (corrected mapping)

The orphan is gone: `Kms` created, `Key` failed cleanly, and **teardown removed
everything with residue 0** — where the first run left a key behind that made its
parent undeletable. The account was then verified clean across all eight endpoints.

Two things still open:

- `Key` reports **no upstream id** even with `additionalStatusFields: [keyId]`, so
  observe remains unproven. The mapping fix stopped the orphan without yet making the
  resource work.
- `Kms` drift injection returned **400** on this run where it returned 200 on the
  first, most likely because the KMS was still settling. Every individual step is now
  proven — drift in run 1, delete in run 2 — but never in a single pass, so it stays
  **beta**: GA should mean one clean end-to-end run, not a union of partial ones.

#### The id-field lesson, in full

Three attempts, three different wrong answers, all from the same provider:

| Resource | Path parameter | **Response field** | What I assumed |
|----------|----------------|--------------------|----------------|
| `security/Key` | `{keyId}` | `keyId` | `id` — status never populated, key orphaned |
| `security/Kmip` | `{kmipId}` | **`id`** | `kmipId` — status never populated, kmip orphaned |
| `project/Folder` | `{id}` | `id` | `id` — correct by luck |

The first fix replaced "always `id`" with "use the path-parameter name", which fixed
Key and broke Kmip in exactly the same way. Both guesses were wrong because both were
guesses: **the findby response schema is the only authority**, and it is right there
in the OAS.

The generator now derives it (`response_id_field`), preferring a literal `id`, then
any `*Id` field, and returning **None** when there is none at all — which is what
distinguishes a name-keyed resource like `Database` or `Grant` from one whose id is
merely called something else. Conflating those two cases is what sent the mapping to
`spec.name` for resources that do have a server id.

The failure mode is worth stating plainly because it costs money: a CR whose status is
never populated cannot address its own resource on delete, so RDC releases the
finalizer without calling the API — correctly, since nothing can re-derive the
identifier — and the real resource is left running with no Kubernetes object pointing
at it. For `security` that also blocks deleting the parent KMS, which is how one bad
mapping turned into a manual cleanup twice.

All 34 mappings are now derived rather than declared, so this class is closed for
every resource, not just the two that failed.

### AlertRule: the create body and the read responses disagree

Found by the adversarial derivation, then confirmed straight from `metering.json`:

| | shape |
|---|---|
| `POST /alertRules` body | `{metadata, properties}` — metadata-wrapped |
| `GET /alertRules` items | `{id, alertModel, request, resourceVersion, …}` — **flat** |
| `GET /alertRules/{id}` | same flat DTO |

The generator derived `meta` from the **create body**, so the RestDefinition declared
`identifiers: [metadata.name]` and `additionalStatusFields: [metadata.id]`. Neither
field exists in anything the API returns, which means:

- **findby can never match**, so the controller concludes the resource does not exist
  and POSTs again — every reconcile, without bound, against a live account.
- **`status.metadata.id` never populates**, so delete has an unresolvable path
  parameter and RDC releases the finalizer without calling the API — orphaning
  whatever was created.

This is the same failure that orphaned `security/Key`, but with a worse first half:
Key created one resource and lost track of it, whereas this would create resources
*repeatedly*.

`meta` is now derived from the **response**, because identifiers and status fields are
read from the response — the create body has no say in it. Only `AlertRule` changed
across all 34; `KeyPair`, which posts and returns metadata, is untouched and its GA
evidence stands.

Two further notes on AlertRule, both blocking:

- `Aruba.Insight/alertRules` returns **404** on this account and `Aruba.Metering/…`
  returns 400, so the resource may not be reachable here at all.
- Its remaining spec fields (`serviceTypology`, `metric`, `rule`, `theshold`, `um`,
  `duration`, `state`) have **no enum in the OAS**, and the verifier's advice was to
  read legal values from a live findby response — which 404 makes impossible.

### `compareScope: updatable` applied where the update body is narrower

Derived, not declared: for every resource with both verbs, the create body's leaf
paths are compared with the update body's. Where create can express something update
cannot, that difference is **unfixable** — the controller sees it, calls UPDATE, the
update cannot carry the field, and the difference is still there next reconcile,
forever, while the resource bills. That is exactly what
[oasgen-provider#51](https://github.com/krateo-platformops/oasgen-provider/issues/51)
added `compareScope: updatable` for, shipped in 0.22.1 and until now unused here.

Eight resources qualify:

| Resource | create → update leaves | fields update cannot fix |
|---|---|---|
| `container/Kaas` | 20 → 11 | `autoscalerProfile`, `identity.*`, `nodeCidr.*`, … |
| `container/Registry` | 11 → 5 | `adminUser.username`, `blockStorage.uri`, `size`, … |
| `container/KaasBackup` | 6 → 4 | `retentionDays`, `type` |
| `network/VpcPeering` | 4 → 2 | `metadata.location.value`, `properties` |
| `network/VpcPeeringRoute` | 4 → 2 | `metadata.location.value`, `properties` |
| `network/SecurityRule` | 4 → 3 | `properties` |
| `network/VpnRoute` | 4 → 3 | `properties` |
| `security/Key` | 2 → 1 | `algorithm` |

**Re-verified rather than assumed.** `SecurityRule` and `VpcPeering` were already GA on
drift evidence, and narrowing their comparison changes what drift means for them. The
free network chain was re-run with the new scope live:

```
DRIFT CORRECTED for SecurityGroup/ga-chain-sg
DRIFT CORRECTED for SecurityRule/ga-chain-sr
DRIFT CORRECTED for VpcPeering/ga-chain-peering
drift failures: 0        residue: 0 — clean
```

`metadata.tags` lives in both bodies, so fixable drift is still detected; only the
unfixable comparison is dropped. The GA claims stand on re-executed evidence.

### VpcPeeringRoute — six resources for one

A peering route cannot be tested alone. Its `localNetworkAddress` and
`remoteNetworkAddress` are only legal if subnets with **exactly those CIDRs** exist in
the two peered VPCs, and the subnets must be `type: Advanced` — `Basic` lets the CMP
assign the address, and the literals would then never match.

```
Vpc/ga-pr-vpc-a          6a9a52d9
Vpc/ga-pr-vpc-b          6a9a5316
Subnet/ga-pr-subnet-a    6a9a53c2   Advanced, 10.100.0.0/24
Subnet/ga-pr-subnet-b    6a9a5409   Advanced, 10.200.0.0/24
VpcPeering/ga-pr-peering 6a9a54c0
VpcPeeringRoute/ga-pr-route 6a9a556c
teardown: 6/6 in reverse order      residue: 0 — clean
```

This exposed a real limitation in the runner: ids were keyed by **kind**, so a chain
with two VPCs silently overwrote the first, and `${id:Vpc}` meant "whichever ran last"
— which would have peered a VPC with itself. Ids are now keyed by kind **and** CR name,
so `${id:ga-pr-vpc-b}` is unambiguous. `${id:Kind}` still works for single-instance
chains.

`VpcPeeringRoute` is **beta**: create, observe and delete are proven; drift was not
exercised. It is now a candidate for GA on the next run, since `compareScope: updatable`
makes its `metadata.tags` the only compared field — its update body carries metadata
only, which is exactly why that scope was applied to it.

### Database chain — two proven, two blocked by an unguessable password policy

`Dbaas` provisioned on the pre-existing account network (deliberately: the chain binds
to `cloudburst-default` rather than creating its own VPC, and its `residue` lists only
the dbaas endpoint so teardown can never treat the shared VPC as leftover).

```
Dbaas        6a9a846d752ef81c21ebe520   mysql-8.0 / DBO1A2 / 20 GB   Ready
Database     gadb                       name-keyed, resolved via idPath: name
DatabaseUser —                          BLOCKED
Grant        —                          blocked behind DatabaseUser
```

**`DatabaseUser` cannot be created from any published information.** The API rejects
every attempt with:

```
{"fieldName":"Password","errorMessage":"Password does not match the minimum requirements."}
```

and the OAS declares `password` as a bare string — no `minLength`, no `pattern`, no
description beyond "The password to assign to the new database user." Three values
were tried:

| Value | Length | Result |
|---|---|---|
| `Ga!Test123456Ac@` | 16 | rejected |
| `Xq7#vNb2$wRt5Zk9` | 16 | rejected — no dictionary word, no sequence |
| `Prova123456789AC@` | 17 | rejected — **Aruba's own SDK example value** |

The third is the decisive one. When the vendor's own documented example fails its own
validator, the rule cannot be derived from published sources at all, and guessing
further costs a billable reconcile each time. This is not a payload problem to solve by
reading harder.

It also sharpens the existing P2. `DatabaseUser.password` being a plaintext spec field
was already a reason not to promote the `database` provider; that the field's accepted
values are undocumented makes it doubly unusable — an operator cannot even construct a
valid one without trial and error against a paid instance.

`Dbaas` drift was also not provable: injection returned **400**, because the generic
mechanism re-`PUT`s the whole fetched object and the DBaaS update body will not accept
it. So `Dbaas` and `Database` are **beta** — created and observed, not drift-proven.

Account verified clear afterwards: 0 dbaas instances, no CRs.

### Container wave — Kaas created, but the CR never reports Ready

Priced before running, from Aruba's published figures: worker `K2A4` **EUR 0.026/hr**,
HA master **EUR 0.05/hr** (charged separately, so `ha: false`), Registry Small
**EUR 0.01233/hr**. The whole wave for an hour is under EUR 0.10 — cheaper than the
20 GB volume runs already done.

**`K1A2` is not orderable.** Aruba's metadata page lists it as a KaaS node size, but
the pricing page's smallest flavour is `K2A4`. The derived payload used K1A2 and would
have failed; this only surfaced because the cost was checked first.

Four undeclared constraints, each costing one attempt:

| Attempt | Error |
|---|---|
| `preset: true` | `Preset: Another vpc is already existing in location ITBG-Bergamo` — preset only works when the location has **no** VPC, and `cloudburst-default` exists |
| wired to existing network | `NodeCidr: Field is required` (plus `.Name`, `.Address`) — the OAS marks none of them required |
| reused `automatic-sg-01` | `SecurityGroup.Name: Name is not valid` — it wants a **new** name; the cluster creates its own |
| provisioning | `Status: Invalid status` on every UPDATE while the cluster is `InCreation` |

That last one is a controller behaviour worth naming: **RDC attempts an update while the
resource is still provisioning**, producing repeated 400s until the cluster goes
`Active`, at which point `Synced` flips to `True` on its own. Harmless here, but it is
the same class as the Kmip `invalid status` delete — an API with a state machine needs
the controller to wait for a stable state, and only `create` has an `async` block for
that today.

Final state: cluster `6a9aa9566520798027656a3d` reached **Active** upstream, `Synced=True`,
`status.metadata.id` populated — but `Ready` stayed `False/Creating` for its whole life.
So `Kaas` is **beta**: create and delete are proven and the id was observed, but the CR
never converged to Ready, so observe cannot be claimed.

Teardown was complete, including the security group and node subnet the cluster created
for itself: only `automatic-sg-01` and `automatic-subnet-01` remain.

### schedule/Job is blocked behind CloudServer

A Job needs exactly one `steps[]` entry, and the API rejected a read-only step outright:

```
Steps -> All steps must have a correct HttpVerb defined (POST, PUT, PATCH, DELETE).
Steps -> Not found step typology.
Steps -> Not found configuration for typology
```

Aruba's metadata documentation settles what a step may be: **`POST` only**, and *"Only
`poweron` and `poweroff` are currently supported"*, with `body` required to be null.
So a Job is a power operation on a `CloudServer` and nothing else.

The account has no cloud servers, and `CloudServer` creation is itself blocked on
snowplow (P0-3). Job is therefore **blocked**, not merely untested — and no amount of
payload work changes that.

Worth recording against the earlier derivation: its verifier refuted `actionUri:
poweroff` as an invented literal. The caution was reasonable, but the value was
**correct** — it is one of exactly two the API accepts. Adversarial verification is
worth its cost, and it is not infallible in either direction.

### security/Kms and security/Key reach GA

One clean run, with `Kmip` excluded — its state machine wedges teardown and blocks the
parent Kms, which is what spoiled the two earlier attempts:

```
Kms  6a9ae569445ca0a1d0d19a00        drift corrected
Key  b6ae8eeef24e416b96866c92a...    Ready, status.keyId populated
teardown 2/2 · residue 0 — clean
```

`Kms` is **GA**: every step proven, and this time in a single pass rather than as the
union of two partial runs.

`Key` is **GA** on `create → observe → delete`, and the reason drift is excluded is
structural rather than an excuse — its update body is:

```json
{"type": "object", "properties": {"name": {"type": "string"}}, "additionalProperties": false}
```

`name` is also its identifier. There is no non-identifying field to converge, so drift
has nothing to act on — the same position as `Restore`, whose update body is empty
outright. Perturbing the identifier would test rename semantics and risk the controller
creating a duplicate billable key, which is a different thing entirely.

This also closes out the mapping saga: the `keyId` fix works end to end, the key is
addressable on delete, and no orphan is left to block the parent.

### BackupPolicyAssignment — drift proven, delete wedged (and I made it worse)

```
BlockStorage 6a9ae926   Ready      (billable, 20 GB)
BackupPolicy 6a9ae9d4   Ready      (free)
BackupPolicyAssignment 6a9aea83   Ready, DRIFT CORRECTED
```

Create, observe and drift all pass. **Delete does not.** The first DELETE is accepted —
the assignment moves to `state: Deleting` — and every subsequent DELETE returns
`400 Invalid status`, so the finalizer never releases. Same shape as
[#101](https://github.com/krateo-platformops/oasgen-provider/issues/101): the resource
is mid-transition, DELETE errors with a non-404, and RDC's existence check is
unreachable because the error path returns first.

**My own mistake made it permanent.** When teardown stalled I force-deleted the
`BlockStorage` directly, out of dependency order, while the assignment still referenced
it. The assignment has been stuck in `Deleting` ever since and does not clear — a
resource that binds a policy to a volume cannot finish deleting once its volume has
vanished underneath it. Six further DELETEs all returned 400.

The volume — the only billable member — is gone. The assignment and its policy are
free, but they are **residue that needs removing from the Aruba console**, and that is
on me, not on the provider.

The lesson is the one the runner already encodes and I overrode by hand: tear down in
reverse dependency order, and when a delete stalls, wait rather than reach past it.

### VpnTunnel proven; VpnRoute blocked on two undeclared fields

`VpnTunnel` created and observed (`6a9b3576`, `Site-To-Site` / `ikev2`) and deleted
cleanly. Its enum values came from the CRD field descriptions, which spell them out
verbatim — *"Admissable values: - ikev2"* — one of the few places Aruba does document a
constrained string. Drift was **skipped**: injection returned 400, because the generic
mechanism re-`PUT`s the whole fetched object and the tunnel's update body rejects it,
exactly as with `Dbaas`. So it is **beta**.

The secret was never committed. `PSK_SECRET` is declared empty in the fixture and the
runner now refuses to start until it is supplied from the environment:

```
chain 'vpn' needs these vars supplied from the environment: PSK_SECRET
```

The run used `GA_PSK_SECRET="$(openssl rand -base64 24)"`, and `peerClientPublicIp` is
`203.0.113.1` — RFC 5737 TEST-NET-3 — so the tunnel could never negotiate with a real
host.

`VpnRoute` is **blocked**. Every attempt returns:

```
Properties.CloudSubnet  -> subnet not found
Properties.OnPremSubnet -> network address is invalid
```

`cloudSubnet` was given the CIDR of the subnet the tunnel itself created
(`192.168.60.0/24`, confirmed present in the VPC), and `onPremSubnet` was tried as both
RFC 5737 documentation space and RFC1918 private space. Both were rejected. The OAS
describes them only as "Cidr of the cloud subnet" / "Cidr of the onPrem subnet" with no
pattern, no enum and no note about what "not found" is looking for — a CIDR, a name, or
a URI. Guessing further costs a billable tunnel per attempt, so it stops here.

Teardown was done in dependency order this time — route, tunnel, elastic IP, VPC — after
the BackupPolicyAssignment lesson. Account clear afterwards: 0 tunnels, 0 elastic IPs,
and only the pre-existing `cloudburst-default` VPC.

### VpnRoute unblocked by reading Aruba's own code

The two errors that blocked it — `CloudSubnet: subnet not found` and
`OnPremSubnet: network address is invalid` — are not documented anywhere in the OAS,
which describes both fields only as "Cidr of the … subnet". They are stated plainly in
Aruba's CLI end-to-end suite:

> "The VPN route's `--cloud-subnet` must reference an existing VPC subnet CIDR, not the
> VPN tunnel's own internal provisioning subnet. Create a dedicated VPC subnet for this
> purpose and tear it down after the test."
> — `Arubacloud/acloud-cli` `e2e/network/test.sh:1026-1028`

That is exactly what had been tried and rejected: `192.168.60.0/24` was the tunnel's own
`ipConfigurations.subnet`, which the route lookup does not resolve. `cloudSubnet` is not
a free CIDR at all — the server dereferences it to a real Subnet resource, which is why
the response type is an object `{cidr, uri}` while the request is a bare string.

Applied: a dedicated `Advanced` subnet at `10.60.0.0/24`, created before the tunnel so
it is `Active` in time, plus `onPremSubnet: 192.168.129.0/24` — a literal Aruba's own
e2e runs against a live tenant.

```
Vpc          6a9bf5d2
Subnet       6a9bf67f   Advanced 10.60.0.0/24  (dedicated to the route)
ElasticIp    6a9bf70d
VpnTunnel    6a9bf740   keeps its own 192.168.60.0/24
VpnRoute     6a9bf909   DRIFT CORRECTED
teardown 5/5 · residue 0 — clean
```

`VpnRoute` is **GA**. `VpnTunnel` stays beta: its drift injection still returns 400,
because the generic mechanism re-`PUT`s the whole fetched object.

The lesson generalises. Aruba maintains eight first-party repos that all call this API,
and the constraints absent from the OpenAPI documents are written down in their SDK
examples, CLI e2e scripts and operator reconcile logic. Reading those is cheaper than
discovering the same rules one billable 400 at a time — and it is how this resource went
from blocked to GA without a single extra guess.

### DatabaseUser and Grant unblocked — base64, and a leaf identifier

Two blockers fell in one run, and neither was a policy problem.

**The password must be base64-encoded, and nothing says so.** Every plaintext value was
rejected with `Password does not match the minimum requirements` — including Aruba's own
SDK example — which reads as a complexity failure and is not one. Their Terraform
provider encodes client-side:

```go
// internal/provider/dbaasuser_resource.go:130
passwordBase64 := base64.StdEncoding.EncodeToString([]byte(data.Password.ValueString()))
```

Sending `UHJvdmExMjM0NTY3ODlBQ0A=` (base64 of `Prova123456789AC@`) worked immediately.
The same file records a second undocumented trap in a comment: that identical error is
*also* returned while the DBaaS user API is still initialising, even with a correct
password — the provider polls `GET` on a non-existent user until it 404s to prove the API
is up. **Two unrelated causes, one misleading message.**

**Grant was our bug.** Its identifier was bound to the object `user`, not the leaf, so
the `{username}` path parameter was filled with a stringified Go map:

```
GET .../grants/map%5Busername:gauser%5D
```

Observe therefore never matched, and the controller re-POSTed until the API answered
`A grant already exists for the specified Database User` — the grant had been created on
the first attempt and then lost. With `identifiers: [user.username]` and the get verb
bound to `spec.user.username`, it reconciles cleanly against the existing grant.

That is the same failure as `security/Key`'s `keyId` and `AlertRule`'s flat response: an
identifier bound to something the API does not key on. The generator now derives the id
field and metadata-wrapping from the response schema; this case needed the *leaf* of a
nested create body, which the override table now states explicitly.

```
Dbaas        6a9d90dd   Ready
Database     gadb       Ready
DatabaseUser gauser     Ready   (base64 password)
Grant        gauser     Ready   (leaf identifier)
teardown 4/4 · dbaas 0 · no CRs remaining
```

Both are **beta**, not GA: neither has an update verb, so drift is not applicable, but
their delete was exercised only through the chain teardown rather than as a verified
step. **`P2` still stands** — `DatabaseUser.password` is a plaintext-in-spec field, and
now demonstrably a base64-in-spec one, which is no better. The `database` provider should
not be promoted until it can source that value from a Secret.

### CloudServer created for real, and Job unblocked behind it

The 1.1 create endpoint works exactly as Aruba's own SDK uses it. One POST, 201, with
every association in the body:

```
POST /projects/{p}/providers/Aruba.Compute/cloudServers?api-version=1.1   -> 201
  flavorName CSO1A2 · dataCenter ITBG-1
  vpc + subnets[] + securityGroups[] + bootVolume + elasticIp   (all {uri} objects)
  -> 6a9d9c29260a604a55d6c1c9, InCreation -> Active
```

**No power-on step, no associate step, no attach step.** The multi-call sequence this
repository was built around does not exist for create — those endpoints are day-2. The
boot volume had to be created `bootable: true` with `image: LU22-001`; a plain volume
never boots, which is undocumented and stated only in Aruba's SDK and operator samples.

This settles [#108](https://github.com/krateo-platformops/oasgen-provider/issues/108):
CloudServer is not blocked by its API shape, only by a RestDefinition carrying a single
`oasPath` while its create lives in `compute-provider_v1.1.json`.

**`schedule/Job` then created against it**, which had been blocked with no way forward:

```
Job 6a9d9ec6156cdd48efdf9b7b   state Disabled (enabled: false)   Synced=True
```

The step form matters and is undocumented: `httpVerb: POST` with a **relative**
`actionUri: poweroff`. A full path is rejected with *"The action /projects/…/poweroff
with http verb POST is not configured for this resource typology"* — which reads like
the action is unsupported when it is merely spelled wrong.

`Job` is **beta**: created and observed with `status.metadata.id` populated, but `Ready`
never went true — the same pattern as `Kaas` and `Registry`, and here the resource sits
in a legitimate terminal state (`Disabled`, because the job is intentionally not
enabled) that the readiness check does not appear to accept.

Everything was removed afterwards: job, cloud server, boot volume and elastic IP.
Account verified clear across servers, volumes, elastic IPs, jobs and dbaas.

### 0.22.3 — #102 works; Kmip fails for a different reason (and my hypothesis was wrong)

Upgraded to 0.22.3 (35 controllers, 34/34 Ready) specifically to see whether
[#102](https://github.com/krateo-platformops/oasgen-provider/pull/102) — "consult the
observe verb on any delete error, not just 404" — released the two teardowns that had
wedged. It did not, and the reason is **not** that the fix is broken.

**`security/Kmip` — a 200 tombstone.** After delete, the resource is gone from its list
and still answers `GET` with 200:

```
GET .../kmip                     -> {"total":0,"values":[]}
GET .../kmip?includeDeleted=true -> {"total":0,"values":[]}
GET .../kmip/{id}                -> 200 {"status":"Deleted","deletionDate":"...21:31:39Z"}
```

`externalResourceStillExists` saw 200, reported the resource present, and held the
finalizer. **That was the correct decision given the information it had** — 0.22.3's
delete path behaved exactly as written. What is wrong is treating 200 as evidence of
existence when the body says `Deleted`. Filed as
[#111](https://github.com/krateo-platformops/oasgen-provider/issues/111).

I nearly filed this as a regression against a working fix, and would have, had I not
checked the raw endpoints before writing it up.

**`schedule/BackupPolicyAssignment` — NOT the same cause.** I predicted it was the same
tombstone problem. It is not:

| | list | GET | status | deletionDate |
|---|---|---|---|---|
| `Kmip` | 0 | 200 | `Deleted` | set |
| `BackupPolicyAssignment` | **1** | 200 | `Deleting` | **absent** |

The assignment is genuinely still present, stuck mid-deletion server-side, and the
volume it references returns 404 — because I deleted that volume out of dependency order
when the teardown first stalled. Aruba never completes the deletion once the referenced
resource has vanished. So this is **residue I created**, not a provider defect, and
0.22.3 could never have fixed it.

It also poisons re-runs: a fresh chain matched the stuck record by name and tried to
update it, failing with `400`. A stuck resource makes its own name unusable.

**Account state:** the billable members are gone (0 block storages, 0 KMS, 0 keys). A
free `BackupPolicyAssignment` and its `BackupPolicy` remain wedged in `Deleting` and
will need clearing from the Aruba console or by support.

### Forward-compatibility: the 34 on oasgen 0.30.0 (2026-10-15)

At the time of this run we pinned **0.22.2**; upstream was at **0.30.0**, eight minor
versions ahead. (The pin is now **0.27.1** — see "The bump, taken" below.) Run on a
fresh `kind-aruba-030` at Kubernetes **1.36.4** (the floor 0.28.0 now demands), charts
`oasgen-provider-crds` and `oasgen-provider` both 0.30.0:

| check | result |
|-------|--------|
| RestDefinitions `Ready=True` | **34 / 34**, in ~180s |
| generated controller deployments | **35 / 35** at `1/1` (34 + the provider) |
| `oasgen-provider` error/fatal log lines | **0** |
| served CRD versions | unchanged — `v1-0` ×1 (`AlertRule`), `v1-0-0` ×33, `v1alpha1` ×34 |
| samples accepted by strict decoding | **66 / 68** |
| RestDefinition schema shape | identical: same `spec` keys, same `resource` keys, same `required` |

**The 34 RestDefinitions need no change to run on 0.30.0.** Stage 3's "retire served
versions nothing is using" did not touch ours, and `fix(api): refuse a per-verb oasPath`
does not apply — all 34 use a single `spec.oasPath`.

#### The one coupled change: dotted selectors flip from flat to nested

The two failures are `network/loadbalancer` and `compute/cloudserver`, the only
resources whose identifier is dotted, and they fail **identically on 0.24.0**, so this
is not a 0.30.0 regression. It is [oasgen#106](https://github.com/krateo-platformops/oasgen-provider/issues/106)
— ours, filed when a flat `metadata.name` selector could never match because RDC reads
it nested — **closed COMPLETED on 2026-09-09 and shipped.**

Measured, so the bump does not rediscover it:

```
oasgen 0.22.2   flat  `metadata.name: x`        correct   (the old pin)
oasgen 0.24.0   NESTED `metadata: {name: x}`    correct   (kind-aruba-110)
oasgen 0.27.1   NESTED `metadata: {name: x}`    correct   (the pin now)
oasgen 0.30.0   NESTED `metadata: {name: x}`    correct   (kind-aruba-030)
```

Above 0.22.x the flat form is refused as:

```
unknown field "spec.metadata.name"
```

Worth noting how that message misleads: it reads the same whether the rejected key was
flat (`metadata.name`) or nested (`metadata` → `name`). Someone debugging it from the
error alone can conclude the opposite of the truth and "fix" it in the wrong direction.
The CRD's own property list is the authority — `['configurationRef', 'metadata',
'projectId']` says nested, unambiguously.

#### What a bump to 0.30.0 therefore requires

1. `OASGEN_VERSION` **and** `node_image: kindest/node:v1.36.x` in the same commit —
   0.28.0's chart floor is `>=1.36.0-0`, so on 1.33 Helm refuses and the job fails for
   a reason unrelated to the 34.
2. Flip the dotted selector to nested in `gen_samples_and_coverage.py` and regenerate.
3. Optionally move `DatabaseUser.password` out of base64: request-direction `jq` shipped
   in 0.26.1, along with the redaction fix it needed — see
   [authentication](authentication.md#a-credential-that-is-not-a-spec-field).

### The bump, taken — 0.22.2 → 0.27.1

Not to 0.30.0. 0.28.0's chart floor is `>=1.36.0-0`, so taking it drags the node image
with it; **0.27.1 is the top of the range that still installs on 1.33**, and both
charts publish it (`helm show chart` on each, `kubeVersion: '>=1.33.0-0'`). What it
buys over 0.22.2: nested dotted selectors (#109, 0.23.0), envelope unwrapping (#146,
0.25.0), jq-shaped request bodies (#148, 0.26.0) and credential-header redaction
(#166, first tagged in 0.26.1 — which is why the bump does not stop at 0.25.1).

Steps 1 and 2 above were done in one commit: `OASGEN_VERSION: "0.27.1"`, the selector
flipped to nested in `gen_samples_and_coverage.py`, and all 34 pairs regenerated. The
node image stays at `v1.33.12`, deliberately — 0.27.1 does not demand 1.36, and moving
both at once would confuse two independent failure modes if CI went red.

Only two files changed in the regeneration, `samples/network/loadbalancer.yaml` and
`samples/compute/cloudserver.yaml`, which is the expected blast radius: they are the
only resources with a dotted identifier.

Step 3 was deliberately **not** bundled in: moving the base64 out of the Secret's
value changes what every existing DatabaseUser Secret must contain, which deserves its
own commit and its own live run.

#### Proven by mirroring the CI job locally, not by the static validator

Fresh `kind-aruba-0271` at `kindest/node:v1.33.12`, both charts at 0.27.1, the same
two apply steps and the same checks the workflow runs:

| check | result |
|-------|--------|
| RestDefinitions `Ready=True` | **34 / 34**, inside ~4.5 min |
| deployments at `1/1` in `krateo-system` | **35 / 35** (34 controllers + the provider) |
| `oasgen-provider` error/fatal log lines | **0** |
| samples under **strict** server-side decoding | **69 / 69** |

The two dotted-selector samples are accepted, and the old flat form is refused on the
same cluster:

```
unknown field "spec.metadata.name"
```

which is the point: the flip is required by the bump, not cosmetic.

#### The smoke was proving the wrong namespace

Running it locally turned up something the workflow could not have told us. The step
was:

```
kubectl apply -n krateo-system -f configmaps/
kubectl apply -R -f restdefinitions/     # <- no -n
```

The manifests declare no namespace, so the second line put all 34 in `default`, and
**oasgen deploys each generated controller into its RestDefinition's own namespace** —
so the 34 controllers went to `default` too. The readiness check counts with `-A`, so
the job was green throughout while proving a layout nobody installs: `docs/usage.md`,
the chart and every live cluster here put these in `krateo-system`.

It surfaced only because the local mirror counted *in krateo-system* and answered
`0/34`. A smoke that asserts "34 are Ready" without asserting where they are is
weaker than it looks. Fixed in the same commit, and the table above is from the run
with the fix, with all 35 deployments in `krateo-system`.

**Not covered by this run:** anything requiring the Aruba API. This proves generation,
CRD shape and controller rollout, not a single live reconcile.

### Verifying oasgen-provider #146 (envelope unwrap) — findings that affect this repo

Ran the `fix/110-envelope-unwrap` build against all 34 RestDefinitions on a fresh cluster
(both images built from source, chart 0.24.0 with each overridden). Three results matter
here beyond the PR itself.

**Only one resource was ever affected.** The type degradation #110 describes needs the
status schema to come from the findby *envelope*, which happens only when a resource has
no `get` verb. 33 of our 34 have one, so their status was always built from the bare item
and was always correctly typed. The single exception is `database/DatabaseBackup`
(`findby, create, delete`). With `additionalStatusFields: [metadata.id, metadata.tags]`:

```
stock 0.24.0   "tags": {"type": "string"}                              <- degraded
PR #146        "tags": {"type": "array", "items": {"type": "string"}}  <- correct
```

#### CRD validation ratcheting

**A CRD status-type change is survivable, but only because of validation ratcheting.**
Forcing the upgrade path — stale string in etcd, schema then tightened to array — on
Kubernetes 1.36.1:

| write | result |
|---|---|
| array where schema says string (stock) | **rejected** — so nothing bad ever reaches etcd |
| stale string still readable after the change | yes |
| correct array written over it | accepted, replaces cleanly |
| the *same* stale string written back | **accepted** |
| a *different* wrongly-typed value | rejected |

The last two identify the mechanism: the API server skips validating a field whose value
is **unchanged**. Graduation, from `apiextensions-apiserver/pkg/features/kube_features.go`
across release branches:

```
1.28  Alpha, off by default
1.30  Beta,  on by default   <- disableable
1.33  GA,    LockToDefault: true   <- cannot be disabled
```

So a status-type change self-corrects on **≥ 1.33** and is merely *likely* to on 1.30–1.32,
where an operator can turn the gate off. Below 1.30 it does not: RDC's `populateStatusFields`
merges into existing status and never clears, and `clearStatusFields` is called from Create
and Update but **not Observe**, so a stale value would be rewritten and rejected on every
reconcile with nothing to clear it.

**Our API omits declared fields conditionally.** `security/Kmip`, same resource, two reads:

```
live:        {"id","name","type","status","creationDate"}
post-delete: {"id","name","type","status","creationDate","deletionDate"}
```

This matters for any future change to replace-rather-merge status semantics: a conditionally
present field would blank and un-blank, and `compareScope: updatable` would read that as
drift — the unfixable-drift shape of
[#51](https://github.com/krateo-platformops/oasgen-provider/issues/51). Every field we
currently declare is a core identity field present on every read, so nothing would flap
today — but that is luck, not design, and it constrains what we may safely declare later.

Tracked upstream as [#152](https://github.com/krateo-platformops/oasgen-provider/issues/152).

### VpnTunnel drift, on the fourth explanation (2026-10-20)

`tests/ga/chains/vpn.yaml --with-billable`, five resources, oasgen 0.24.0 on
`kind-aruba-110`:

```
Vpc ... Ready          Subnet ... Ready          ElasticIp ... Ready
VpnTunnel ... Ready, id=6ad70b7bab21a33c1f53b2b3
    drift: 6ad70b7bab21a33c1f53b2b3 still InCreation after 240s
    drift injected via declared spec (tags=[drifted-by-hand])
    DRIFT CORRECTED for VpnTunnel/ga-vpntunnel
VpnRoute  ... Ready, id=6ad70d7eab21a33c1f53b2cc
    DRIFT CORRECTED for VpnRoute/ga-vpnroute
```

**Nothing about the resource needed changing.** The 400 that held this at beta was the
state refusal documented in
[async-readiness](async-readiness.md#every-write-is-refused-mid-transition-not-just-dbaass),
and the fix was in the probe: wait for a settled state, and retry a refusal that names
the `Status` field instead of concluding the body is wrong.

Note the second line. This tunnel is still `InCreation` past the probe's 240s settle
wait, so the wait alone would not have been enough — the **retry** is what got the
write through. A probe that waits but does not retry would have reported this as a
rejected body all over again, which is worth remembering before anyone "simplifies"
that loop away.

Three wrong explanations preceded this, all of them reasoned from a status code rather
than from the error body: "the update body will not accept a full re-PUT" (twice, for
two different resources), then a provisioning-state refusal that was real but was
masking the Dbaas shape bug. The error array was sitting in the response the whole
time; the probe was reading the wrong keys for it.

### CloudServer observed for the first time, and a delete that cannot run (2026-10-20)

`scripts/ga_cloudserver_observe.py`. CloudServer's create is unreachable from a
RestDefinition, so its `findby` and `get` had only ever been applied against an empty
collection — which proves nothing at all. This creates the server over the API and
makes the controller find it:

```
boot volume 6ad7326b (bootable, LU22-001) -> NotUsed
elastic ip  6ad732a0                      -> NotUsed
cloud server 6ad732b8 created at api-version=1.1 -> Active
CR Ready — observed id=6ad732b8503f218a2d6969f6
RESULT {"server_active": true, "observed_id": "6ad732b8...", "cr_ready": true} leftovers=0
```

The observed id is the one the POST returned, so the CR converged on *that* server
rather than on anything incidental.

**`securityGroups` is required and the OAS does not say so.** The first attempt was
refused with `GenericResource: securityGroup cannot be null`, from a request schema
that marks no property required anywhere. That is the fourth undeclared constraint of
this kind recorded here, after Kaas's three.

**The error nearly went unread.** The first failure printed `None: None`, because the
1.1 compute endpoint does not use the `errors: [{field, message}]` envelope the
network and storage providers use — the same class of mistake that cost three wrong
explanations earlier in this campaign, one layer further down. The probe now renders
whatever arrives and dumps the body when it recognises nothing.

#### Delete routes through a RESTAction, and wedges without Snowplow

Teardown timed out, and the controller said why, every few seconds, forever:

```
Cannot delete external resource
err: resource declares deleteApiRef krateo-system/arubacloud-compute-cloudserver-delete
     but no snowplow client is configured (set the snowplow/authn URLs)
```

CloudServer's delete is a RESTAction, not a native verb, so **on a cluster without a
Snowplow client the CR cannot be deleted at all**. It sits with
`composition.krateo.io/finalizer`, `Ready=False/Deleting`, `Synced=False/ReconcileError`,
while the remote server is already gone.

This is a deployment prerequisite that nothing in the install path states, and it is
why CloudServer is beta rather than anything higher: the resource can be observed, but
a CR created on a Snowplow-less cluster is undeletable through Kubernetes.

The finalizer was cleared by hand here — **after** confirming the remote was genuinely
gone (`GET .../cloudServers` returned `total: 0` and the id returned 404). That check
is the whole difference between tidying up and orphaning a billable resource, which is
the general rule recorded elsewhere in this file and still stands: clearing a finalizer
removes the controller's delete path. It is safe only when there is demonstrably
nothing left for it to delete.

### Registry: never created, then created, then Failed (2026-10-20)

Three findings, in the order they appeared.

**1. `size` had never been right, so Registry had never been created.** The chain sent
`size: "20"` and Aruba refuses it with `'20' is not a valid size`. Its tier note said
"applies and reaches `Ready`; sample admitted by its CRD" — which was true, and
described a CR converging on nothing. It also means Registry was never really a member
of the Kaas/Registry/Job never-Ready group; that group is Kaas and Job.

The legal values are `Small`, `Medium`, `HighPerf`, **case-sensitive**, measured by
POSTing each candidate with a deliberately nonexistent volume uri so the call could
not succeed, and watching whether the `Size` error appeared:

```
Small / Medium / HighPerf   accepted  (only Volume + ElasticIp "not found" remain)
small / SMALL / S           'x' is not a valid size
20 / 100 / 1000             'x' is not a valid size
```

Nothing in the spec can tell you this. The create DTO calls the field "The number of
concurrent connections required by user", the response DTO calls it "the id of
registry size", and `RegistrySize` — the one catalogue — is **defined but referenced
by no path**. Reading the first description literally is exactly how `"20"` got there.

**2. With a registry that exists, the premature-Ready defect was visible.** The CR
reported `Ready` while Aruba still had the registry `InCreation` ten minutes later,
and both update and delete are refused in that window. Same defect as Dbaas, same
fix: an `async` block polling `status.state`. Confirmed live — the CR now holds at
`Ready=False/Pending` where before it said Ready immediately.

**3. It does not provision on this account.** Measured directly, twice:

```
t+    1s  InCreation
t+ 1818s  Failed
```

and the status is bare — `{"state": "Failed", "creationDate": ...}`, with no
`failureReason` and no `error`, both of which `StatusResponseDto` declares. The first
timing run deleted the resource before reading its status; checking the leftover of
the second is the only reason this is known, and it is why a third 30-minute run was
not needed.

The account is a **TRIAL** (`legacy_profilename: TRIAL`, `groupMembership:
[ListinoTrial]`), which is the obvious suspect. **It is not established.** `Kaas` is
the same container provider and did reach `Active` on this same account, so "container
is unavailable on trial" is contradicted by our own evidence. A registry-specific
entitlement is possible; so is a constraint on the payload that nothing expresses. The
cause is unestablished, and saying so is better than the fourth confident guess in a
week.

Two operational notes:

- the `async` gate earns its place even though Ready is unreachable: `failureValues:
  [Failed]` is now confirmed live, so a dead registry fails fast instead of hanging
  for the full 30 minutes. `maxAttempts` stays at 120 rather than being trimmed to the
  observed 1818s, because an account that provisions successfully but slowly must not
  be cut off by a bound derived from a failure here.
- a `Failed` registry needs its **DELETE issued twice**. The first returns 202 and
  leaves the resource in place.

### Why Kaas never reported Ready — and the one-line fix (2026-10-20)

`Ready` comes from one thing only: RDC's drift comparison passing. When it fails, the
reason is computed, written to the condition **in memory**, and never persisted — so
the only condition that reaches the apiserver is `Creating`, set by the Update leg
([oasgen#213](https://github.com/krateo-platformops/oasgen-provider/issues/213)). The
resource is not creating. It is in a permanent observe→update loop.

That loop is visible in the live state, which is worse than a wrong condition. From
the controller's own HTTP dump across one 25-minute run, the real cluster's
`status.state`:

```
InCreation, InCreation, Active, Updating, Active, Updating, Active, Updating,
Active, Updating, Active, Updating, Active, Updating, Active, Updating, Deleting...
```

19 PUTs, 7 of them accepted, 12 refused with `{"fieldName": "Status",
"errorMessage": "Invalid status"}`, on a `resyncInterval` of 180s. **RDC mutates a
healthy cluster every three minutes, forever**, and each accepted PUT drives it out of
`Active` into `Updating`, which is what refuses the next one.

#### The two fields that can never match

No debug build was needed: the verbose dump carries the PUT body and the GET response,
and the comparison is just those two. Of the leaves present on **both** sides, 11 of 11
are equal.

**The comparison walks the CR's keys and skips any key absent from the response** —
that is what stops `configurationRef` counting as drift. So only a key present on
*both* sides with a differing value can cause drift, which narrows four candidate
mismatches to two. All four are declared this way in
`openapi/container-provider.json` itself:

| CR / request | response | mechanism | drifts? |
|---|---|---|---|
| `properties.nodeCidr.{name,address}` | `properties.`**`nodecidr`**`.{address,uri}` | casing — `KaasInsertPropertiesDto` says `nodeCidr`, `KaasPropertiesResponseDto` says `nodecidr` | **no** — key absent, skipped |
| `securityGroup.{name}` | `securityGroup.{uri}` | different key — Aruba makes its own group | **no** — key absent, skipped |
| `kubernetesVersion.{value}` | `{value,sellStartDate,recommended}` | response superset | **no** — the CR's only key matches |
| `nodePools[0].instance: "K2A4"` | `{ram,price,cpu,name,diskSizeGb}` | **type** — string in `NodePoolsDto`, object in `NodePoolsResponseDto` | **YES** |
| `nodePools[0].dataCenter: "ITBG-1"` | `{name: "ITBG-1"}` | **type** — same | **YES** |

An earlier version of this section claimed all four mismatches caused the drift. That
was wrong, and the evidence for it was already in the diff that produced it: the tool
printed `nodeCidr` and `securityGroup.name` under "fields the response does not
contain (skipped by the comparison)", and the prose then called them causes. Flattening
to leaf paths hid the only distinction that matters — *key absent* versus *key present
with a different type*.

**`compareScope: updatable` cannot fix this, and that is the interesting part.**
`KaasUpdatePropertiesDto` declares exactly one property, `nodePools`, so narrowing the
comparison to what the update body can express still leaves `nodePools`, whose
`instance` and `dataCenter` are string-in-request and object-in-response by the
vendor's own schema. The comparison is unsatisfiable at every scope that includes the
one field the update can carry. [oasgen#51](https://github.com/krateo-platformops/oasgen-provider/issues/51)
assumed "a field the update body can express" is the same as "a field the API will
echo back comparably"; Kaas is the counterexample.

#### Fixed, without waiting for upstream

`responseTransform` is a whole-document gojq program applied to the response **before**
per-field mapping, status population and the drift comparison — present in the
installed CRD and available since 0.20.0, so the pin already has it. It states what
the vendor's two shapes have in common, which is a true statement about the API rather
than a way of silencing the comparison: the scalar a write sends is the `name` of the
object a read returns.

```yaml
- action: get
  responseTransform:
    inline: |
      if (.properties.nodePools | type) == "array" then
        .properties.nodePools |= map(
          (if (.instance | type) == "object" then .instance = .instance.name else . end)
          | (if (.dataCenter | type) == "object" then .dataCenter = .dataCenter.name else . end)
        )
      else . end
```

Guarded on type, so it is idempotent and cannot corrupt a body that is already
request-shaped or has no node pools. Deliberately **no** lines for `nodeCidr` or
`securityGroup`: those keys are skipped, and handling them would be inventing a fix
for drift that does not occur.

Verified before the cluster was touched — the program was run against the captured
response, and the comparison rule above was reimplemented over the transformed body,
predicting a pass. Then live, end to end:

```
Kaas/ga-kaas  Ready, id=6ad786908e35c4ce7d3379df
DRIFT CORRECTED for Kaas/ga-kaas
drift Kaas/ga-kaas: ok      teardown clean, leftovers = 0
```

So `Kaas` is **GA**, and the write loop is gone with it. `compareScope:
identifiersAndStatus` was considered and rejected: it would have stopped the loop only
by stopping drift detection, which for a repo whose GA bar *is* drift enforcement
would be worse than an honest beta.

#### A note on getting the reason out, for anyone who tries

`REST_CONTROLLER_DEBUG=true` in the controller's ConfigMap **did nothing** on
rest-dynamic-controller 0.24.0: the startup line still reports `"debug": false`. The
HTTP dump that made this diagnosis possible comes from the CR's
`krateo.io/connector-verbose` annotation, which is a different switch
([authentication](authentication.md#verbose-connectors-print-the-bearer-token-below-0261)
— and on anything below 0.26.1 it prints your bearer token). Note also that the
provider owns those generated ConfigMaps: applying a RestDefinition regenerates them
and silently drops a hand-added key.

### KaasBackup, and three values the spec cannot give you (2026-10-20)

KaasBackup was unreachable for the whole campaign: it hangs off a Kaas, and no chain
could get past a Kaas that never reported `Ready`. With that fixed it runs:

```
Kaas/ga-kaas             Ready, id=6ad7ad3c...   DRIFT CORRECTED
KaasBackup/ga-kaasbackup Ready, id=6ad7aea9...
drift Kaas/ga-kaas: ok
```

That is also Kaas's **second** consecutive drift pass, so its GA rests on two runs.

**`type` accepts only `Full`.** The OAS declares it a bare string with no enum. The
API will tell you, if you read the error body — and note which field it blames:

```
('RetentionDays', 'Type is not valid. Allowed values are Full')
('Kaas',          'There is another backup in creation')
```

The complaint about `type` arrives attached to `RetentionDays`. The second line is a
second undeclared rule: **only one backup may be in creation at a time**, so backups
cannot be created in parallel.

**`billingPeriod` is not validated.** This was found by accident, and the accident is
the point. The probe sent `billingPeriod: "NotAPeriod"` deliberately, as a guarantee
that the call could not succeed — the same trick that safely probed Registry's `size`,
where an invalid volume uri *is* checked. Aruba accepted it, created the backup, and
persisted the string verbatim:

```json
"properties": {"billingPeriod": "NotAPeriod", "type": "Full", "retentionDays": 7}
```

So the probe created a real billable resource it was designed not to create. **A
deliberately-invalid field is only a safety net if that field is validated, and which
fields are validated is not knowable in advance** — only a field whose rejection you
have already observed can be used that way.

That backup then went `InCreation` → `Failed`, which looked like a KaasBackup defect
and was not: the same resource with `billingPeriod: Hour` provisions normally. The
invalid value was almost certainly the cause. Worth recording as a near miss — one
more confident note about a resource that does not work, drawn from input only this
probe would ever send.

#### The residue check earned its keep, then got sharper

The run ended with the check **refusing to call itself clean**:

```
UNVERIFIED: .../kaas/${id:ga-kaas}/backups returned HTTP 404
no leftovers among the endpoints that answered, but at least one could not be read
```

Correct, and in this case explainable: backups live under their cluster, and the
cluster was already gone. Two fixes followed. The message now prints the **expanded**
URL rather than the template — the output above cannot be pasted into anything. And a
404 on a child collection whose parent *this chain just deleted* is now reported as
the deletion being confirmed rather than as a failed reading, since a backup cannot
outlive its cluster. Every other failure, including a 404 on a path with no deleted
parent in it, still refuses the clean bill.

The decision table was tested offline rather than by provisioning another cluster, and
one gap is stated in the code rather than hidden: a **misspelled** path that still
contains a deleted parent's id is excused. Closing that would mean teaching the chain
which collections exist, which is the OAS's job.

### Delete, proven rather than assumed (2026-10-21)

Three resources sat at beta because their delete evidence was "the CR went away and
the chain tore down the Dbaas afterwards". That cannot distinguish a DELETE the
controller issued from a cascade when the parent went — and from Kubernetes the two
look identical.

`scripts/ga_chain.py` now verifies each delete **individually, while that resource's
parent is still alive**. A resource declares its own URL as `self:` (or inherits it
from `drift:`, the same path), and teardown GETs it after the CR is gone:

```
--- deleting DatabaseBackup/ga-dbbackup
    delete verified: .../Aruba.Database/backups/6ad8743c -> 404 (after 2 checks)
--- deleting Grant/ga-grant
    delete verified: .../dbaas/6ad87000/users/gauser/grants/gadb -> 404
--- deleting DatabaseUser/ga-dbuser
    delete verified: .../dbaas/6ad87000/users/gauser -> 404
--- deleting Database/ga-database
    delete verified: .../dbaas/6ad87000/databases/gadb -> 404
--- deleting Dbaas/ga-dbaas
    delete verified: .../dbaas/6ad87000 -> 404
```

The Dbaas is last, so every one of those 404s was read while it still existed. The
end-of-run residue check structurally cannot do this: by then the parent is gone too.

#### The first version of this check left a Dbaas running

It expanded `${id:self}` during teardown, where the `kind` context no longer exists,
and raised `${id:self} used before None was created` — **inside the teardown loop**,
which abandoned the remaining four deletions with a live, billing Dbaas. Cleaned up by
hand within minutes, but the lesson is worth more than the fix:

- the URL is now resolved **at creation**, where the context exists, and stored
  expanded;
- the check is wrapped so **it can never halt a teardown**. A verification step that
  can stop the cleanup it reports on is worse than no verification at all, and this
  file already records two earlier incidents of a probe leaving billable resources
  behind. Adding a safety feature *in the cleanup path* turned it into the hazard.

#### What it changed

With delete verified individually and drift inapplicable where the API has no update
verb (see the rule in [coverage](coverage.md#support-tiers)), `Database`,
`DatabaseUser` and `Grant` meet the GA bar on executed evidence: **23 GA / 6 beta /
4 experimental / 1 blocked**.

### Re-earning GA under the stricter delete rule (2026-10-22)

Raising the delete bar created a gap in our own table: three resources were GA on
exactly the evidence just rejected for `Database`, `DatabaseUser` and `Grant`. Each is
a **child** resource whose chain's residue list covers only the parent collection, so
a child that vanished when its parent went was indistinguishable from one the
controller deleted.

All three re-ran and all three held, each 404 read **while its parent still existed**:

| resource | verified at | parent alive |
|---|---|---|
| `VpcPeering` | `.../vpcs/6ad9eb1d/vpcPeerings/6ad9eda8` | VPC, deleted after |
| `Key` | `.../kms/6ad9f3b6/keys/ga-key` | KMS, deleted after |
| `Restore` | `.../backups/6ad9f62d/restores/6ad9f6f9` | backup, deleted after |

Picked up incidentally, since teardown now checks every resource: `Subnet` ×2, `Vpc`
×2, `Kms`, `Backup`, `Snapshot` and `BlockStorage` all verified the same way, and
`Kms`, `BlockStorage`, `Snapshot` and `Backup` each passed drift again on the way
through.

`self:` is now declared for **all 46 resources across all 16 chains**, so the check is
repo-wide rather than only where it was first needed. Two things worth stating plainly:

- **No demotions were needed, but demotion was the expected outcome if a delete had
  not been verifiable.** A rule that can only confirm what was already claimed is not
  a rule.
- `VpcPeeringRoute` is flagged `billable: true`, so a free pass of `peering-route`
  skips it. It is the one resource whose `self:` is declared but never exercised,
  which is its own small version of "covered in a place nobody runs".

### Job reaches GA — the last of the never-Ready three (2026-10-22)

`scripts/ga_job_probe.py` creates a CloudServer over the API, runs
`tests/ga/chains/job.yaml` against it, and removes the server afterwards whatever
happens. The server has to be made out of band because a Job's only supported step
actions are poweron/poweroff — so it needs one — and a RestDefinition cannot create a
CloudServer ([oasgen#108](https://github.com/krateo-platformops/oasgen-provider/issues/108)).
That circularity is why Job had never been exercised.

```
cloud server 6ada04ff (api-version=1.1) -> Active
Job/ga-job   Ready, id=6ada054d
DRIFT CORRECTED for Job/ga-job
delete verified: .../Aruba.Schedule/jobs/6ada054d -> 404
chain exited 0 · residue clean
```

**The fixture could never have worked.** Its step carried a full-path `actionUri`,
which Aruba rejects as *not configured for this resource typology*; it must be the
relative `poweroff`. The repo had recorded that rule after a live run and then not
applied it to the fixture — the same shape of defect as Registry's `size`, where the
chain's value had never been accepted by the API.

#### The cause: normalisation, which is what Kaas was *not*

Diffed from the controller's own HTTP dump, request against response:

| field | CR sends | API returns |
|---|---|---|
| `steps[].actionUri` | `poweroff` | `/poweroff` |
| `executeUntil` | `2027-01-01T00:00:00Z` | `2027-01-01T00:00:00+00:00` |

The first is the API contradicting itself: it **refuses** a leading slash on write and
**adds** one on read. The second is the same instant in two encodings. Both keys are
present on both sides, so each one alone makes the comparison unsatisfiable, and the
log shows the consequence plainly — **five accepted PUTs in one short run, all 200,
all no-ops**. This is the server-normalised value the oasgen peer predicted when we
first discussed #213; it was the wrong explanation for Kaas (a type mismatch) and the
right one here.

A `responseTransform` on the get verb normalises both before the comparison runs.

#### Two process notes, both earned the hard way

**The emitted program is the only one worth testing.** The first transform used
`sub("\+00:00$"; "Z")`. The backslash collapsed somewhere between the generator and
the YAML, gojq refused it with `invalid escape sequence "\+" in string literal`, and
the run was wasted — because the version that was tested was a copy retyped into a
shell, not the string the generator produced. `scripts/validate.py` could not catch
it either: it checks paths, YAML and chart rendering, not whether a jq program
compiles. The transform now uses `endswith` and a slice, so there is no escape to
collapse, and it is tested with `jq -f` **against the generated file**.

**Server normalisation is worth suspecting before anything exotic.** Both of Job's
mismatches are the kind of difference a reasonable API produces without meaning
anything by it. The expensive diagnoses in this campaign all began by reasoning from a
status code; the cheap ones began by diffing the two bodies the controller had already
logged.

### A run that outlives its own token (2026-10-22)

The `backup-assignment` run failed in a way worth recording, because the failure was
in the harness rather than the provider, and it was the kind that hides itself.

The chain read the ESO-managed Secret once at startup and passed that string down
through every call. Roughly twenty minutes in, the token expired. From that moment
the API answered **401 to everything**, and the run's own safety net went with it:

```
--- deleting BackupPolicy/ga-bpa-policy
    delete UNVERIFIED: .../backupPolicies/6a9ae9d4780e9f66e872af55 -> HTTP 401
--- deleting BlockStorage/ga-bpa-vol
    delete UNVERIFIED: .../blockStorages/6ada0ddc984f54e9730cacf9 -> HTTP 401
    UNVERIFIED: .../blockStorages returned HTTP 401
    no leftovers among the endpoints that answered, but at least one could not be
    read — NOT a clean bill
```

The `kubectl delete` calls still worked, because those go through the cluster and the
controllers hold their own credentials. Only the *verification* was blinded. So the
fate of a billable 20 GB volume was unknown until `scripts/account_check.py` was run
by hand and found the collection empty — the deletes had in fact succeeded.

Two things earned their keep here. The residue check refused to call the account
clean on a 401, rather than reporting zero leftovers from endpoints that never
answered; an earlier version of that check would have printed `clean` and been
believed. And a separate sweep existed to ask the question again with a fresh token.

The fix is that no code path may depend on a token minted at startup. `token()` now
caches and takes `force=True`, and `api()` treats a 401 as a retryable condition:
re-read the Secret, replay the call once, and only then report failure. The token is
no longer threaded through the call sites at all, so there is nothing left to hold a
stale one. Verified by poisoning the cache with a junk string — the next call logged
`401 — re-reading the ESO Secret and retrying once` and returned 200.

The same hazard was worse in the two probe scripts (`ga_cloudserver_observe.py`,
`ga_job_probe.py`): their teardown runs *after* the longest waits in the script, so an
expired token there means 401 on every DELETE and a server left running and billing.
Both now refresh the same way.

#### And the run proved nothing anyway

Its `BackupPolicy` reported `Ready` against id `6a9ae9d4780e9f66e872af55` — the policy
that has been stuck in `Deleting` on this account since **2026-09-04**. `findby`
matches by name, the fixture used the same name, so the chain adopted the corpse; the
assignment that depends on it then failed its update with 400. The fixture now uses
`ga-bpa-policy-b`.

That adoption is a defect in its own right, independent of the harness: **a resource
in `Deleting` satisfied `findby` and was observed as healthy.** Nothing in the
response made the CR hesitate about a resource on its way out of existence.

### VpcPeeringRoute reaches GA, on the third attempt (2026-10-22)

`VpcPeeringRoute` was the last resource whose only missing evidence was drift, and
getting that one verdict took three runs. Neither failure was the resource's fault,
which is the part worth recording:

1. The chain had **no drift block at all** — while this document and the coverage note
   both implied the peering chain covered drift. The claim came from the older bash
   harness and was never carried across when the declarative chain replaced it.
2. The block was added, the drift was injected, and the poll died on a single
   `TimeoutError: The read operation timed out`. `api()` caught `HTTPError` only, so a
   transport failure escaped as an exception and took a 20-minute billable run with it.
3. With transport blips treated as "ask again", the verdict came out clean.

```
--- creating VpcPeering/ga-pr-peering
    Ready, id=6ada2da8ab21a33c1f53c786
    DRIFT CORRECTED for VpcPeering/ga-pr-peering
--- creating VpcPeeringRoute/ga-pr-route
    Ready, id=6ada2e75ab21a33c1f53c79b
    DRIFT CORRECTED for VpcPeeringRoute/ga-pr-route

RESULT: 6 resources created and observed
```

All six deletes were verified individually against the API, each at its own URL and
each **while its parent was still alive** — the route at its full four-level path
`.../vpcs/{id}/vpcPeerings/{id}/vpcPeeringRoutes/{id}`, which is the only way to
distinguish a real DELETE from a cascade:

```
    delete verified: .../vpcPeerings/6ada2da8.../vpcPeeringRoutes/6ada2e75... -> 404
    delete verified: .../vpcs/6ada2b12.../vpcPeerings/6ada2da8...             -> 404
    delete verified: .../vpcs/6ada2babab.../subnets/6ada2cf1...               -> 404
    delete verified: .../vpcs/6ada2b12.../subnets/6ada2c59...                 -> 404
    delete verified: .../vpcs/6ada2babab21a33c1f53c75d                        -> 404
    delete verified: .../vpcs/6ada2b12ab21a33c1f53c750                        -> 404
```

That brings the count to **25 GA**. The lesson is not about peering: a harness that
turns a transient network error into a lost run will keep charging for the same
evidence, and the first of the three failures shows that a note claiming coverage is
not the same as a fixture that produces it.

### BackupPolicyAssignment: two runs, no new evidence, and a half fix of mine (2026-10-22)

`BackupPolicyAssignment` was re-run twice to replace an adopted verdict with a real
one. Neither run produced evidence, and the cause is worth recording because it is a
provider behaviour rather than bad luck.

`findby` matches by **name**, and this account holds two resources wedged in
`Deleting` since **2026-09-04** that hold the fixture's names. So:

- **Run 1** adopted `BackupPolicy/ga-bpa-policy` — id `6a9ae9d4`, a seven-week-old
  corpse — and reported it **Ready**. The dependent assignment then failed its update
  with 400.
- I renamed the policy to `ga-bpa-policy-b`. That was **half a fix**: the assignment
  was still called `ga-bpa`, which is also the name of the wedged *assignment*.
- **Run 2** therefore adopted the corpse one level down, and failed in a way that
  names the problem exactly: `delete of ga-bpa did not remove the external resource,
  which is still present` — a **delete** failure reported while the resource was
  supposedly being *created*.

Both names are now distinct. Two things the second run did get right: the fresh policy
came up as `6ada3146` (today's id, not the corpse's), and both the policy and the
20 GB volume were deleted and **verified 404** against the API.

#### The residue check was blaming the run for a corpse

Run 2 reported `leftovers = 1` while having created nothing that survived. The
leftover was the September assignment, matched because the check compared **names**.
A fixture name is not unique on an account. It now matches by **id** — only a
resource whose id the run recorded is that run's to answer for — with name matching
kept as a fallback for a create that timed out before an id could be read. The same
bug would have hidden a real leak behind a same-named stranger.

#### Why this chain is now excluded from routine runs

The honest conclusion is that there is nothing left to learn here at acceptable cost.
Create, observe and drift are **already proven live** (`6a9aea83`). Delete is
**already proven broken**: the resource enters `Deleting` and stays, and every further
DELETE returns 400 `Invalid status` — which is precisely how the two September corpses
were made. A run with working names would re-confirm both and leave a *third*
permanently undeletable resource on the account.

So the chain keeps its distinct names and carries a header saying not to run it
casually. It exists to test a **fix** to the delete path, where wedging one more
resource would buy something. `BackupPolicyAssignment` stays at **beta** on the
evidence that exists, and the two corpses need the Aruba console or support.

### The evidence reproduces on the released stack (2026-10-22)

Every live verdict in this document was measured on `kind-aruba-110`, which turned out
not to be the stack the install docs describe:

| | evidence cluster | CI / install docs |
|---|---|---|
| chart | `oasgen-provider-0.24.0` | `0.27.1` |
| provider image | `local/oasgen-provider:108-live` | released `0.27.1` |
| RDC image | `local/rest-dynamic-controller:110-test` | released `0.27.1` |

The provider was also drifted from its own Helm release — the values ask for
`110-test` while the running Deployment is `108-live`, patched in place afterwards, so
a `helm upgrade` there would have swapped the provider out from under the evidence.
Neither local image carries a label saying what it was built from.

Nothing about this was *known* to be wrong: `responseTransform`, the feature carrying
`Kaas`, `Registry` and `Job`, is present at tag `0.27.1` in both the RestDefinition
CRD and the RDC client. But "therefore it works on the pin" is an inference, and the
whole premise of this campaign is that inferences about this provider have a poor
record against clusters.

`scripts/pin_verify.sh` settles it. It builds a cluster matching CI exactly — pinned
node image `kindest/node:v1.33.12`, charts at `0.27.1`, **no image overrides** — and
runs the free `network-security` chain: four resources, four drift blocks, zero cost.

```
    34 ghcr.io/krateo-platformops/rest-dynamic-controller:0.27.1
     1 ghcr.io/krateo-platformops/oasgen-provider:0.27.1

    DRIFT CORRECTED for Vpc/ga-ns-vpc
    DRIFT CORRECTED for Subnet/ga-ns-subnet
    DRIFT CORRECTED for SecurityGroup/ga-ns-sg
    DRIFT CORRECTED for SecurityRule/ga-ns-sr
    ... all four deletes verified -> 404
    attempt 1: leftovers = 0
    clean
```

ESO minted its first token on that cluster on the **first** check, so the unattended
credential path holds on the released stack as well — it is not a property of the
local build.

Two notes on the script, both from getting it wrong first. It applies **only** the 34
`*-configuration.yaml` objects: `apply -f samples/` would also create a live billable
CR per resource and clobber the token Secret ESO owns. And it uses
`helm upgrade --install` throughout, because the first re-run against the cluster it
had already built failed with `cannot re-use a name that is still in use` — a
verification script that cannot be run twice is not much of a verification script.

The client credential is never prompted for: it already exists on the evidence
cluster, so it is piped cluster-to-cluster through `kubectl` and never touches disk,
argv or a log. The two labels ESO requires are re-applied in transit, without which
the lookup fails with a bare "not found".

## Kmip reaches GA — 26 (2026-10-23)

`security/Kmip` was the last resource in the repository that had **never run**. It is
now GA, and the run that earned it was on the released stack via `pin_verify.sh`, so
there is no local build anywhere in the evidence:

```
=== images actually running, including the RDCs ===
       1 ghcr.io/krateo-platformops/oasgen-provider:0.27.1
      34 ghcr.io/krateo-platformops/rest-dynamic-controller:0.27.1
--- creating Kmip/ga-kmip
    Ready, id=b132fb62-998d-479f-9c9d-794c6f1ea2fd
RESULT: 3 resources created and observed
  drift Kms/ga-kms: ok
--- deleting Kmip/ga-kmip
    delete verified: .../kms/6ada8934d94d05eaad355b01/kmip/b132fb62-998d-479f-9c9d-794c6f1ea2fd
                     -> 200 tombstone (status=Deleted, deletionDate set) after 2 checks
    attempt 1: leftovers = 0
    clean
=== chain exited 0 ===
```

Drift is **inapplicable** by the `Key`/`Restore` precedent, not unproven: `KmipNameDto`
is `{name}` only and `name` is the identifier, so there is no non-identifying field
left to converge.

### The cause was a name collision, and the scope choice was the whole fix

Its path is `/kms/{id}/kmip/{kmipId}`, so the spec carries `id` meaning *the KMS*
while every response carries `id` meaning *the Kmip*. One key, present on both sides,
two unrelated identities — the comparison can never be satisfied, so the controller
PUT `{"name":"ga-kmip"}` every 180s against a response that already said
`name: ga-kmip`. Third distinct route into [oasgen#213](https://github.com/krateo-platformops/oasgen-provider/issues/213),
after `Kaas` (request/response **type** mismatch) and `Job` (server
**normalisation**).

`identifiersAndStatus` was tried first and changed nothing — four more no-op PUTs,
one billable KMS run. It projects the spec onto `identifiers + additionalStatusFields`
and `id` **is** an additionalStatusField here, because that is how the Kmip's own id
reaches status to build `{kmipId}`; the scope pulled the colliding key straight back
in. `updatable` derives its field set from the update body instead, and is the only
scope that drops `id` without breaking either URL construction or the status mapping.
A `responseTransform` cannot help for the same reason: the response's `id` is what
populates status.

### The finalizer released by the fallback, not by the feature — oasgen#218, fixed and verified live

Kmip is a tombstone resource: after a successful DELETE it vanishes from its own
collection, including with `includeDeleted=true`, while `GET` on its id still answers
**200** with `status: Deleted` and a `deletionDate` a live read does not carry. The
chain now accepts that as proof of absence; demanding a 404 there would report an
unconfirmed delete forever.

The RestDefinition declares a `notFoundBody` predicate on the get verb for exactly
this, and the predicate is correct — but on every released version it is the *observe*
path that honours it and the *delete* path that cannot. RDC hands the delete check the
whole `restclient.Response` struct instead of the body, gojq cannot index a Go struct,
the program errors, and the deliberately-forgiving error branch reports
*unverifiable* — at which point a successful DELETE is trusted. Right outcome,
adjacent reason, and deletion is never actually verified for a tombstone resource,
which is the limitation [oasgen#111](https://github.com/krateo-platformops/oasgen-provider/issues/111)
was filed to remove.

Filed as [oasgen#218](https://github.com/krateo-platformops/oasgen-provider/issues/218)
with a reproduction: one line, `body` → `body.ResponseBody` at
`mutate_restaction.go:157`, introduced by the commit that added the feature and
present from 0.23.0 through 0.32.0. The patch builds and the controller tests pass
both before *and* after it, which is the other half of the report — the existing
`notFoundBody` tests call the helper directly with a map, so nothing in the suite
exercises either caller.

This repository's chain verifies the tombstone against the API itself, which is how
the discrepancy surfaced at all: the chain could prove the resource was gone while
the controller could not.

#### Verified live on the fix, 2026-10-25

Fixed in oasgen `f97b150` (PR #219) and exercised end to end on a cluster built
entirely from the fix branch — both images and both charts from `f9c1b70` in a
detached worktree, so there is no released-oasgen-with-newer-RDC skew to argue about.
`REST_CONTROLLER_DEBUG=true` in every generated controller, because all three outcomes
of this code path log at debug and from outside the cluster a verified teardown and a
fallback-trusted one are indistinguishable.

The Kmip took 102 minutes from create to released finalizer, almost all of it waiting
on Aruba:

```
InCreation            (35 min — DELETE answers 400, the state-refusal convention)
CertificateAvailable  (two DELETEs answer 500)
Deleting
Deleted               (tombstone)
```

`CertificateAvailable`, **not** `Active`, is this resource's healthy state. Anything
that fills in `successValues` by analogy with the other providers is wrong here.

The body the predicate was finally handed:

```json
{"id":"9e719afb-97fc-4abd-b84c-cc57ac2d3c39","name":"ga-kmip","type":"KmipVpc",
 "status":"Deleted","creationDate":"2026-10-25T10:35:06.451+00:00",
 "deletionDate":"2026-10-25T12:17:32.925+00:00"}
```

and the two controller lines, consecutive, in the same millisecond:

```
12:17:33.054 DEBUG Get returned 200 but notFoundBody reports the resource absent; treating as deleted
12:17:33.054 INFO  Delete returned an error but the external resource is verifiably absent; releasing finalizer
                   deleteError: "unexpected status: 400: invalid status code: 400"
```

The second line is the proof, not the first. The DELETE **errored** and the finalizer
was released anyway, which is only reachable through the verified-absent branch: the
fallback requires either a successful delete or no get verb, and neither held. Counted
rather than eyeballed — exactly **1** occurrence of the first message in the whole
controller log, against **355** of `External resource still present after delete;
holding finalizer` before it. A non-zero count alone would not have distinguished
"fired" from "fired on every poll".

This is the wedge, reproduced and then cleared. On every released version 0.23.0
through 0.32.0 this exact situation holds the finalizer **forever**: DELETE 400, the
predicate errors on the `restclient.Response` wrapper, the result degrades to
*unverifiable*, `if deleteErr != nil { return deleteErr }`, and the Kmip answers 200
for the rest of time so no later poll ever escapes.

Two caveats on what this run does and does not show. The verdict was **called too
early once**: at `InCreation` the hung delete looked like a predicate failure, and it
was not — the resource genuinely was still present and holding was correct. And a run
that stopped there would have proved nothing either way, because with the DELETE
failing the *old* code also held, via the unverifiable branch. Same outcome, different
reasoning. Only the tombstone separates them.

## Folder reaches GA — 27 (2026-10-23)

`project/Folder` was beta because of a claim in its own chain file, and the claim was
wrong. Full lifecycle now, on the released 0.27.1 stack:

```
--- creating Folder/ga-pf-folder
    Ready, id=6adb13221647a47e76f0c788
    drift injected via driftBody (name='ga-pf-folder-drifted'); waiting for correction to 'ga-pf-folder'
    DRIFT CORRECTED for Folder/ga-pf-folder
--- deleting Folder/ga-pf-folder
    delete verified: /folders/6adb13221647a47e76f0c788 -> 404
    attempt 1: leftovers = 0
    clean
```

### The note said there was nothing safe to perturb

> no tags to perturb, a name that is the immutable identifier, and a `default` flag
> that decides account-wide where new projects land. Nothing is safe to change.

Two of those three hold. The name is **not** immutable. It is what `findby` matches
on, which is where the confusion came from, but Folder's `get`, `update` and `delete`
all build their path from `status.id` — never from the name — and `FolderUpdateDto`
is `{name, default}` with `name` plainly writable. So the name can be perturbed
upstream and converged back, the resource is never lost while it is diverged, and the
account-wide `default` switch is never touched. The probe sends `default: false`
through the perturbation deliberately, so the switch is not flipped even momentarily.

### What actually blocked it was the harness

The generic drift injector perturbs `metadata.tags`. `FolderUpdateDto` declares
`additionalProperties: false`, so adding a `metadata` key is a 400 — and it is a 400
for **every** body shape the injector tries, since both its candidates carry the tag.
The probe then reported that drift could not be injected, which read as a property of
the resource.

This is the second time a harness limitation has been recorded as a resource
limitation (the first was Dbaas drift sitting SKIPPED for the whole campaign because
only one of two body shapes was tried). The fix generalises rather than special-cases:
a chain may now declare

```yaml
driftBody:            # the exact PUT body to inject
  name: ga-pf-folder-drifted
  default: false
driftAssert:          # the field the controller must restore, and to what
  path: name
  equals: ga-pf-folder
```

and the injector asserts the write actually landed before waiting for correction — an
API that accepts and silently ignores a PUT must not look like a controller that
corrected it instantly, which is the vacuous pass oasgen#76 produced.

### Also fixed here: delete verification had a fixed three-minute budget

`DELETE_VERIFY_SECONDS` replaces a hardcoded 12 polls at 15s. Three minutes cannot
tell a delete that STALLS from one that is merely slow, and that distinction is the
entire open question on `schedule/BackupPolicyAssignment`. It is a separate finding
from Folder but it came out of the same read of the teardown path.

## KaasBackup does not provision either — and it is the same failure as Registry (2026-10-23)

`container/KaasBackup` was beta with a note promising that a longer settle window
would earn its drift test. It would not have. The backup **fails**, and the note was
wrong about why.

With the state logged on every transition, on the released 0.27.1 stack:

```
--- creating Kaas/ga-kaas
    state 6adb1c8a: (first read) -> InCreation at t+0s
    state 6adb1c8a: InCreation   -> Active     at t+266s
    DRIFT CORRECTED for Kaas/ga-kaas
--- creating KaasBackup/ga-kaasbackup
    Ready, id=6adb1e4e8e35c4ce7d33a3ec
    state 6adb1e4e: (first read) -> InCreation at t+0s
    state 6adb1e4e: InCreation   -> Failed     at t+1637s
    drift: PUT returned 400 for every body shape tried — Status: Invalid status
           — cannot inject (state at refusal: Failed)
--- deleting KaasBackup/ga-kaasbackup
    delete verified: .../kaas/6adb1c8a.../backups/6adb1e4e... -> 404
```

### Two claims of mine that this refutes

**"It stayed `InCreation` for over 25 minutes, outlasting a 900s settle wait."** It
does leave `InCreation` — into `Failed`, at t+1637s. The window was never the
obstacle; this run gave it 2700s and the outcome was the same. A `Failed` resource
refuses writes with exactly the envelope a busy one uses,
`{fieldName: Status, errorMessage: Invalid status}`, which is why the probe's verdict
read like impatience.

**"The earlier `Failed` was almost certainly the unvalidated `billingPeriod`."** A
probe had created a backup with `billingPeriod: "NotAPeriod"`, the API persisted that
string verbatim, and the backup failed — so the string got the blame. This run sent
`Hour` and failed identically. That explanation does not survive.

### Why the harness could not tell me this before

The probe reported its own verdict — *400 for every body shape tried* — and discarded
the state that produced it. That is the same defect as
[oasgen#213](https://github.com/krateo-platformops/oasgen-provider/issues/213), the
one this campaign filed against the provider for computing a drift reason and throwing
it away, reproduced in our own harness. `wait_settled` now logs every state transition
with a timestamp, and the give-up message names the state at the moment of refusal.
One line per transition, and it turned a year's worth of plausible guessing into a
measurement in a single run.

### The pattern that matters

| Resource | `InCreation` | Outcome | Diagnostic |
|---|---|---|---|
| `container/Registry` | 1818s | `Failed` | none |
| `container/KaasBackup` | 1637s | `Failed` | none |
| `container/Kaas` (parent) | 266s | **`Active`** | — |

Two sub-resources of one provider failing the same way, on an account where the parent
cluster provisions in four minutes, is a far stronger signal than either alone. Both
statuses are bare: no `failureReason`, no `error`, though `StatusResponseDto` declares
both. This is now the central question in the Aruba support request, phrased as
entitlement/quota/backing-service rather than payload, because the payloads have been
exhausted.

`create`, `observe` and a 404-verified `delete` all work for KaasBackup. What fails is
the provisioning behind them, so it moves from beta to **experimental** alongside
Registry rather than staying beta on a promise of patience.

## BackupPolicyAssignment: the delete really does stall (2026-10-23)

The one measurement this resource had never been given: delete the assignment with
its referenced volume **alive** and wait longer than three minutes. Taken, and the
answer is no.

```
--- creating BackupPolicyAssignment/ga-bpa-b
    Ready, id=6adb152d97493a7adf79529d
    drift injected via declared spec (tags=[drifted-by-hand]); waiting for correction
    DRIFT CORRECTED for BackupPolicyAssignment/ga-bpa-b
--- deleting BackupPolicyAssignment/ga-bpa-b
    error: timed out waiting for the condition          (TIMEOUT=1800, finalizer held)
    DELETE NOT CONFIRMED after 45 min: .../6adb152d97493a7adf79529d still reads 200
```

75 minutes in `Deleting`, with the BlockStorage `NotUsed` (alive) and the policy
`Active` throughout. The dependency order violated in September was respected here,
so the stall is a property of the resource rather than residue of that mistake. The
earlier correction — that calling the delete *proven broken* overstated the evidence —
was right to make, and the stronger claim is now earned instead of assumed.

`BackupPolicyAssignment` stays **beta**: create, observe and drift all proven live;
only the delete does not complete.

### What the September pair and this one do NOT share

Both are `Deleting` forever, but they are not behaving identically, and a correction
to something recorded earlier today matters here.

Earlier this session I wrote that the two September records *"advance together on
roughly a 15-minute cycle"*, inferred from two samples 14.7 minutes apart
(`07:20:05Z`, `07:34:47Z`). Later samples do not support a fixed cycle — `09:23:14Z`
and `09:26:05Z` are three minutes apart. Two points were not a period, and reporting
them as one was over-reading.

A second guess of mine died the same way. Seeing `ga-bpa-b` sit untouched from
`08:11:02Z` to `09:27:31Z` — its entire 75-minute stall — until the DELETE on its own
policy touched it, I concluded the writes were cascade effects rather than a retry
loop. Then I sampled all four every 60s:

```
t+0m  ga-bpa=09:26:05  ga-bpa-b=09:28:05  ga-bpa-policy=09:26:05  ga-bpa-policy-b=09:28:04
t+1m  ga-bpa=09:29:09  ga-bpa-b=09:29:12  ga-bpa-policy=09:29:09  ga-bpa-policy-b=09:29:12
t+2m  ga-bpa=09:29:09  ga-bpa-b=09:30:03  ga-bpa-policy=09:29:09  ga-bpa-policy-b=09:30:02
t+3m  ga-bpa=09:29:09  ga-bpa-b=09:30:03  ga-bpa-policy=09:29:09  ga-bpa-policy-b=09:30:02
```

`ga-bpa` is a seven-week-old corpse with no CR, no controller and nothing of mine
touching it, and it advanced at `09:29:09Z` anyway — within three seconds of all three
others. So something server-side does sweep these records; it is not purely cascade.

What is actually observed, and all that should be claimed:

* every record in `Deleting` has its `updateDate` advanced from time to time
* an assignment and its policy always move together, to the second
* the intervals are **irregular** — 3 minutes, 1 minute, and a 73-minute gap all occur
* the `state` never changes

Three readings of this in one session, each confidently wrong before the next sample.
The mechanism is not established and this is where I stop guessing at it; the table
above is the evidence, and Aruba can say what it means.

### The volume was kept alive on purpose

The chain's teardown deletes in reverse dependency order, so after the assignment it
goes for the policy and then the volume. Letting it finish would have reproduced
exactly the September mistake: an assignment stuck in `Deleting` whose volume no
longer exists, which is what made that pair unrecoverable.

The run was stopped after the verdict line and before the volume delete. The policy's
DELETE had already gone out, so `ga-bpa-policy-b` is `Deleting` too, but
**`ga-bpa-vol` is alive** — Aruba support now has one wedged assignment whose
referenced resource still exists, next to one whose does not. That contrast is worth
more than a clean account here.

## `status.failureReason` exists, and we have never surfaced it (2026-10-26)

Found while a KMS refused to provision three times running. The API says why a
resource failed, in a field declared in **all ten** provider documents:

```
components.schemas.StatusResponseDto.properties.failureReason
  {"type": "string", "nullable": true}
```

It is `null` on a healthy resource and populated on a failed one. No RestDefinition in
this repository maps it, so every provisioning failure reaches a CR as a resource that
simply never converges, with the reason sitting unread in the response body the
controller already fetched.

```
POST /projects/{project}/providers/Aruba.Security/kms?api-version=1.0
{"metadata":{"location":{"value":"ITBG-Bergamo"},"name":"ga-kms","tags":[]},
 "properties":{"billingPeriod":"Hour"}}
→ 201 Created, status.state = InCreation
… 112s later: status.state = Failed, status.failureReason = "Ecommerce"
```

**This corrects an earlier claim in this document and in the support draft** that the
Registry and KaasBackup failures came "with no diagnostic". They may well have carried
one; we were not reading the field. What those two held is unknown and now
unrecoverable — both resources are long deleted.

### `Ecommerce` is intermittent, not an entitlement

Four KMS creates with an identical body on 2026-10-25:

| # | Outcome | Time | `failureReason` |
|---|---|---|---|
| 1 | `Failed` | 112s | `Ecommerce` |
| 2 | **`Active`** | 60s | — |
| 3 | `Failed` | 429s | `Ecommerce` |
| 4 | **`Active`** | <60s | — |

A 20 GB `blockStorages` created between #1 and #2 provisioned normally in 75s and
deleted clean, so the account was able to purchase throughout. An earlier reading of
this as "the trial can no longer buy KMS" was wrong and was retracted within the hour;
two of four succeeding is flakiness, not an entitlement boundary.

### Surfacing it is cheap, and the mechanism permits it

Checked against the controller source rather than assumed:

- `AdditionalStatusFields` carries a CEL rule making it **append-only** — new entries
  at the end are explicitly allowed, unlike `configurationFields`, which is immutable
  once the CRD exists.
- `populateStatusFields` skips a path it cannot find (`if err != nil || !found {
  continue }`), so adding it costs nothing on the resources that never fail.
- **No OAS change is needed** — the field is already declared. The destination path
  mirrors the source, so it lands at `status.status.failureReason` on the CR. Lifting
  it to a top-level key with a `responseTransform` would read better but needs a key
  the OAS does not declare, and the CRD's status schema is built from these same paths.

Applied 2026-10-26 to 26 of 34 resources — and then measured, which found the catch
below.

### It only reaches a CRD that does not yet exist (2026-10-26)

`AdditionalStatusFields` being append-only turns out to permit an edit that does
nothing. One `kubectl apply -R -f restdefinitions/` on `aruba-nfb`, two outcomes:

```
RestDefinition arubacloud-container-registry   generation 2
  additionalStatusFields = ["metadata.id","status.failureReason"]
CRD registries.arubacloud.ogen.krateo.io       generation 1   <- never updated
  status properties: [conditions, metadata]                   <- field ABSENT

CRD scheduledjobs.arubacloud.ogen.krateo.io    minted in the same apply
  status properties: [conditions, metadata, status]
  status.status:     [failureReason]                          <- field PRESENT
```

The only difference is that one CRD already existed. oasgen logs `External resource
is up to date` for the registry and emits no warning, no event and no condition;
`Synced=True` on a RestDefinition whose spec no longer describes its own CRD.

So **`failureReason` arrives on a fresh install and not on an upgrade**, and the only
way to force it onto an existing CRD is to delete the RestDefinition and recreate it,
which destroys the CRD and every CR of that kind. Reported upstream.

Confirmed on a second field, with a controlled before/after rather than an inference.
All 34 of our resources set `excludedSpecFields` (which is immutable, and mutually
exclusive with the allowlist), so this needed a throwaway RestDefinition for a
synthetic kind:

```
BEFORE  RD  generation 1   includedSpecFields ["metadata"]
        CRD generation 1   spec: [configurationRef, keyPairId, metadata, projectId]
        patch -> ["metadata","properties"]      (accepted; the CEL rule is happy)
AFTER   RD  generation 2   includedSpecFields ["metadata","properties"]
        CRD generation 1   spec: [configurationRef, keyPairId, metadata, projectId]
        RD status: Ready=True  Synced=True
```

`properties` never appears. So it is not something about `additionalStatusFields` —
both fields carry the same append-only rule, both are spec-only changes that alter
the generated schema while the OAS is byte-identical, and both are inert. Upstream
traced it to exactly that: `status.resourceHash` exists for this case and `Update`
compares it correctly, but `Observe` never does, and the runtime only calls `Update`
when `Observe` reports not-up-to-date ([oasgen#224](https://github.com/krateo-platformops/oasgen-provider/issues/224)).

Three things follow for this repo. The change is still right — new installations get
it and it costs nothing. The *measurement* of a failed resource does not depend on it:
`failureReason` is in the response body either way, so a chain or a probe reading the
API directly sees it whatever the CRD says, which is how the Kms `Ecommerce` value was
captured before any of this was declared.

**Fixed upstream, written 2026-10-26, not yet released.** A four-line check in
`Observe` beside the existing OAS-content one; `Update`'s half already compared the
hash correctly and needed nothing. What this means for an upgrade here is specific
and good: `status.resourceHash` shipped in oasgen **0.27.0** and this repo pins
**0.27.1**, so our RestDefinitions already carry a populated hash. The first reconcile
after the fix therefore reports drift once, regenerates the CRD — which is what
finally applies the `status.failureReason` fields added above — and settles. Bounded
and self-healing, not a loop. Only a RestDefinition created before 0.27.0 has an empty
hash, and even that converges after one extra regeneration.

Until that release lands, the asymmetry below stands.

And the `Job` → `ScheduledJob` rename has a second, opposite-signed effect that is
easy to miss: **a renamed kind is a new CRD, so it is minted fresh and does carry the
new status fields.** `scheduledjobs` has `status.status.failureReason`; `registries`
cannot until #224 ships. Do not read "the rename is done" as "the status fields are
live everywhere" — on an existing cluster it is the one resource where they are.

## `DELETE` returning 2xx means very little on this API (2026-10-26)

Three independent observations, all on 2026-10-25, which together say the delete
response carries almost no information about whether the delete took:

- a healthy `kms` with **no keys** needed **three** `DELETE`s over ~23 minutes. Each
  answered `202`. After the first two it still read `state: Active` — not even
  `Deleting`, so nothing was in progress — and it was gone within a minute of the third.
- the `kmip` above answered `400` while `InCreation`, then **`500` twice** at
  `CertificateAvailable`, and moved to `Deleting` anyway. The progression
  400 → 500 → `Deleting` → tombstone is the evidence; the 500 alone is not.
- a `Failed` registry has long been known to need its `DELETE` issued twice (the first
  returns `202` and leaves the resource in place).

This is the premise of [oasgen#111](https://github.com/krateo-platformops/oasgen-provider/issues/111)
from the other direction, and the argument for `resolveDeleteOutcome` treating the
observe verb as the only ground truth: a 2xx here is not a promise, and a 5xx is not a
refusal.

## oasgen 0.32.0 refuses to generate `Job` (2026-10-26)

Not a regression in our definitions — a guard we collide with. `9801a69`
([oasgen#200](https://github.com/krateo-platformops/oasgen-provider/issues/200)),
first released in **0.32.0**:

```
create failed: generated resource name "jobs" (kind "Job", CRD "jobs.arubacloud.ogen.krateo.io")
is already served by the built-in API group(s) batch; two groups claiming one name makes
`kubectl get jobs <name>` ambiguous for the BUILT-IN resource, breaking tooling unrelated
to this provider, so generation refuses it. Set spec.resource.kind to a name that does not
collide (for example a vendor prefix such as "ArubacloudJob")
```

**Corrected 2026-10-26, before acting on it.** The first version of this section said
`Job` "ceases to exist on upgrade". That is wrong, and the error was generalising from
the only case this cluster could show me: `aruba-nfb` was a *fresh* cluster. The guard
is asymmetric, in `restdefinition.go:1361-1377`:

- **fresh install**, no CRD yet → refuses. `Synced=False`, no CRD, no controller. This
  is what we hit.
- **upgrade** where `jobs.arubacloud.ogen.krateo.io` already exists → a
  `BuiltInNameCollisionExisting` warning event, and generation **continues**. The CRD
  and controller keep working.

The asymmetry is deliberate and the event message says why: refusing on an existing
CRD would block every future update without removing the collision, and renaming the
kind destroys that CRD's existing resources. So existing installations upgrade fine
and no one's `Job` resources are destroyed. What breaks is **new installs**, plus a
permanent warning event on a GA resource — which is still not a place to sit, but it
is not a race against a release cut.

All 34 kinds were checked against the built-ins; `Job` is the only collision. The
guard is correct — two groups claiming `jobs` genuinely breaks `kubectl get jobs` for
the built-in — so the fix is a rename on our side.

**Done: `schedule/Job` is now `schedule/ScheduledJob`.** `ArubacloudJob`, which the
guard's own message suggests, stutters against a group that already reads
`arubacloud.ogen.krateo.io`; the resource is a scheduled poweron/poweroff task under
`Aruba.Schedule`, so `ScheduledJob` says what it is. One `KIND_MAP` entry drives the
rename; `restdefinitions/schedule/job.yaml` → `scheduledjob.yaml`, the sample pair,
the `job-config` → `scheduledjob-config` configuration reference in both chains, and
the generated provider docs follow from a regenerate.

Renaming before GA costs nothing. Renaming after costs every user their CRs, because
a kind rename means a new CRD and the old one's resources do not come with it.
