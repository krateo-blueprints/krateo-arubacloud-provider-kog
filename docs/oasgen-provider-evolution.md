---
type: Decision
title: oasgen-provider evolution
description: Generation gaps found against the unmodified specs, and which were fixed upstream.
tags: [aruba, kog]
timestamp: 2026-09-05T00:00:00Z
---

# Aruba Cloud KOG — issues that require an oasgen-provider evolution

This document enumerates every issue found while generating RestDefinitions for
**all** Aruba Cloud APIs directly from the official OpenAPI specifications
(`https://arubacloud.github.io/api/openapi/<provider>.json`, vendored under
`openapi/`, byte-for-byte unmodified) with
[`oasgen-provider`](https://github.com/krateo-platformops/oasgen-provider) and
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

> **Pin status (2026-11-07): 0.52.0.** 0.53.0 is skipped (stamps `default: {}` on 240
> optional object paths), and **0.54.0 is skipped too** — it fixes the stamping but no
> managed resource can read its auth Secret, so nothing reconciles. Both are measured;
> see the appendices. 0.54.1 pending.


| # | Issue | Status | Resources affected | Evolution needed |
|---|-------|--------|--------------------|------------------|
| A1 | `nullable: true` ignored | 🟩 | all (~4119 keys) | **Shipped** in oasgen 0.52.0 ([#424](https://github.com/krateo-platformops/oasgen-provider/pull/424)) — `nullable` and `minProperties` reach the CRD; measured present at 0.52.0 and absent at 0.41.0. The strip this once forced was a no-op and has been removed |
| A2 | `additionalProperties: {schema}` unsupported | 🟩 | container, metering, compute, storage, audit (42) | **Shipped** in oasgen 0.18.0 — typed maps reach the CRD ([#45](https://github.com/krateo-platformops/oasgen-provider/issues/45)) |
| A3 | `readOnly` / `writeOnly` ignored | 🟧 | metering, network, container, compute, storage (25) | Honour `readOnly` → status-only; `writeOnly` → create-only. Keywords now preserved in the specs |
| A4 | `number` / `format: double` coerced to integer | 🟧 | all billing/`price` fields (18) | Native `number` (float) type in CRD generation |
| A5 | `format` only appended to description | 🟧 | all (int32/int64/date-time/uuid/uri) | Map `format` to CRD `format`/validation |
| A6 | numeric/length/pattern constraints dropped | 🟧 | scattered (`minLength`, …) | Emit CRD validation from OAS constraints |
| A7 | `apiKey` security schemes unsupported | 🟩 | 8 specs (24 of 34 resources) | **Shipped** in oasgen 0.19.0 + RDC 0.19.0 — `authentication.apiKey` with `header`/`valuePrefix`, and skipped schemes now surfaced ([#49](https://github.com/krateo-platformops/oasgen-provider/issues/49)) |
| B1 | `metadata`-wrapped name/id (nested identifier) | 🟩 | ~28 resources | Solved (nested identifiers + `requestFieldMapping`) — was the reason for the old subnet proxy |
| B2 | Different path-param name per verb | 🟩/🟧 | `database/Database`, `schedule/BackupPolicy` | Solved per-verb; a spec-level alias would be cleaner |
| B3 | `findby` list envelope (`{total, values[]}`) | 🟩 | all list endpoints | **Shipped** — `findby.itemsPath`, optional, with ≥2 arrays an error rather than a guess ([#110](https://github.com/krateo-platformops/oasgen-provider/issues/110)) |
| B4 | Secret-bearing spec fields (password, keys) | 🟧 | `database/DatabaseUser`, `compute/KeyPair`, `container/Registry` | `secretRef` resolver + OAS-declarable `*SecretRef` field |
| B5 | `findby` reads only the first page | 🟥 | all 36 (any collection >50 items) | Implement the `offset` strategy already designed in `types.go`. Measured: Aruba's page is 50 and `limit` is honoured downward only, so `limit=2000` still returns 50. No `pagination` block can express this today, and the findby entry point returns early to `CallFindBySingle` before any paginator exists, so the 404 is unconditional after one page |
| C1 | Lifecycle expressed as POST action sub-endpoints | 🟥 | compute, container, database, project, baremetal | First-class "action verbs" or `createApiRef`/`updateApiRef` delegation |
| C2 | Async readiness | 🟩 | all create/update | Solved by `async` (requeue); wired on `Hpc`. Poll-path validation + `handleParam` **shipped** in 0.18.0 ([#46](https://github.com/krateo-platformops/oasgen-provider/issues/46)). Residual: open-string state enums |
| C6 | Delete-direction `*ApiRef` extras lack the spec | 🟩 | `compute/CloudServer` (any delegated delete) | **Shipped** in RDC 0.18.0 — spec forwarded on every direction ([rdc#41](https://github.com/krateo-platformops/rest-dynamic-controller/issues/41)) |
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

### A1 — `nullable: true` (🟩 SHIPPED in oasgen 0.52.0 — [#424](https://github.com/krateo-platformops/oasgen-provider/pull/424))
The Aruba specs are OAS **3.0.1**, which expresses "may be null" with
`nullable: true`. Through 0.41.0 oasgen-provider did not read the keyword at all —
no `Nullable` field on its domain schema and zero references in
`internal/tools/oas2jsonschema/` — so it was silently dropped during conversion and
the generated CRD declared the field non-nullable.

**0.52.0 carries it through**, and this is the reason the pin moved. Measured on this
repo's own `openapi/network-provider.json`, rendering the committed `Vpc`
RestDefinition at each tag:

| | `spec.properties` in the generated CRD |
|---|---|
| 0.41.0 | `type: object` — no `nullable` |
| 0.52.0 | `type: object`, **`nullable: true`** |

`Schema.Nullable` now exists in `internal/tools/oas2jsonschema/types.go`, documented as
carrying "OAS 3.0's `nullable: true` and Swagger 2.0's `x-nullable` into the generated
CRD"; `grep -rl Nullable internal/tools/oas2jsonschema/` matches 0 files at 0.41.0.
`minProperties` arrives in the same PR. What this buys in practice: a `null` the API
returns, or that a user sends explicitly for an optional object, now survives the
apiserver instead of being pruned to "unspecified" and then defaulted.

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
> workaround below (coercing to `additionalProperties: true`) has been **removed**.
>
> **Scope correction (measured on a live cluster).** The fix is real and correct,
> but for *this* API surface it changes nothing observable: all 42 of Aruba's typed
> maps live in **response/catalog DTOs**, and zero appear in any create request
> body — so none reaches a generated CRD spec (verified: 0 `additionalProperties`
> across all 68 generated CRDs). The value of the fix here is latent — it applies
> the moment a map appears in a request body. An earlier revision of this document
> claimed the maps "keep their value type in the generated CRDs"; that was
> overstated. Requires oasgen >= 0.18.0.
> ([oasgen-provider#45](https://github.com/krateo-platformops/oasgen-provider/issues/45))

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
> ([oasgen-provider#49](https://github.com/krateo-platformops/oasgen-provider/issues/49))

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

> Filed upstream: [oasgen-provider#49](https://github.com/krateo-platformops/oasgen-provider/issues/49).

---

## Category B — resource-shape gaps

### B1 — `metadata`-wrapped name & id / nested identifiers (🟩 solved — this is why the proxy existed)
Almost every Aruba resource nests its human name and server id inside a
`metadata` object: the **create** body is `{metadata:{name,location,tags}, properties:{…}}`
and the **read** body returns the id at `metadata.id`. The original blueprint
shipped a Go **`subnet-plugin`** whose *entire purpose* was to flatten this
`metadata` object, because "nested fields used as identifiers are not fully
supported" (quoted from the old plugin README).

oasgen-provider removes that need. Every metadata-wrapped resource in this
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

### B3 — `findby` list envelope (🟩 shipped in #110)
Every Aruba list endpoint returns `{total, self, prev, next, first, last, values:[…]}`
— the resources are under `values`, not at the top level.

**This section asked for a feature that already existed, and said so for longer than
it was true.** The ask below was filed as open and is now closed: `findby.itemsPath`
shipped in [#110](https://github.com/krateo-platformops/oasgen-provider/issues/110),
and it is present in the version this repo pins. Verified by reading the released
source at **0.41.0** — the pin when that check was made — and re-read at the current
pin **0.52.0**, where `getter.go:63` is unchanged. Not on main and not on report:
`ItemsPath string \`json:"itemsPath,omitempty"\`` at
`go/rest-dynamic-controller/internal/tools/definitiongetter/getter.go:63`, consumed by
`ExtractItemsFromResponse(body, itemsPath)` in
`go/rest-dynamic-controller/internal/tools/client/restclient.go`, whose branches are:

| response shape | result |
|---|---|
| bare JSON array | itself |
| `itemsPath` declared | that property — **authoritative**: resolving to a non-array, or being absent, is an error, not a reason to look elsewhere |
| exactly one array property | that one, unambiguously |
| two or more array properties | an error naming them in sorted order; never a guess |
| no array property | the object itself, as a list of one |

**Why this repo still declares no `itemsPath` anywhere.** All 68 list envelopes in
these specs carry exactly one array, so the single-array branch resolves `values`
with nothing declared — including the two variants that are not the common shape
(`metering.json` `StandardAlertResponse`/`StandardAlertReceivedResponse` add a
`metadata` key; `auditing.json` `AuditResponseFilter` has no `first`/`last`/`prev`).
The inference never looks at key *names*, only at how many properties are arrays, so
all three shapes resolve for the same reason.

**What was withdrawn, and what held.** The claim that there is "no field to declare
the items' location" is withdrawn — there is. The diagnosis underneath it held and was
the thing actually fixed: the old code ranged the map and took the first list, and Go
randomises map iteration order, so an envelope with two arrays searched a different
one on different reconciles. Losing that toss means `findby` reports not-found, which
the reconciler acts on by **creating a duplicate** — a coin flip, not a stable wrong
answer. #110 replaced it with the table above and sorts the names before building the
error, so the diagnostic is stable too.

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

### B5 — `findby` reads only the first page, and Aruba caps the page at 50 (🟥)

Every one of our 36 RestDefinitions declares **no** `pagination` block, because
there is nothing it could declare. That is not an omission; it is the only
option the two sides leave.

**Measured against the live API** on a 1175-item collection
(`Aruba.Audit/events`, 2026-11-06):

| request | HTTP | `total` | `len(values)` | `next` set |
|---|---|---|---|---|
| *(no params)* | 200 | 1175 | **50** | yes |
| `limit=1` | 200 | 1175 | 1 | yes |
| `limit=10` | 200 | 1175 | 10 | yes |
| `limit=50` | 200 | 1175 | 50 | yes |
| `limit=100` | 200 | 1175 | **50** | yes |
| `limit=500` | 200 | 1175 | **50** | yes |
| `limit=2000` | 200 | 1175 | **50** | yes |

So the default page is 50, `limit` is honoured **downward only**, and a larger
`limit` is silently ignored. There is no request that returns item 51 — you
cannot opt out of paging by asking for one big page.

The absences above are measured, not assumed: collections holding fewer than 50
objects report `total == len(values)`, so the probe demonstrably reports
agreement when it exists. Every other collection in the test account holds
**≤ 6** objects, which is exactly why no live run has ever shown this.

**Why no `pagination` block fits.** RDC implements two strategies,
`continuationToken` and `pageNumber`
(`go/rest-dynamic-controller/internal/tools/pagination/`). Aruba pages by
`offset`/`limit`:

* `continuationToken` re-sends a response value verbatim as a token. Aruba's
  signal is `next`/`first`/`last` as whole URLs, not a token.
* `pageNumber` advances by **one** (`page_number.go`, `p.page++`), so
  `pagePath: offset` would request offset 0, then 1, then 2 — overlapping pages,
  not successive ones.
* `offset` is commented out in
  `go/oasgen-provider/apis/restdefinitions/v1alpha1/types.go` as *"(Future) …
  deliberately unimplemented rather than undesigned: it is pageNumber with
  offsetPath/limitPath and an advance of limit rather than 1"*. That is exactly
  what Aruba needs.

**Why the `Indeterminate` guard does not save us.** RDC is careful here: a
pagination mechanism that is *declared but cannot be read* yields
`Indeterminate` rather than a verdict, and exhausting `maxPages` is
`Indeterminate` too — "I was told to stop looking" is deliberately not "it is
not there". That guard protects a **declared** mechanism, and we declare none.

The path we actually take never reaches it. In
`go/rest-dynamic-controller/internal/tools/client/restclient.go` the findby entry
point returns early before any paginator exists:

```go
if findByAction == nil || findByAction.Pagination == nil {
    return u.CallFindBySingle(ctx, cli, path, opts, itemsPath, findByAction)
}
```

`CallFindBySingle` issues **one** request, walks the items, and ends with:

```go
// If no match is found after checking all items, return a Not Found error.
return Response{}, &StatusError{StatusCode: http.StatusNotFound,
    Inner: fmt.Errorf("item not found"), Synthesised: true}
```

So with nothing declared the 404 is **unconditional after one page**, and nothing
in the call knows a page was truncated — there is no `PageResult` and therefore no
verdict to be `Indeterminate` about.

> **Correction.** This entry first cited `NewPaginator(nil)` returning
> `nil, nil` as the mechanism. That is dead code on this path: the early return
> above means `NewPaginator` is never called, and `restclient.go:458` is its only
> non-test caller, reached only with a non-nil config. The conclusion was right
> and the evidence was wrong, which is worse than it sounds — anyone checking the
> claim would have found an unreachable branch and had reason to doubt the rest.
> Caught by the github-provider-kog session trying to break the claim rather than
> confirm it. The published upstream issue carries the same correction.

Also distinct, and easy to conflate: `MaxFindByPages = 1000` is a hard ceiling on
a *paginated* walk and returns an **error**, not a 404. It is not `maxPages`.

**Consequence.** For a collection exceeding 50 items, `findby` cannot match an
object that sits past the first page. It reports not-found, and an unmatched
listing is then treated as absence — see
[oasgen-provider#485](https://github.com/krateo-platformops/oasgen-provider/issues/485),
which establishes that the normal Observe path tests `IsNotFoundError` and does
not distinguish a synthesised 404 from a vendor one. Not-found drives a
**create**. Nothing in Aruba's API corresponds to GitHub's 422 "already exists",
so there is no vendor-side backstop.

**Honest limits on this entry.** The API behaviour in the table is observed. The
RDC and oasgen code paths are read, not observed. **Nobody has watched a
reconciler create a duplicate** — the step from "observe reports absent" to "a
second resource exists at the vendor" is inferred. Treat the severity as
code-derived until someone sees it. Reproducing it needs 51+ objects in one
collection, which no current chain creates.

**Evolution:** implement the `offset` strategy already designed in that type
comment. For Aruba it also wants the page size to come from the declaration
rather than a request parameter, since the server ignores a `limit` above 50.

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
([oasgen-provider#46](https://github.com/krateo-platformops/oasgen-provider/issues/46))

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
> ([rdc#41](https://github.com/krateo-platformops/rest-dynamic-controller/issues/41))

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
introduced for any of the 36 resources. The only genuinely proxy-shaped needs
that remain (compute CloudServer multi-call create/lifecycle) are covered by the
fork's Snowplow `*ApiRef` delegation rather than a bespoke web service.


---

## Appendix — the 0.41.0 → 0.52.0 pin move, and the evidence behind it

**The move was made. 0.53.0 exists and was declined, for a measured reason.**

Everything below comes from one probe run against this repo's own vendored documents
and its 36 committed RestDefinitions, at three tags, with no overrides. The probe and
its positive control are reproduced at the end so the numbers can be re-taken.

### Why move off 0.41.0

[#424](https://github.com/krateo-platformops/oasgen-provider/pull/424) carries OAS 3.0
`nullable: true` and `minProperties` into the generated CRD — see section **A1** above, which this
release closes. Three more fixes land in the same span and bear directly on this repo:

* [#421](https://github.com/krateo-platformops/oasgen-provider/pull/421) — a
  `fieldMapping` no longer deletes the spec property it reads from. Every `get`,
  `update` and `delete` in `restdefinitions/` uses `requestFieldMapping`, so this is
  the whole fleet.
* [#456](https://github.com/krateo-platformops/oasgen-provider/pull/456) — an
  `excludedSpecFields` entry that matches nothing is now reported instead of ignored.
* [#428](https://github.com/krateo-platformops/oasgen-provider/pull/428),
  [#431](https://github.com/krateo-platformops/oasgen-provider/pull/431),
  [#437](https://github.com/krateo-platformops/oasgen-provider/pull/437).

### Why NOT 0.53.0

0.53.0 bumps `krateo-platformops/plumbing` **v1.14.2 → v1.27.0**. 0.41.0 and 0.52.0 are
both on v1.14.2, so this is the first pin that would change `crdgen` behaviour.

v1.27.0's `crdgen/nesteddefault.go` stamps `default: {}` on any property that is not
required by its parent, is `type: object`, has no default of its own, and has a
reachable defaulted descendant. oasgen 0.53.0 does not opt out: `NoParentDefaults`
appears nowhere in the tag, and the `crdgen.Options` literal at
`internal/tools/render/render.go:180` omits it, so the zero value applies and stamping
is on. 0.53.0 does wire the stamped paths into a warning — mitigation, not prevention.

Our shape is `allOf: [$ref VpcPropertiesDto]` + `nullable: true` + `description`, not an
inline `type: object`, so whether it is even eligible was an open question. It is:

| rendered `Vpc` | plumbing | `spec.properties` own `default` |
|---|---|---|
| 0.41.0 | v1.14.2 | absent |
| **0.52.0** | **v1.14.2** | **absent** |
| 0.53.0 | v1.27.0 | **`{}`** |

The `false` rows are measurements, not blind zeroes: the same probe carries a positive
control on upstream's own `render/testdata/parentdefault/stamped.yaml` fixture, which
stamps at 0.53.0 and does not at 0.52.0 — the instrument demonstrably can report a
stamp, and reports none at the pin.

Fleet-wide, the same three tags across all 36 definitions:

| generation warnings | 0.41.0 | **0.52.0** | 0.53.0 |
|---|---|---|---|
| stamped-parent `default: {}` | 0 | **0** | **240** |
| `excludedSpecFields: id` matches nothing (#456) | 0 | 36 | 36 |
| identifier absent from findby items | 0 | 16 | 16 |
| other (pre-existing) | 2 | 2 | 2 |
| **render failures** | **0** | **0** | **0** |

240 stamped paths is the cost of 0.53.0 stated plainly: 240 places where the apiserver
would store defaulted values into every object that omits the parent. It matters at
exactly one of them. `VpcPropertiesDto.default` carries `"default": true` and its own
description says "Only one default vpc for region is admissible" — so on 0.53.0 a `Vpc`
that omits `spec.properties` claims that region-unique role silently. Across all 13
vendored documents there are exactly **three** real `default` keywords, and the other
two are `false`; the remaining 237 stamped paths are inert today and are a standing
invitation for a future vendored document to make one of them live.

What 0.53.0 is forgoing by not being the pin, so the trade is on the record:
[#475](https://github.com/krateo-platformops/oasgen-provider/pull/475), a BREAKING fix
that makes the auth-secret RBAC actually scope credential access — security-relevant
here — plus [#460](https://github.com/krateo-platformops/oasgen-provider/pull/460)
(re-render a CRD when its generator was upgraded),
[#472](https://github.com/krateo-platformops/oasgen-provider/pull/472) and
[#474](https://github.com/krateo-platformops/oasgen-provider/pull/474). The sequence
that gets both: land the guards in this repo first (`required` on the chart's resource
property objects, and samples that spell every property out — both done), then 0.53.0's
stamping is harmless here and its RBAC fix is worth taking.

### Live verification of the pin (2026-11-07)

CI proves install, CRD generation and 36/36 RestDefinitions reaching Ready at 0.52.0
on a clean runner. It cannot reach Aruba — no credentials — so a live reconcile was
the one claim the pin rested on by inference. `scripts/pin_verify.sh` was run against
the real API on **released 0.52.0 images with no overrides** (`oasgen-provider:0.52.0`,
`rest-dynamic-controller:0.52.0`, verified in the log):

```
RESULT: 4 resources created and observed
  drift Vpc/ga-ns-vpc:          ok
  drift Subnet/ga-ns-subnet:    ok
  drift SecurityGroup/ga-ns-sg: SKIPPED
  drift SecurityRule/ga-ns-sr:  ok
```

Create and observe: demonstrated for all four. Drift correction: demonstrated for
three. **Residue: clean** — the ground-truth check found zero leftovers, so nothing
was left running at Aruba.

Two caveats, named rather than buried, because the run's own exit code was 1:

**1. SecurityGroup drift could not be INJECTED.** The chain reported `PUT returned 400
for every body shape tried (declared spec, response echo) — Status: invalid status`
with the resource `Active`. This is not the controller failing to correct drift; the
harness could not create a divergence to correct. The OAS says it should be possible —
`SecurityGroupUpdateDto` is `{metadata:{name,location,tags}, properties:{default}}`, so
`tags` is non-identifying, the same field that worked on Vpc and Subnet in this very
run. So this is a live-versus-OAS divergence and **`docs/coverage.md`'s claim that
SecurityGroup has "full lifecycle live incl. drift correction, re-runnable on demand
under `tests/ga/chains/network-security.yaml`" overstates what that chain demonstrates
today.** That row is pending a targeted PUT probe rather than being rewritten from one
run; note also that `coverage.md` attributes SecurityGroup's original confirmation to
the *cloudserver* chain, not to the chain it cites as the reproduction.

**2. Two deletes completed OUTSIDE the confirmation window.** `SecurityRule` and
`Subnet` still read 200 during their own delete and were 404 at the residue check
minutes later. The chain refuses to attribute a deletion it did not witness, which is
why it exits 1 — the right strictness, and a different thing from a failed delete. Both
resources are gone.

**Weather, not findings.** The run logged 23 transport timeouts and one 401 (recovered
by re-reading the ESO Secret), and `SecurityRule` took 340s to reach Active. None of
these is attributable: the per-resource state lines print only on CHANGE, so the log
shows the failures and hides the successes, and no denominator existed. That gap is now
closed — `ga_chain.py` prints a transport census, so the next run can say whether 23 is
5.8% or 63.6%. Until then the timeouts are an observation and nothing has been reported
to Aruba on their strength.

**The samples were checked against these CRDs too**, captured from the cluster before
teardown rather than rendered locally: 8 CRDs for the 4 installed kinds, and
`scripts/check_samples.py` found no type, unknown-field or missing-required mismatch.

## Appendix — correction: the `offset` footgun is real, its consequence is NOT proven

PR #41 removed `offset`, `limit`, `filter`, `sort` and `projection` from
`configurationFields` and excluded them from the spec. **That change is still right.**
This corrects the consequence its description claimed.

### What is measured and stands

```
GET .../vpcs             -> 200  total=1  len(values)=1
GET .../vpcs?offset=1    -> 200  total=1  len(values)=0
GET .../vpcs?offset=5    -> 200  total=1  len(values)=0
```

A Configuration-settable `offset` can make the findby response carry **zero items while
`total` says otherwise**, with the contradiction sitting unread in the same envelope.
That is a real footgun on a field no user has a legitimate reason to set, and removing it
needs no further justification.

### What was claimed and is NOT proven

PR #41 said this produces "a duplicate external resource per reconcile". **That has not
been observed, and the one attempt to observe it was invalid.**

The experiment set `offset=1` against a collection that by then held **two** vpcs — the
account's standing one plus the test resource — so `offset=1` skipped one and returned
the other, possibly the target. The `offset=1 -> 0 items` measurement above was taken on
a **one**-item collection and did not transfer. The run reported no duplicate in eight
minutes, with the CR at `Ready=True`, `Synced=True`, `Observed=True(ResponseObtained)`.

Worse than the bad offset: the script printed a list of candidate explanations for the
null result and **that list did not include the confound it had just fallen into.** A
hedge that enumerates the wrong candidates is not much better than no hedge.

### The candidate that would narrow the claim substantially

`Observed=True(ResponseObtained)` says observe ran and concluded the resource exists. One
explanation is that once a CR carries an id in status, `IsResourceKnown` returns true and
observe uses **get**, never reaching findby — exactly the branch in RDC's `exists.go`:

```go
action := apiaction.Get
if !isKnown {
    // Not addressable by identifier yet ... findby is the only way to answer
    action = apiaction.FindBy
}
```

If that is what happened, a blinded findby is irrelevant to an **established** resource,
and the hazard needs a resource that exists while its CR carries no recorded id — a fresh
CR adopting an existing resource, or a CR whose status was lost. That is far narrower
than "every reconcile", and it is the same case the `github-provider-kog` session raised
against oasgen#485's design.

### Status

The removal stays. The consequence is recorded as **unproven**, the re-run uses an offset
derived from the collection size so it cannot be undermined by the test adding an item,
and nothing upstream was filed on the duplicate-create claim.

---

## Appendix — 0.54.0 is UNUSABLE here, and 0.53.0 remains skipped (2026-11-07)

**Do not pin 0.54.0.** The render side is fixed and the reconcile side is broken.

### What 0.54.0 got right

0.54.0 sets `crdgen`'s `NoParentDefaults` at both call sites in `render.go`, which
resolves the stamping that kept us off 0.53.0. Measured with the same census against
the same 13 documents:

| | resource CRD | Configuration CRD | total |
|---|---|---|---|
| 0.53.0 | 2 across 2 kinds | **238 across 36 kinds** | 240 |
| 0.54.0 | 0 | 0 | **0** |

The zero is measured, not assumed — the identical instrument reports 240 at 0.53.0.
And rendering all 72 CRDs with the **same** definitions at 0.52.0 and 0.54.0: the only
difference is `status.observedGeneration` added to the 36 resource CRDs, with all 36
Configuration CRDs **byte-identical**, nothing removed and no value changed.

Worth recording that 238 of the 240 were on the **Configuration** CRD and only 2 on the
resource CRDs, and that exactly **one** of the 240 was ever a hazard
(`VpcPropertiesDto.default = true`). "240 stamped paths" with no qualifier reads as 240
hazards; it was not.

### Why it is unusable anyway

On a kind cluster running released 0.54.0 images with no overrides, **no managed
resource reaches Ready**:

```
Vpc/ga-ns-vpc  Ready=Unknown(ObserveFailed):
  observe failed: secrets "arubacloud-token" is forbidden:
  User "system:serviceaccount:krateo-system:vpcs-arubacloud.ogen.krateo.io-v1alpha1"
  cannot get resource "secrets" in API group "" in the namespace "default"
```

```
can-i get secrets -n default         -> no
can-i get secrets -n krateo-system   -> no      <- not a namespace-scoping problem
can-i get vpcs (its own CR)          -> yes     <- the ClusterRole is fine
```

There is **no namespace** in which that ServiceAccount can read a Secret, so moving the
Secret is not a workaround.

**The cause** (established by the oasgen-provider maintainer, recorded here with that
attribution rather than as our finding): `deploy/authsecretrbac.go` collects auth-secret
refs from three paths — `basic.usernameRef`, `basic.passwordRef`, `bearer.tokenRef` —
while the generator produces a fourth shape, `apiKey.tokenRef`. oasgen#475 removed the
blanket `resources: ["secrets"]` ClusterRole grant that had been masking the gap. Filed
as [oasgen-provider#500](https://github.com/krateo-platformops/oasgen-provider/issues/500)
as the measurement, with the cause added by the maintainer.

**Our exposure, measured:** 25 of the 36 kinds use `apiKey` and 11 use `bearer`.

```
apiKey (25): AlertRule Backup BlockStorage ElasticIp Folder Hpc Image Kaas KaasBackup
             Key Kmip Kms LoadBalancer Project Registry Restore SecurityGroup
             SecurityRule Snapshot Subnet Vpc VpcPeering VpcPeeringRoute VpnRoute
             VpnTunnel
bearer (11): AiKey BackupPolicy BackupPolicyAssignment CloudServer Database
             DatabaseBackup DatabaseUser Dbaas Grant KeyPair ScheduledJob
```

Whether the 11 bearer kinds still work at 0.54.0 is **not measured** — the run failed on
the first apiKey kind and never reached a bearer one.

### An error of ours worth keeping

A first reading of the cluster found a Role named for the resource with `rules: null` in
`krateo-system` and reported that as the cause. **That was wrong**: it is an unrelated
placeholder rendered by `deploy.go` into the RestDefinition's namespace. The facts that
held were the `can-i` results; the explanation was attached to the first mechanism that
fit the shape of the evidence.

### Why CI could not catch it, which is the reusable part

The 0.54.0 run went **green** on every check:

```
Every RestDefinition reaches Ready        <- the RestDefinition, i.e. CRD generation
Each Configuration CRD exposes EXACTLY its document's auth method   <- the CRD's shape
Samples are accepted by the generated CRDs   <- kubectl dry-run, no reconcile
Composition charts render against the generated CRDs
Provider logged no errors
```

**None of those reconciles a managed resource, and the smoke job never creates an
`arubacloud-token` Secret at all.** So the Secret-read path is structurally
unexercised, and the suite passed for reasons unrelated to what changed — the same shape
as a golden file that stays byte-identical because no fixture carries the shape under
test. A green run here licenses "it installs and generates", never "it reconciles".

The conclusion drawn from that green run at the time — "#475 does not break a
36-definition consumer at install, generation, readiness or auth" — was **over-scoped**.
The four steps were true; what they licensed was not.

Only a live reconcile against the vendor found this, and it was kept as the gate for the
unpin rather than CI being accepted in its place. That decision is the whole reason the
pin did not move.

### State

**The pin stays 0.52.0**, which is live-verified end to end. 0.53.0 stays skipped for the
stamping. 0.54.0 is skipped for this. 0.54.1 is expected with the collector fixed, and
will be measured the same way: census, 72-CRD diff, then a live reconcile — with the live
reconcile understood as the step that actually decides.

---

### A warning the bump adds that is WRONG, and should not be acted on

16 of the 36 definitions acquire this at 0.52.0:

```
spec.resource.identifiers: identifier "metadata.name" is absent from the items the
findby response carries ... findby can never match any item regardless of policy: it
will report not-found and the reconciler acts on not-found by creating
```

It predicts a duplicate external resource per reconcile. It is a false positive, and the
cause is `allOf`. `VpcResponseDto.metadata` is `allOf: [$ref ...]`, and that definition
carries all 17 fields including `name`; the checker's `lookupTypeByPath`
(`internal/tools/oas2jsonschema/spec_builder.go`) walks `Schema.Properties` only, and
the domain `Schema` keeps `AllOf` as a separate field rather than merging it. Measured
directly against our document at 0.52.0:

```
item.Properties      = [metadata status properties]
metadata.Properties  = []                      <-- what lookupTypeByPath searches
metadata.AllOf len   = 1                       <-- what it does not search
metadata.AllOf[0]    = [id uri name location project tags category ...]
lookupTypeByPath(metadata.name) = false
```

The affected kinds: AlertRule, Backup, BlockStorage, ElasticIp, Image, LoadBalancer,
Restore, SecurityGroup, SecurityRule, Snapshot, Subnet, Vpc, VpcPeering,
VpcPeeringRoute, VpnRoute, VpnTunnel. All 16 have verified create/observe/drift/delete
in [coverage](coverage.md), which is what a "findby can never match" prediction has to
be reconciled against. **Do not remove an identifier on the strength of this warning.**

The 36 `excludedSpecFields: id` warnings, by contrast, are CORRECT: `id` is genuinely
absent from those create bodies, which is the same inertness recorded against the
cleanup-ordering constraint. They are still not a reason to delete the entries without
checking the floor that made them necessary.

### Not yet verified in a cluster

`scripts/pin_verify.sh` has NOT been run at 0.52.0. Everything above is CRD generation —
it says the 36 definitions render, and what they render — and nothing about a live
reconcile at this tag. The free `network-security` chain on a kind cluster is what would
close that, and it needs the credential source cluster up.

### Reproducing the numbers

The probes live outside this repo because they are Go tests against a clone of
oasgen-provider, run per tag:

* render the committed `Vpc`/`SecurityGroup` definitions against
  `openapi/network-provider.json` and report whether `spec.properties` carries its own
  `default`, with a positive control on upstream's `parentdefault/stamped.yaml`;
* render all 36 definitions from `restdefinitions/` and tally `GenerationWarnings` by
  code — untyped warnings, which is what crdgen's stamping notices are, must be counted
  under their own bucket or the 240 disappears.

## Appendix — the 0.27.1 → 0.34.0 pin move, and the evidence behind it

**The move was made, and the pin has since gone further.** 0.34.0 is where the
Kubernetes floor rose from 1.33 to 1.36 — a real cost passed to users, taken for the
two fixes below — and that is why this appendix is about 0.34.0. The pin is now
**0.36.0**, which costs nothing more in cluster version (the floor is unchanged
through 0.37.0) and is required for `profile/AiKey`: 0.35.0 to parse `profile.json`
at all, 0.36.0 for oasgen#236. See [release](release.md#compatibility).

What follows is the evidence the 0.34.0 decision rested on, kept because the
reasoning is what makes it reviewable.

We used to pin **0.27.1**. Two fixes we depend on land above it:
[#225](https://github.com/krateo-platformops/oasgen-provider/pull/225) (the
`resourceHash` gate, released in 0.33.0), which is what makes the
`status.error` field appended on 2026-10-27 actually reach CRDs that already
exist, and [#227](https://github.com/krateo-platformops/oasgen-provider/pull/227)
(per-verb `oasPath` reaching the controller — the #220 that keeps CloudServer's
create unreachable), heading for 0.34.0.

Moving the pin means deciding whether the 27 GA resources need re-verifying.
Most of the span is fixes, but oasgen-provider flagged two changes with real
behavioural surface:

1. **The built-in collision guard (0.32.0)** — already paid for. `ScheduledJob`
   exists because of it; see `KIND_MAP` in `scripts/generate_restdefinitions.py`.
2. **Path-item parameters becoming visible (0.32.0)** — this one *changes
   generated CRDs*, but only for documents that declare `parameters` on the
   **path item** rather than on the operation.

**Point 2 is measured, not reasoned about, and the answer is zero.** Across
every vendored document, counting `paths./x.parameters` (path item) against
`paths./x.get.parameters` (operation):

| | path items declaring `parameters` | operations declaring `parameters` |
|---|---|---|
| All 12 generated-from documents | **0** | 220 |
| `auditing.json` (vendored, not consumed) | 0 | — |

Aruba declares parameters on the operation, every time, in all 13 documents. For
comparison, oasgen's own 0.32.0 notes cite Keycloak declaring them on 273 of 274
path items. So **the path-item change cannot alter our generated CRDs** — not
because the change is small, but because our inputs never use the construct.

So of the two flagged changes, one is **ruled out by evidence** (path-item
parameters — our documents don't use the construct) and the other is **already
paid for** (the collision guard). On today's information the span therefore
carries **no unbounded CRD-shape risk** for the 36 resources, with exactly one
deliberate exception: #225's one-time regeneration — which is the change we
*want*, since it is what makes the `status.error` append take effect on CRDs
that already exist.

That is a much smaller thing to weigh than "27 GA resources may need
re-verifying", which is where this started.

It does **not** amount to "nothing changes", and the two must not be run
together. Zero path items removes a *known* risk; it does not demonstrate the
regeneration is a no-op. That needs
`oasgen-render` run at both versions and compared, with the output captured so
the comparison is demonstrably performed rather than inferred from an empty
`git diff`. `openapi/profile.json` is the one to look at first — vendored
2026-10-27, it has never been through generation on any version, so a diff
attributes nothing about it to the upgrade; generate it at both to separate
"this document is new" from "this version changed it".

### What upgrading actually does to existing CRDs

oasgen-provider's 0.33.0 release note (amended in their #235 — point at the section
on `main`, not the originally released text, which omitted this) records the
behaviour we depend on:

* appending to `additionalStatusFields` was accepted and **silently did nothing**
  before #225, and `includedSpecFields` the same — which is why our
  `status.error` append needs the pin to move to take effect;
* on upgrade, expect a **one-time CRD regeneration per RestDefinition**. It is
  expected, it happens once, and it is the thing that finally applies spec changes
  previously accepted and dropped;
* an appended entry now reaches an **existing** CRD: `resourceHash` covers the whole
  of `spec.resource`, so the append changes it, `Observe` reports drift and `Update`
  regenerates. No manual step and no delete-and-recreate — which matters, because
  delete-and-recreate was previously the only route and it destroys the CRD and every
  CR of that kind.

One operational caveat worth repeating to anyone doing the upgrade: the regeneration
happens on the **next reconcile**, not at apply time. On the default 3-minute poll, an
operator who applies and immediately inspects the CRD sees the old schema and may
conclude it is stuck. It is not.

Reproduce the table with:

```sh
python3 - <<'EOF'
import json, os, sys
sys.path.insert(0, "scripts")
from providers import CONFIGMAP_PROVIDERS
VERBS = {"get","put","post","delete","patch","head","options","trace"}
for prov, fn in sorted(CONFIGMAP_PROVIDERS.items(), key=lambda kv: kv[1]):
    d = json.load(open(os.path.join("openapi", fn)))
    pi = sum(1 for _, i in (d.get("paths") or {}).items()
             if isinstance(i, dict) and i.get("parameters"))
    op = sum(1 for _, i in (d.get("paths") or {}).items()
             for v, o in i.items()
             if v in VERBS and isinstance(o, dict) and o.get("parameters"))
    print(f"{fn:<32} path-item={pi:<4} operation={op}")
EOF
```
