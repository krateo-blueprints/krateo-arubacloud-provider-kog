# Live-cluster test

Everything in this repository had been validated statically until this run. This
page records what a real cluster proved, and — more usefully — the **four defects
static validation could not have caught**.

**Environment:** kind (Kubernetes v1.36.1) · chart 0.9.20 · oasgen-provider 0.19.0 ·
rest-dynamic-controller 0.19.0 · all 34 RestDefinitions applied.

## Result

| Check | Result |
|---|---|
| RestDefinitions accepted | **34 / 34** |
| RestDefinitions `Ready` | **34 / 34** |
| CRDs generated | **68** (34 resources + 34 Configurations) |
| RDC controllers running | **34 / 34** |
| `<Kind>Configuration` samples apply | **34 / 34** |
| Sample CRs apply | **34 / 34** |
| Reconcile reaches `api.arubacloud.com` | **yes — HTTP 401** with a placeholder token |

That 401 is the end-to-end proof: the controller resolved the Configuration and
its Secret, built the request from the **unmodified** OAS, sent the credential in
the header the document declares, and got a real HTTP response from Aruba. Only
the credential *value* is untested — everything around it works.

### The three upstream fixes, verified in the cluster

| Fix | Evidence from the running cluster |
|---|---|
| [#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49) apiKey auth | `SubnetConfiguration` CRD exposes `authentication.apiKey` = `{tokenRef, header, valuePrefix}`, with `header.default: "Authorization"` and the description *"Defaulted from security scheme "Bearer", which declares "Authorization""*. `DbaasConfiguration` (an `http`/`bearer` spec) exposes only `authentication.bearer` — the per-document derivation is real |
| [#46](https://github.com/braghettos/krateo-oasgen-provider/issues/46) `handleParam` | `arubacloud-baremetal-hpc` passes admission with `poll.path: …/monitor/{id}` + `handleParam: id` against Aruba's **unmodified** document |
| [#45](https://github.com/braghettos/krateo-oasgen-provider/issues/45) typed maps | Correct but **latent here** — see the scope correction below |

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

### 4. Dynamic Configuration watch forbidden — upstream chart bug

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

**Fixed upstream**: [chart#31](https://github.com/braghettos/krateo-oasgen-provider-chart/pull/31).
Verified by patching the live ClusterRole and restarting: **88 errors → 0**.

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

- **A real Aruba credential.** Every call 401s with the placeholder token, so no
  request has been authenticated and **no resource has ever been created**.
- Consequently: create/update/delete round-trips, drift detection, the HPC async
  poll loop reaching `Succeeded`, and the CloudServer RESTAction sequences remain
  unexercised. They need a real token and a project — and they create billable
  resources.

## Reproducing

```sh
kind create cluster --name aruba-kog-test
helm install oasgen-provider     oci://ghcr.io/braghettos/krateo/krateo-oasgen-provider     --version 0.9.20 -n krateo-system --create-namespace --wait
helm install oasgen-provider-crd oci://ghcr.io/braghettos/krateo/krateo-oasgen-provider-crd --version 0.9.20 -n krateo-system --wait
kubectl apply -n krateo-system -f configmaps/
kubectl apply -n krateo-system -R -f restdefinitions/
kubectl get restdefinitions.ogen.krateo.io -n krateo-system   # wait for Ready
kubectl apply -f samples/arubacloud-token-secret.yaml         # edit the token first
kubectl apply -f samples/network/subnet-configuration.yaml -f samples/network/subnet.yaml
```
