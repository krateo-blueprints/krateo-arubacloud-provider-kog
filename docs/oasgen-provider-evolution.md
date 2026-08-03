# Aruba Cloud KOG — issues that require an oasgen-provider evolution

This document enumerates every issue found while generating RestDefinitions for
**all** Aruba Cloud APIs directly from the official OpenAPI specifications
(`https://api.arubacloud.com/openapi/<provider>.json`, vendored under
`openapi/`, byte-for-byte unmodified) with the **braghettos** fork of
[`oasgen-provider`](https://github.com/braghettos/krateo-oasgen-provider) and
**no wrapper/proxy web service**.

It is written to be actionable: each issue states what the API does, why the
current oasgen-provider cannot handle it natively, and the concrete provider
evolution that would close the gap.

**This repository applies no workarounds to the specifications themselves.** They
are vendored byte-for-byte as Aruba publishes them (see
[OAS policy](oas-patches.md)); every gap below is therefore either harmless in
practice, handled inside the RestDefinition, or a live limitation tracked
upstream.

Scope analysed: 11 specs, ~108 operations, **34 manageable resources** across the
`network`, `compute`, `container`, `database`, `storage`, `security`, `schedule`,
`baremetal`, `project` and `metering` providers.

> Legend — **Status**: 🟥 blocks generation without patching · 🟧 works only via a
> RestDefinition workaround · 🟩 solved by the fork, documented for completeness.

---

## 0. Summary table

| # | Issue | Status | Resources affected | Evolution needed |
|---|-------|--------|--------------------|------------------|
| A1 | `nullable: true` ignored | 🟧 | all (~4119 keys) | Accept OAS 3.0 `nullable`; auto-convert to 3.1 null-union. **Not** a blocker — the strip it forced was a no-op and has been removed |
| A2 | `additionalProperties: {schema}` unsupported | 🟩 | container, metering, compute, storage, audit (42) | **Shipped** in oasgen 0.18.0 — typed maps reach the CRD ([#45](https://github.com/braghettos/krateo-oasgen-provider/issues/45)) |
| A3 | `readOnly` / `writeOnly` ignored | 🟧 | metering, network, container, compute, storage (25) | Honour `readOnly` → status-only; `writeOnly` → create-only. Keywords now preserved in the specs |
| A4 | `number` / `format: double` coerced to integer | 🟧 | all billing/`price` fields (18) | Native `number` (float) type in CRD generation |
| A5 | `format` only appended to description | 🟧 | all (int32/int64/date-time/uuid/uri) | Map `format` to CRD `format`/validation |
| A6 | numeric/length/pattern constraints dropped | 🟧 | scattered (`minLength`, …) | Emit CRD validation from OAS constraints |
| A7 | `apiKey` security schemes unsupported | 🟩 | 8 specs (24 of 34 resources) | **Shipped** in oasgen 0.19.0 + RDC 0.19.0 — `authentication.apiKey` with `header`/`valuePrefix`, and skipped schemes now surfaced ([#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49)) |
| B1 | `metadata`-wrapped name/id (nested identifier) | 🟩 | ~28 resources | Solved (nested identifiers + `requestFieldMapping`) — was the reason for the old subnet proxy |
| B2 | Different path-param name per verb | 🟩/🟧 | `database/Database`, `schedule/BackupPolicy` | Solved per-verb; a spec-level alias would be cleaner |
| B3 | `findby` list envelope (`{total, values[]}`) | 🟧 | all list endpoints | Explicit `findby.itemsPath` / response-collection selector |
| B4 | Secret-bearing spec fields (password, keys) | 🟧 | `database/DatabaseUser`, `compute/KeyPair`, `container/Registry` | `secretRef` resolver + OAS-declarable `*SecretRef` field |
| C1 | Lifecycle expressed as POST action sub-endpoints | 🟥 | compute, container, database, project, baremetal | First-class "action verbs" or `createApiRef`/`updateApiRef` delegation |
| C2 | Async readiness | 🟩 | all create/update | Solved by `async` (requeue); wired on `Hpc`. Poll-path validation + `handleParam` **shipped** in 0.18.0 ([#46](https://github.com/braghettos/krateo-oasgen-provider/issues/46)). Residual: open-string state enums |
| C6 | Delete-direction `*ApiRef` extras lack the spec | 🟩 | `compute/CloudServer` (any delegated delete) | **Shipped** in RDC 0.18.0 — spec forwarded on every direction ([rdc#41](https://github.com/braghettos/krateo-rest-dynamic-controller/issues/41)) |
| C3 | Create requires multiple chained calls | 🟥 | `compute/CloudServer` | Multi-call composition (`createApiRef` / Snowplow) |
| C4 | Resource has no delete verb | 🟧 | `baremetal/Hpc` | Allow lifecycle without delete; skip finalizer teardown |
| C5 | Update only via sub-endpoint (no `PUT {id}`) | 🟧 | `compute/CloudServer` | `updateApiRef` delegation / action verbs |
| D1 | One OAS document per RestDefinition | 🟧 | `compute` (create in `_v1.1`) | Allow multiple OAS documents / overlays per RestDefinition |
| D2 | Immutable `identifiers`/`kind`/`configurationFields` | 🟩 | operational | Documented; no change required |

---

## Category A — OAS constructs oasgen-provider cannot consume

These are constructs the toolchain does not consume natively. **None of them is
patched any more** — this repo ships Aruba's documents byte-for-byte unmodified
(see [OAS policy](oas-patches.md)), so each entry below is either harmless in
practice or a live limitation tracked upstream. §A7 is the one that currently
costs functionality.

### A1 — `nullable: true` (🟧 ignored, ~4119 occurrences — but NOT a blocker)
The Aruba specs are OAS **3.0.1**, which expresses "may be null" with
`nullable: true`. oasgen-provider does not read the keyword at all — there is no
`Nullable` field on its domain schema and zero references in
`internal/tools/oas2jsonschema/` — so it is silently dropped during conversion
and the generated CRD declares the field non-nullable.

**Correction (this repo used to overstate it).** An earlier revision graded this
🟥 "blocks" and had a patch script strip all ~4119 nodes. That was wrong on both
counts: the strip was a **no-op** (regenerating every RestDefinition without it
produces byte-identical output), and it could never have "tripped strict response
validation in the controller" because RDC does not validate response bodies at
all — `ValidateRequest` covers parameters/query/headers/cookies, and body
validation is commented out pending a stable libopenapi-validator. The strip has
been removed; the specs now carry `nullable` exactly as published.

**Evolution:** translate OAS 3.0 `nullable: true` into the OAS 3.1
`type: [<t>, "null"]` union during ingestion, so the CRD permits the nulls the
API actually returns. Real, but a fidelity gap rather than a blocker — and one
that only becomes user-visible if/when body validation is enabled.

### A2 — `additionalProperties` as an object (🟩 SHIPPED in oasgen 0.18.0)
> **Resolved.** oasgen 0.18.0 carries the object form through to crdgen as a
> typed map: the adapter recurses the value schema through the same
> guard/visited/depth machinery as `items`, and the serializer emits it. The
> workaround below (coercing to `additionalProperties: true`) has been **removed**,
> so all 42 maps now keep their value type and
> validation in the generated CRDs. Requires oasgen >= 0.18.0.
> ([oasgen-provider#45](https://github.com/braghettos/krateo-oasgen-provider/issues/45))

Historical context — only the boolean form used to be supported. The Aruba APIs
use typed free-form maps for genuinely useful, resource-facing fields, not just
catalog noise:

- `metadata.annotations` / `metadata.labels` on `container/Kaas`,
  `container/KaasBackup`, `container/Registry`, `storage/*`.
- `AlertModel.labels` / `AlertModel.annotations`, `AlertAction.parameters` on
  `metering/AlertRule`.
- `CloudServerNetworkInterfaceDto.properties` on `compute/CloudServer`.

The repo used to coerce these to `additionalProperties: true`, which
**lost the value type** (they became untyped maps in the CRD). Annotations and
labels are first-class Kubernetes concepts, so degrading them to untyped was a
real ergonomic loss — which is why this was the first gap filed and fixed.

### A3 — `readOnly` / `writeOnly` (🟧 ignored, 25 occurrences)
Ignored by oasgen (zero references in `oas2jsonschema`), so these land in the CRD
spec as writable. This repo used to strip them too; that was likewise a **no-op**
and has been removed — the keywords are now preserved in the shipped specs, ready
for the day oasgen honours them. Concrete server-managed fields that should be
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

### A7 — `apiKey` security schemes (🟩 SHIPPED in oasgen 0.19.0 + RDC 0.19.0)
> **Resolved.** oasgen 0.19.0 generates an `authentication.apiKey` block
> (`tokenRef` + `header` + optional `valuePrefix`), defaulting `header` from the
> scheme's declared name when the document has exactly one apiKey scheme; RDC
> 0.19.0 sends `Header.Set(header, valuePrefix+token)`. A scheme it still cannot
> support is no longer skipped in silence. **All 34 resources can now authenticate
> against Aruba's unmodified specs.**
>
> One Aruba-specific detail: the generated Configurations must set
> `valuePrefix: 'Bearer '` (trailing space included) because Aruba declares
> `apiKey` but expects bearer framing — oasgen rightly does not default a prefix.
> The generated samples derive this from each document's own scheme; see
> [authentication](authentication.md).
> ([oasgen-provider#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49))

Historical context follows.
Eight of eleven specs declare the token as
`type: apiKey, in: header, name: Authorization`. oasgen wires only `http`
(`bearer`/`basic`) schemes: `createSchemaForSecurityScheme` returns an error for
anything else and `buildAuthMethodsSchemaMap` swallows it with a bare `continue`.
The generated `<Kind>Configuration` therefore has **no `authentication` block at
all** — there is no field through which to supply a credential, and nothing warns.

| Aruba spec | Scheme | Status |
|---|---|---|
| compute, database, schedule | `http` / `bearer` | ✅ authenticates |
| network, container, security, storage, baremetal, project, metering | `apiKey` in header | ❌ **24 of 34 resources cannot authenticate** |

This repo previously rewrote the scheme to `{type: http, scheme: bearer}`. That
patch is **gone**: the specs are now shipped unmodified
([OAS policy](oas-patches.md)), so the gap is visible instead of papered over.

**Evolution:** support `apiKey`-in-header (sending the header the document
declares), and — independently — never skip a security scheme silently; a
document whose only scheme is unsupported should raise a warning or condition
rather than yield a Configuration with no way to authenticate.

> Filed upstream: [braghettos/krateo-oasgen-provider#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49).

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

### B3 — `findby` list envelope (🟧 verified heuristic)
Every Aruba list endpoint returns `{total, self, prev, next, first, last, values:[…]}`
— the resources are under `values`, not at the top level. **Verified against RDC
source** (`restclient.go: ExtractItemsFromResponse`): the controller takes the
**first array-valued key** it finds in the response object. Aruba's envelope has
exactly one array, so this works — but it is a heuristic over a randomly-ordered
Go map, and a response carrying a second array field would make item extraction
nondeterministic. There is no field to declare the items' location explicitly.

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

> **A working, proxy-free solution to this whole category is implemented in this
> repository** — see [`lifecycle-beyond-crud.md`](lifecycle-beyond-crud.md).
> `compute/CloudServer` delegates create/update/delete to Snowplow RESTActions via
> `*ApiRef` (`restactions/compute/`), and a Krateo Composition
> (`compositions/`) provisions a whole environment. The items below therefore
> describe the *gap* and, where relevant, whether it is closed by delegation
> today or still wants first-class support.

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

### C2 — async readiness (🟩 largely solved; one ergonomic residual)
Aruba creates return the resource immediately with `status.state = InCreation`,
transitioning to `Active` (and `baremetal` exposes a dedicated
`GET …/hpcs/monitor/{id}` progress endpoint). This is **controller-native
territory** and the fork's per-verb `async` block handles it — in `requeue` mode
it is a non-blocking, level-based wait with terminal-failure detection. Both Aruba
shapes are expressible:

- **operation handle** — `baremetal/Hpc` is wired end to end (create returns
  `monitorUri`; poll Aruba's own `…/hpcs/monitor/{id}` with `handleParam: id` for
  `Succeeded`/`Failed`). See [async-readiness](async-readiness.md).
- **status field** — point `async.poll` at the resource's own GET with
  `statusPath: status.state`, `successValues: [Active]`. Expressible today.

**Residual (ergonomic only):** for the status-field shape the terminal values
(`Active`, …) must be supplied per resource because the API models `status.state`
as an **open string** (`{"type":"string","nullable":true}`), not an enum. This is
by design, not an oversight: the specs are generated from ASP.NET server models and
do emit enums where the backing type is a C# enum (18 of them, including a
`ResourceProviderClaimStatus` lifecycle enum) — but `state` is a deliberately
loosely-typed projection (its runtime values `Active`/`InCreation`/`Updating`/
`Deleted` don't even match that enum, which is used only on a failure DTO). See
[async-readiness §why](async-readiness.md#why-the-state-field-has-no-enum). The
operation-handle shape has no such gap (HPC's monitor enum is self-describing). A
convenience would be a "poll-own-get until `status.state`" shorthand so the
status-field pattern needs no hand-entered value set. This no longer blocks
readiness — it is wired for HPC and enable-per-resource elsewhere.

**Addendum (adversarial review) — 🟩 SHIPPED in 0.18.0.** Two hardening asks came
out of reading the executor source, and both were fixed:

- **`async.poll.handleParam`** (oasgen 0.18.0 + RDC 0.18.0) declares *which* path
  parameter receives the operation handle, instead of hardcoding `operationId`.
  Aruba's published `.../hpcs/monitor/{id}` is now used **unmodified** with
  `handleParam: id`, and the OAS-renaming workaround has been removed.
- **Admission-time validation** of `async.poll.path` (oasgen
  `restdefinition/helper.go: validateAsyncPollPaths`) now checks both halves of
  the contract — exact OAS key *and* the `{handleParam}` token — when the
  RestDefinition is processed, instead of failing on the first poll.

Full evidence: [adversarial-review](adversarial-review.md) findings #1/#6.
([oasgen-provider#46](https://github.com/braghettos/krateo-oasgen-provider/issues/46))

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

### C6 — delete-direction `*ApiRef` invocations do not receive the spec (🟩 SHIPPED in RDC 0.18.0)
> **Resolved.** RDC 0.18.0's `buildExtras` forwards the CR spec on **every**
> `*ApiRef` direction (`writesDesiredState` now governs only whether the
> RESTAction's *result* is projected into status, not what it *receives*). The
> CloudServer delete RESTAction reads `.spec.projectId` like its create/update
> siblings, and the static `deleteApiRef.extras.projectId` workaround — which
> pinned one RestDefinition to one project — has been removed.
> ([rdc#41](https://github.com/braghettos/krateo-rest-dynamic-controller/issues/41))

**Found by the adversarial review** (RDC `observe_restaction.go: buildExtras`):
create/update delegation forwarded the whole CR spec to the RESTAction, but a
**delete** invocation forwarded only static extras, `name`/`namespace`/`uid`, and
identifier values keyed by their path string (`.["metadata.name"]`). A teardown
sequence needing any other spec field — for Aruba, the `projectId` that scopes
every URL — could not obtain it dynamically.

The failure mode was nasty enough to be worth remembering: the RESTAction's
guards saw nulls, skipped every step, snowplow returned success, RDC's existence
check found the resource alive, and the finalizer never released — a silent
delete deadlock. **This is exactly what happens if you run these manifests
against RDC < 0.18.0** (see [troubleshooting](troubleshooting.md)).

## Category D — cross-cutting

### D1 — one OAS document per RestDefinition (🟧)
`compute/CloudServer`'s create lives in a **separate** document
(`compute-provider_v1.1.json`) from its get/list/delete
(`compute-provider.json`). A RestDefinition references a single `oasPath`, so a
resource needing verbs from both documents cannot express them.

This repo used to merge the v1.1 `POST` into the base document; that merge is
**gone**, and nothing was lost — CloudServer delegates create via `createApiRef`,
so no verb ever referenced the v1.1 path. The constraint is still real for any
future resource that needs a *native* verb from a second document.

**Evolution:** allow a RestDefinition to reference multiple OAS documents (or an
overlay), so version-split APIs need not be spliced.

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
