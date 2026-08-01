# Adversarial review — generated artifacts vs. the code that executes them

This review deliberately attacked this repository's generated RestDefinitions,
RESTActions and claims against the **actual executor source**: the braghettos
forks of `krateo-rest-dynamic-controller` (RDC), `krateo-oasgen-provider` and
`plumbing` (crdgen). Rationale: the fork's own history proves doc-comments can
lie — `requestTransform` was schema-accepted and executed by **nothing** for a
full release before being rejected at admission and finally implemented in
RDC 0.17.0.

Every claim below cites the file that proves it. Confirmed breaks were **fixed
in the same change** that added this document.

## Verdict table

| # | Suspected failure | Verdict | Evidence | Action taken |
|---|-------------------|---------|----------|--------------|
| 1 | Async poll path `{operationId}` vs OAS `{id}` | 🟥 **CONFIRMED — HPC async could never poll** | RDC `restclient.go:53` resolves the poll path by **exact string** lookup (`PathItems.Get(path)`); `async_handler.go:120` binds `params["operationId"]` literally | `patch_oas.py` now renames the baremetal monitor param `{id}`→`{operationId}` in the OAS itself; `validate.py` enforces verbatim path + token |
| 2 | Delete RESTAction reads `.spec.*` | 🟥 **CONFIRMED — delete deadlock** | RDC `observe_restaction.go:108` `buildExtras`: spec is forwarded **only** for create/update; delete gets static extras + name/namespace/uid + identifiers keyed by path string (`.["metadata.name"]`) | Delete RESTAction rewritten to use `.["metadata.name"]` + static `projectId` extra; generator emits per-verb `deleteApiRef.extras`; new evolution item §C6 |
| 3 | `*ApiRef` unusable on a stock chart install | 🟧 **CONFIRMED — config gap** | RDC `main.go`: `-snowplow-url` defaults to `URL_SNOWPLOW` env, **empty = disabled** → `mutate_restaction.go` hard-errors; the chart's RDC deployment/configmap templates set **neither** `URL_SNOWPLOW` nor `URL_AUTHN` | Documented as an install prerequisite in [lifecycle-beyond-crud](lifecycle-beyond-crud.md) and [troubleshooting](troubleshooting.md) |
| 4 | Own validator masks break #1 | 🟥 **CONFIRMED — false-negative by design** | `validate.py` normalised `{param}` names away, "passing" a path RDC would never find | Check rewritten to the verbatim contract |
| 5 | "Fork is at v0.9.0" claim | 🟥 **CONFIRMED — wrong** | `git ls-remote --tags \| tail` sorts **lexically**; version-sorted, both forks are at **0.17.0** | README/docs corrected |
| 6 | oasgen rejects the async RD at admission | 🟩 Acquitted (worse: it doesn't) | No poll-path validation exists in the oasgen restdefinition controller — a broken poll path is **accepted** and fails only at runtime | Noted in §C2 as a missing-guardrail evolution item |
| 7 | findby list envelope `{total, values[]}` unhandled | 🟩 **Acquitted — works, by heuristic** | RDC `restclient.go:537` `ExtractItemsFromResponse`: returns the **first array-valued key** of the object. Aruba's envelope has exactly one array (`values`), so it works; a second array field would make it nondeterministic (Go map order) | §B3 reframed from "assumption" to "verified heuristic"; explicit `itemsPath` still the right evolution |
| 8 | Nested identifiers (`metadata.name`) unsupported in matching | 🟩 **Acquitted — fully supported** | RDC `restclient.go:572` `isItemMatch` → `pathparsing.ParsePath` → `NestedFieldNoCopy(item, "metadata","name")`, compared via `isInResource` (spec, then status) | None needed — the core no-proxy design is sound |
| 9 | compute v1.0/v1.1 schema merge corrupts create shapes | 🟩 **Acquitted — provably lossless** | 67 shared schema names between the two documents, **0 differing definitions** | Noted in `patch_oas.py` header |
| 10 | `Grant` identifier `user` absent from responses → recreate loop | 🟩 **Acquitted** | `GET .../grants/{username}` 200 props: `[user, database, role, creationDate, createdBy]` | None |
| 11 | async/`*ApiRef` not implemented in shipped RDC versions | 🟩 **Acquitted** | `async_handler.go` + `mutate_restaction.go` present since tag **0.15.0**; only `requestTransform` execution is 0.17.0-only (`ApplyRequestTransform` hits: 0.16.1→0, 0.17.0→3). Chart ships RDC 0.16.1 → everything this repo uses works; `requestTransform` (unused here) would not | Version matrix added below |

## Feature → minimum-version matrix (verified per tag)

| Feature used by this repo | RDC version | In chart-shipped 0.16.1? |
|---------------------------|-------------|--------------------------|
| Nested identifiers, `requestFieldMapping` | ≤0.15.0 | ✅ |
| `async` (requeue, operationRef, poll) | ≤0.15.0 | ✅ |
| `createApiRef`/`updateApiRef`/`deleteApiRef` | ≤0.15.0 | ✅ (needs `URL_SNOWPLOW` set) |
| `requestTransform` execution | **0.17.0** | ❌ (not used by this repo) |

## Why there is *still* an issue implementing KOG for the Aruba APIs

The nested-`metadata` problem — the reason the original subnet proxy existed — is
genuinely solved (#8). What remains, ranked by how fundamental it is:

1. **Implicit runtime contracts that nothing validates.** The async poll path
   must exist *verbatim* in the OAS with a param literally named `{operationId}`
   (#1), and delete-direction `*ApiRef` extras silently lack the spec (#2). Both
   are accepted by oasgen at admission (#6) and fail only at runtime — the same
   failure class as the historical `requestTransform` trap. **Evolution ask:**
   admission-time validation of `async.poll.path` against the OAS, and either
   spec-forwarding on delete or a declarative way to project spec fields into
   delete extras (§C6).
2. **The API's shape itself resists contract-driven generation.** `status.state`
   is an open string by upstream design ([async-readiness](async-readiness.md)),
   so readiness values are hand-supplied; lifecycle is spread across POST action
   endpoints (§C1); the list envelope is only handled by a first-array heuristic
   (#7). Each needs either an oasgen evolution (§B3 `itemsPath`, §C1 action
   verbs) or per-resource domain knowledge that no generator can derive.
3. **Deployment wiring.** Delegation requires a snowplow deployment plus
   `URL_SNOWPLOW`/`URL_AUTHN` on every generated RDC (#3) — currently manual.
4. **Spec fidelity gaps** (§A1–A7) still force pre-patching the OAS: 4117
   `nullable` strips, 42 typed-map coercions, security-scheme rewrites, and now
   one async param rename. The patch script *is* the measure of the remaining
   distance between "the published Aruba contract" and "what KOG can consume".

## Method note

Everything above was read from the braghettos forks only (`krateo-oasgen-provider`
`main`@`70168ea`, `krateo-rest-dynamic-controller` `main`@`4a8ac8c` + tags
0.15.0/0.16.0/0.16.1/0.17.0, `plumbing` v1.12.0). RESTAction jq programs and the
end-to-end async flow remain unexecuted against a live cluster — that caveat
stands.
