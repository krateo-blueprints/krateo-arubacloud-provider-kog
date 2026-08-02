# OAS policy — the specs are **not** modified

**This repository performs no OpenAPI rewriting.** The documents under
`openapi/` are Aruba's published specifications, byte-for-byte:

```
https://api.arubacloud.com/openapi/<provider>.json
```

KOG adapts to the published contract, not the other way round. A vendor's API
description is the authority; a generator that requires it to be edited before it
can be consumed has moved its own limitations onto the user.

> This page previously documented a `scripts/patch_oas.py` that made ~4,150
> edits. That script is **deleted**. The history of why each transformation
> existed and how each was retired is kept below, because it is the clearest
> measure of how far the tooling has come.

## Enforcement

`openapi/CHECKSUMS.txt` records the sha256 of every vendored document, and
`scripts/validate.py` recomputes them on every run. Any drift — including a
well-meaning "fix" — fails validation:

```
openapi/metering.json: MODIFIED (sha256 differs from CHECKSUMS.txt)
```

To refresh from upstream, replace the files and regenerate the manifest:

```sh
curl -sO https://api.arubacloud.com/openapi/network-provider.json   # etc.
(cd openapi && shasum -a 256 *.json | sort -k2 > CHECKSUMS.txt)
```

## What this costs today: authentication on 7 of 10 providers

Consuming the specs unmodified surfaces exactly one unresolved tooling gap.
oasgen supports only `type: http` (`bearer`/`basic`) security schemes; an
`apiKey` scheme is **silently skipped**, so the generated `<Kind>Configuration`
CRD has no `authentication` block and there is no way to supply a token.

| Aruba spec | Declared scheme | Status |
|---|---|---|
| compute, database, schedule | `type: http, scheme: bearer` | ✅ authenticates |
| network, container, security, storage, baremetal, project, metering | `type: apiKey, in: header, name: Authorization` | ❌ **24 of 34 resources cannot authenticate** |

Filed as [oasgen-provider#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49);
tracked here as §A7. Until it lands, only the three `http`/`bearer` providers are
functional end to end. That is a deliberate trade: a temporary, *visible*
limitation in the tool beats a permanent, invisible edit to the vendor contract.

Note that Aruba is inconsistent with itself here — 3 of its 11 specs already use
the correct `http`/`bearer` form — so this is also legitimate feedback to them.
But a spec being imperfect is not licence for KOG to rewrite it.

## History — every transformation that used to exist, and why it went

| Former transformation | Count | Why it is gone |
|---|------:|---|
| coerce `additionalProperties: {schema}` → `true` | 42 | oasgen 0.18.0 emits a **typed map** ([#45](https://github.com/braghettos/krateo-oasgen-provider/issues/45)) |
| rename baremetal monitor param `{id}` → `{operationId}` | 1 | `async.poll.handleParam: id` binds the handle to Aruba's own name ([#46](https://github.com/braghettos/krateo-oasgen-provider/issues/46)) |
| strip `nullable: true` | ~4119 | **No-op.** Zero references in oasgen's `oas2jsonschema` or anywhere in RDC; removing the strip left all 34 RestDefinitions byte-identical |
| strip `readOnly` / `writeOnly` | 25 | **No-op**, same evidence |
| merge compute `v1.1` create into the base document | 1 | Dead weight: CloudServer delegates create via `createApiRef`, so the v1.1 `POST` was never referenced by any verb |
| security scheme `apiKey` → `http`/`bearer` | 8 | **The one that was real** — now [#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49) upstream instead of a local edit |

Two of those six were fixed upstream, two were never load-bearing, one was
unnecessary, and the last became an issue rather than a patch.

## Constructs consumed as-is (documented, not "fixed")

- `nullable`, `readOnly`/`writeOnly` — ignored by the toolchain; harmless (§A1, §A3).
- `additionalProperties: {schema}` — native typed maps since oasgen 0.18.0 (§A2).
- `format`, `number`/`double`, constraint keywords — see §A4–A6.
- `allOf` — merged natively by oasgen.

## Reproducing / auditing

Every generated artifact derives from these untouched documents:

```sh
python3 scripts/generate_restdefinitions.py   # -> restdefinitions/
python3 scripts/gen_configmaps.py             # -> configmaps/ (embeds the specs verbatim)
python3 scripts/gen_samples_and_coverage.py   # -> samples/ + docs/coverage.md
python3 scripts/gen_provider_docs.py          # -> docs/providers/
python3 scripts/validate.py                   # incl. the sha256 immutability check
```
