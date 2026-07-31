# Aruba Cloud KOG — issues that require an oasgen-provider evolution

This document enumerates every issue found while generating RestDefinitions for
**all** Aruba Cloud APIs directly from the official OpenAPI specifications
(`https://api.arubacloud.com/openapi/<provider>.json`, vendored under
`openapi/_source/`) with the **braghettos** fork of
[`oasgen-provider`](https://github.com/braghettos/krateo-oasgen-provider) and
**no wrapper/proxy web service**.

It is written to be actionable: each issue states what the API does, why the
current oasgen-provider cannot handle it natively, the workaround applied in this
repository (if any), and the concrete provider evolution that would remove the
workaround.

Scope analysed: 11 specs, ~108 operations, **34 manageable resources** across the
`network`, `compute`, `container`, `database`, `storage`, `security`, `schedule`,
`baremetal`, `project` and `metering` providers.

> Legend — **Status**: 🟥 blocks generation without patching · 🟧 works only via a
> RestDefinition workaround · 🟩 solved by the fork, documented for completeness.

---

## 0. Summary table

| # | Issue | Status | Resources affected | Evolution needed |
|---|-------|--------|--------------------|------------------|
| A1 | `nullable: true` unsupported | 🟥 | all (4117 keys stripped) | Accept OAS 3.0 `nullable`; auto-convert to 3.1 null-union |
| A2 | `additionalProperties: {schema}` unsupported | 🟥 | container, metering, compute, storage, audit (42) | Support typed free-form maps (`map[string]T`) |
| A3 | `readOnly` / `writeOnly` ignored | 🟧 | metering, network, container, compute, storage (25) | Honour `readOnly` → status-only; `writeOnly` → create-only |
| A4 | `number` / `format: double` coerced to integer | 🟧 | all billing/`price` fields (18) | Native `number` (float) type in CRD generation |
| A5 | `format` only appended to description | 🟧 | all (int32/int64/date-time/uuid/uri) | Map `format` to CRD `format`/validation |
| A6 | numeric/length/pattern constraints dropped | 🟧 | scattered (`minLength`, …) | Emit CRD validation from OAS constraints |
| A7 | Bearer declared as `apiKey`-in-header, not `http` | 🟥 | 8 specs | Treat `apiKey` header schemes as usable Bearer credentials |
| B1 | `metadata`-wrapped name/id (nested identifier) | 🟩 | ~28 resources | Solved (nested identifiers + `requestFieldMapping`) — was the reason for the old subnet proxy |
| B2 | Different path-param name per verb | 🟩/🟧 | `database/Database`, `schedule/BackupPolicy` | Solved per-verb; a spec-level alias would be cleaner |
| B3 | `findby` list envelope (`{total, values[]}`) | 🟧 | all list endpoints | Explicit `findby.itemsPath` / response-collection selector |
| B4 | Secret-bearing spec fields (password, keys) | 🟧 | `database/DatabaseUser`, `compute/KeyPair`, `container/Registry` | `secretRef` resolver + OAS-declarable `*SecretRef` field |
| C1 | Lifecycle expressed as POST action sub-endpoints | 🟥 | compute, container, database, project, baremetal | First-class "action verbs" or `createApiRef`/`updateApiRef` delegation |
| C2 | Async readiness via `status.state`, not an op handle | 🟧 | all create/update | Status-field readiness polling in `async` (not only operation-handle) |
| C3 | Create requires multiple chained calls | 🟥 | `compute/CloudServer` | Multi-call composition (`createApiRef` / Snowplow) |
| C4 | Resource has no delete verb | 🟧 | `baremetal/Hpc` | Allow lifecycle without delete; skip finalizer teardown |
| C5 | Update only via sub-endpoint (no `PUT {id}`) | 🟧 | `compute/CloudServer` | `updateApiRef` delegation / action verbs |
| D1 | One OAS document per RestDefinition | 🟧 | `compute` (create in `_v1.1`) | Allow multiple OAS documents / overlays per RestDefinition |
| D2 | Immutable `identifiers`/`kind`/`configurationFields` | 🟩 | operational | Documented; no change required |

---

## Category A — OAS constructs oasgen-provider cannot consume

These force a **pre-processing patch** of the specification (`scripts/patch_oas.py`).
Every patch is a place where the spec had to be altered to fit the tool rather
than the tool fitting the spec, so each one is a candidate for native support.

### A1 — `nullable: true` (🟥 blocks, 4117 occurrences)
The Aruba specs are OAS **3.0.1**, which expresses "may be null" with
`nullable: true`. oasgen-provider does not support it (per the fork README), so
`patch_oas.py` strips it from **4117** schema nodes. Stripping is safe for CRD
generation but silently changes the contract (a field the API may return as
`null` becomes non-nullable in the CRD, which can trip strict response
validation in the controller).

**Evolution:** accept `nullable` and translate OAS 3.0 `nullable: true` into the
OAS 3.1 `type: [<t>, "null"]` union during ingestion, instead of requiring the
author to hand-edit thousands of nodes.

### A2 — `additionalProperties` as an object (🟥 blocks, 42 occurrences)
Only the boolean form is supported. The Aruba APIs use typed free-form maps for
genuinely useful, resource-facing fields, not just catalog noise:

- `metadata.annotations` / `metadata.labels` on `container/Kaas`,
  `container/KaasBackup`, `container/Registry`, `storage/*`.
- `AlertModel.labels` / `AlertModel.annotations`, `AlertAction.parameters` on
  `metering/AlertRule`.
- `CloudServerNetworkInterfaceDto.properties` on `compute/CloudServer`.

`patch_oas.py` coerces these to `additionalProperties: true`, which **loses the
value type** (they become untyped maps in the CRD).

**Evolution:** support `additionalProperties: {type: ...}` → a typed
`map[string]T` in the generated CRD. Annotations/labels are first-class
Kubernetes concepts; degrading them to untyped is a real ergonomic loss.

### A3 — `readOnly` / `writeOnly` (🟧 ignored, 25 occurrences)
Dropped by the patch. Concrete server-managed fields that should be
**status-only** but currently land in the spec as writable:

- `metering/AlertRule`: `lastReception`, `lastActivation`, `lastEdit`.
- `network`: `VpcTypologyExtraInfo.maxVpc{Sg,Subnet,VpnTunnel}Count`.
- `container`: kubernetes version display fields; `compute/storage`:
  `TemplateDto.sshEnabled`.

Leaving them writable invites spurious drift and confusing specs.

**Evolution:** map `readOnly: true` to a status/output-only field and
`writeOnly: true` to a create-only field excluded from Observe/diff.

### A4 — `number` / `format: double` (🟧 precision loss, 18 occurrences)
oasgen-provider converts `number` to `integer`. Every affected field is a
monetary amount (`AddonDto.price`, `EcommerceDto…totalPrice`,
`Kaas…nodePools[].instance.price`). Coercing `12.99` to an integer is data
corruption. These are read-only billing fields today, but the type gap is real.

**Evolution:** first-class floating-point (`number`) support in CRD generation
and in the controller's response handling.

### A5 — `format` not honoured (🟧)
`format` is only appended to the field description. Pervasive here:
`int32`/`int64` (numeric width), `date-time` (timestamps on every resource),
`uuid` (ids), `uri` (self/links). No CRD-level validation is produced.

**Evolution:** translate common formats to CRD `format` and/or validation.

### A6 — constraint keywords dropped (🟧)
`minLength`, `maxLength`, `minimum`, `maximum`, `pattern`, `min/maxItems` are
ignored, so CRDs accept values the API will reject (round-trip 400s that could
be caught at admission).

**Evolution:** emit CRD `x-kubernetes-validations` / OpenAPI validation from OAS
constraints.

### A7 — security scheme shape (🟥 blocks auth, 8 specs)
Seven of eleven specs declare the Bearer token as
`type: apiKey, in: header, name: Authorization`. oasgen-provider only wires
`http`/`bearer` (or `basic`) schemes, so as-published the token would never be
treated as a credential. `patch_oas.py` rewrites it to
`{type: http, scheme: bearer}` (the exact fix the original blueprint documented
in `oas_changes_references.md`).

**Evolution:** recognise an `apiKey`-in-`Authorization`-header scheme as a Bearer
credential, or document a mapping, so the spec need not be edited.

---

## Category B — resource-shape gaps

### B1 — `metadata`-wrapped name & id / nested identifiers (🟩 solved — this is why the proxy existed)
Almost every Aruba resource nests its human name and server id inside a
`metadata` object: the **create** body is `{metadata:{name,location,tags}, properties:{…}}`
and the **read** body returns the id at `metadata.id`. The original blueprint
shipped a Go **`subnet-plugin`** whose *entire purpose* was to flatten this
`metadata` object, because "nested fields used as identifiers are not fully
supported" (quoted from the old plugin README).

The braghettos fork removes that need. Every metadata-wrapped resource in this
repo uses, with **no proxy**:

```yaml
identifiers: [metadata.name]
additionalStatusFields: [metadata.id]
excludedSpecFields: [id]
verbsDescription:
  - {action: get,    method: GET,    path: .../{id}, requestFieldMapping: [{inPath: id, inCustomResource: status.metadata.id}]}
  - {action: update, method: PUT,    path: .../{id}, requestFieldMapping: [{inPath: id, inCustomResource: status.metadata.id}]}
  - {action: delete, method: DELETE, path: .../{id}, requestFieldMapping: [{inPath: id, inCustomResource: status.metadata.id}]}
```

This is the fork's own canonical Subnet example, applied uniformly to all ~28
metadata-wrapped resources — **the single biggest proxy elimination.**

*Residual ergonomic note:* the pattern is verbose (repeated `requestFieldMapping`
on three verbs for every resource). A per-resource default such as
`idParamSource: status.metadata.id` would remove ~90 lines of boilerplate across
this repo. Nice-to-have, not a blocker.

### B2 — different path-parameter name per verb (🟩 handled, brittle)
The source OAS is internally inconsistent for two resources:
`database/Database` uses `{databaseName}` on GET but `{name}` on DELETE;
`schedule/BackupPolicy` uses `{id}` on GET but `{backupPolicyId}` on PUT/DELETE.
The generator maps each verb's own path parameter individually, so it works, but
only because `requestFieldMapping` is per-verb. Authors hitting this by hand will
be surprised.

**Evolution:** none strictly required; a spec-level path-parameter alias would be
cleaner than relying on the author to notice the mismatch.

### B3 — `findby` list envelope (🟧 assumption)
Every Aruba list endpoint returns `{total, self, prev, next, first, last, values:[…]}`
— the resources are under `values`, not at the top level. The RestDefinitions
assume the controller can locate the item array inside this envelope (the old
plugin returned the same shape, so the controller must already cope). There is no
field to declare it explicitly.

**Evolution:** an explicit `findby.itemsPath` (e.g. `values`) / response-collection
selector, so the envelope shape is declared rather than inferred. The
`continuationToken` pagination block already extracts a token from the response;
extracting the item array is the natural companion.

### B4 — secret-bearing spec fields (🟧)
Some create bodies carry credentials that should come from a Kubernetes Secret,
not sit in plaintext in the CR spec:
`database/DatabaseUser.password`, `compute/KeyPair` (public/returned private
key), `container/Registry` admin password.

The fork ships a `secretRef` **resolver** (`fieldMapping[].resolver`) that
substitutes a Secret value into a request field — but its
`nameFromCustomResource`/`keyFromCustomResource` JSONPaths must point at CR spec
fields, and those fields do not exist in an OAS-derived schema. So today the
password remains a plaintext spec string (left as-is by the generator).

**Evolution:** a supported way to (a) declare a synthetic `*SecretRef` object
field in the generated CRD and (b) exclude the plaintext field, so `secretRef`
can source it. Without both halves, `secretRef` cannot be wired from a pure OAS.

---

## Category C — lifecycle patterns not expressible as five CRUD verbs

This is where genuine, non-cosmetic evolution is required.

### C1 — lifecycle as POST action sub-endpoints (🟥)
oasgen models exactly five verbs (findby/get/create/update/delete). Many Aruba
operations are neither — they are imperative actions on an existing resource:

| Provider | Resource | Action-only endpoints (not expressible today) |
|----------|----------|-----------------------------------------------|
| compute | CloudServer | `poweron`, `poweroff`, `password`, `associateDisassociateElasticIPs`, `associateDisassociateSecurityGroups`, `associateDisassociateSubnets`, `attachDetachDataVolumes`, `restore` |
| container | Kaas | `detach`, `nodePools/{name}` (PUT), `nodes/{id}/attach`, `nodes/{id}/detach`, `download` (kubeconfig) |
| container | Registry | `resetAdminPassword` |
| database | DBaaS/Backup | `backups/{id}/download`, `backups/{id}/restore`, `users/{u}/password` |
| project | Folder | `automaticrenew` (enable/disable) |
| baremetal | Hpc | `name` (rename), `automaticrenew` |

These are dropped from the generated RestDefinitions (they cannot be verbs).

**Evolution options:**
1. First-class **action verbs** — declarative, triggered by spec intent (e.g.
   `spec.powerState: On|Off` mapped to a POST action), with idempotent
   convergence.
2. **Snowplow delegation** — the fork already has `createApiRef`/`updateApiRef`/
   `deleteApiRef`/`observeApiRef` to delegate a verb to a multi-call RESTAction.
   This dissolves these proxies **provided** each RESTAction is authored; it is
   the recommended path today for CloudServer (see C3). It needs the RESTActions
   to exist, which is out of scope for a pure-OAS generator.

### C2 — async readiness via `status.state`, not an operation handle (🟧)
Aruba creates return the resource immediately with `status.state = InCreation`,
transitioning to `Active` (and `baremetal` exposes a dedicated
`GET …/hpcs/monitor/{id}` progress endpoint). The fork's `async` block polls an
**operation handle** returned by the trigger call; Aruba instead wants "poll the
resource's own GET until `status.state ∈ {Active}`".

**Evolution:** a status-field readiness mode for `async` (poll the resource GET,
match a JSONPath against success/failure value sets) in addition to the
operation-handle mode. Until then, readiness relies on Observe re-running until
the resource looks settled, with no explicit "failed provisioning" detection.

### C3 — multi-call create composition (🟥) — `compute/CloudServer`
A usable CloudServer is created by chaining calls: create the server (OAS
**v1.1**), then `attachDetachDataVolumes`, `associateDisassociate{Subnets,
SecurityGroups,ElasticIPs}`, then `poweron`. A single `create` verb cannot
express this. The generated RestDefinition therefore covers only
create/get/findby/delete.

**Evolution:** `createApiRef` delegation to a Snowplow RESTAction that runs the
sequence idempotently (the fork explicitly designed `createApiRef` for exactly
"create instance → attach disk → start"). First-class multi-step create would be
the fuller solution.

### C4 — resource with no delete verb (🟧) — `baremetal/Hpc`
HPC bare-metal has create/get/list but **no** DELETE (decommission is a separate
commercial flow). oasgen tolerates a missing delete verb, but the reconcile model
assumes deletability; the RestDefinition omits `delete`.

**Evolution:** an explicit "non-deletable / retain" resource mode so the absence
of a delete verb is intentional rather than incidental.

### C5 — update only via sub-endpoint (🟧) — `compute/CloudServer`
CloudServer has no `PUT {id}`; every mutation is an action endpoint (C1). With no
update verb, drift on spec fields cannot be reconciled.

**Evolution:** `updateApiRef` delegation, or action verbs (C1).

---

## Category D — cross-cutting

### D1 — one OAS document per RestDefinition (🟧)
`compute/CloudServer`'s create lives in a **separate** document
(`compute-provider_v1.1.json`) from its get/list/delete
(`compute-provider.json`). A RestDefinition references a single `oasPath`, so
`patch_oas.py` had to **merge** the v1.1 `POST /cloudServers` into the base
document before generation.

**Evolution:** allow a RestDefinition to reference multiple OAS documents (or an
overlay), so version-split APIs need not be manually spliced.

### D2 — immutability of structural fields (🟩 operational)
`resourceGroup`, `resource.kind`, `identifiers`, `additionalStatusFields`,
`excludedSpecFields`, `configurationFields` are immutable after creation
(enforced by CEL in the CRD). Fine, but it means the choices the generator bakes
in (e.g. `identifiers: [metadata.name]`) cannot be changed without recreating the
RestDefinition. No change requested — documented so operators know a regeneration
that alters these fields requires delete + recreate.

---

## Appendix — how each old `subnet-plugin` endpoint maps to a native feature

The original blueprint's Go proxy existed only to reshape the `metadata` object.
Every one of its endpoints is now a declarative feature:

| Old plugin behaviour | Native replacement |
|----------------------|--------------------|
| Flatten `metadata.*` → top-level on GET/LIST responses | nested `additionalStatusFields: [metadata.id]` + `identifiers: [metadata.name]` (B1); or `fieldMapping.inResponse` for arbitrary reshaping |
| Un-flatten top-level `name` → `metadata.name` on create/update request | send the native `{metadata:{…}}` body directly (no reshape needed); `requestTransform` (gojq) if a body rewrite is ever required |
| Map `{id}` path param from flattened `id` | `requestFieldMapping: [{inPath: id, inCustomResource: status.metadata.id}]` (B1) |
| Forward Bearer `Authorization` header | native Bearer from the (patched) `http`/`bearer` security scheme (A7) |
| Proxy list envelope `{total, values}` | controller reads the envelope directly (B3) |

Result: **the `subnet-plugin` is fully eliminated**, and no new plugin is
introduced for any of the 34 resources. The only genuinely proxy-shaped needs
that remain (compute CloudServer multi-call create/lifecycle) are covered by the
fork's Snowplow `*ApiRef` delegation rather than a bespoke web service.
