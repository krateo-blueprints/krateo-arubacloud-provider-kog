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
