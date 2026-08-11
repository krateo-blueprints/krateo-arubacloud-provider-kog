# Adversarial review — generated artifacts vs. the code that executes them

This review deliberately attacked this repository's generated RestDefinitions,
RESTActions and claims against the **actual executor source**: the krateo
forks of `krateo-rest-dynamic-controller` (RDC), `krateo-oasgen-provider` and
`plumbing` (crdgen). Rationale: the fork's own history proves doc-comments can
lie — `requestTransform` was schema-accepted and executed by **nothing** for a
full release before being rejected at admission and finally implemented in
RDC 0.17.0.

Every claim below cites the file that proves it. Confirmed breaks were **fixed
in the same change** that added this document.

> **Update (oasgen/RDC 0.18.0).** Findings **#1** and **#2** were filed upstream
> and have since **shipped as first-class features** — `async.poll.handleParam`
> + admission-time poll-path validation ([oasgen#46](https://github.com/krateo-blueprints/krateo-oasgen-provider/issues/46)),
> and spec forwarding on every `*ApiRef` direction ([rdc#41](https://github.com/krateo-blueprints/krateo-rest-dynamic-controller/issues/41)).
> The local workarounds they forced have been **removed** from this repo; the
> rows below keep the original evidence and record what replaced them.
> Finding #3 (snowplow wiring) is unchanged and remains an install prerequisite.

## Verdict table

| # | Suspected failure | Verdict | Evidence | Action taken |
|---|-------------------|---------|----------|--------------|
| 1 | Async poll path `{operationId}` vs OAS `{id}` | 🟥 **CONFIRMED — HPC async could never poll** | RDC `restclient.go:53` resolves the poll path by **exact string** lookup (`PathItems.Get(path)`); `async_handler.go:120` binds `params["operationId"]` literally | ~~`patch_oas.py` renames the OAS param~~ → **superseded**: oasgen 0.18.0 added `poll.handleParam`, so the RD declares `path: …/monitor/{id}` + `handleParam: id` and Aruba's document is used **unmodified**; oasgen validates both halves at admission. The rename patch is deleted |
| 2 | Delete RESTAction reads `.spec.*` | 🟥 **CONFIRMED — delete deadlock** | RDC `observe_restaction.go:108` `buildExtras`: spec is forwarded **only** for create/update; delete gets static extras + name/namespace/uid + identifiers keyed by path string (`.["metadata.name"]`) | ~~Rewritten to `.["metadata.name"]` + static `projectId` extra~~ → **superseded**: RDC 0.18.0 forwards the spec on every direction, so the delete RESTAction reads `.spec.projectId` like its siblings and the static extra (which pinned one RD to one project) is deleted. Evolution item §C6 closed |
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

1. ~~**Implicit runtime contracts that nothing validates.**~~ **Largely closed by
   0.18.0.** The two contracts this review found — the poll path's exact-key +
   parameter-name pairing (#1) and the delete-direction spec gap (#2) — are now
   respectively *validated at admission with a declarative `handleParam` escape
   hatch*, and *eliminated by forwarding the spec on every direction*. What the
   episode leaves behind is a **class** of risk rather than these two instances:
   config that is schema-accepted but semantically unexecutable still generally
   fails at runtime, and the remaining `verbsDescription[].path` values get no
   admission check.
2. **Version skew is now the sharpest edge.** Because the fixes are split across
   two components, a RestDefinition written for 0.18.0 is *accepted* by an older
   RDC that then silently misbehaves — `handleParam` is ignored (poll binds to
   `operationId` and never resolves) and delete receives no spec (finalizer
   deadlock). The chart pins RDC independently of oasgen, so this is reachable by
   default; see [Version prerequisites](../README.md#prerequisites).
3. **The API's shape itself resists contract-driven generation.** `status.state`
   is an open string by upstream design ([async-readiness](async-readiness.md)),
   so readiness values are hand-supplied; lifecycle is spread across POST action
   endpoints (§C1); the list envelope is only handled by a first-array heuristic
   (#7). Each needs either an oasgen evolution (§B3 `itemsPath`, §C1 action
   verbs) or per-resource domain knowledge that no generator can derive.
4. **Deployment wiring.** Delegation requires a snowplow deployment plus
   `URL_SNOWPLOW`/`URL_AUTHN` on every generated RDC (#3) — currently manual.
5. **Spec fidelity gaps** (§A1, §A3–A7) still force pre-patching the OAS: ~4119
   `nullable` strips, security-scheme rewrites, and the compute v1.1 merge —
   though the typed-map coercion and the async param rename are **gone**. The
   patch script *is* the measure of the remaining distance between "the published
   Aruba contract" and "what KOG can consume", and that distance just shrank.

## Method note

Everything above was read from the krateo forks only (`krateo-oasgen-provider`
`main`@`70168ea`, `krateo-rest-dynamic-controller` `main`@`4a8ac8c` + tags
0.15.0/0.16.0/0.16.1/0.17.0, `plumbing` v1.12.0). RESTAction jq programs and the
end-to-end async flow remain unexecuted against a live cluster — that caveat
stands.
