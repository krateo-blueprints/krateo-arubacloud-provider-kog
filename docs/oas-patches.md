# OAS patches reference

The raw Aruba Cloud specs under `openapi/_source/` are OpenAPI **3.0.1** and use a
few constructs `oasgen-provider` cannot consume. `scripts/patch_oas.py` rewrites
exactly those, emitting the consumable specs under `openapi/`. **Every patch is a
symptom of an oasgen-provider gap** — the full analysis (with resource-level
impact) is in [oasgen-provider-evolution](oasgen-provider-evolution.md) §A; this
page is the operational "what changed and why".

Run the patcher to see current counts:

```sh
python3 scripts/patch_oas.py
```

Representative totals across the 11 source specs:

| Transformation | Count | oasgen gap |
|----------------|------:|------------|
| strip `nullable: true` | ~4119 | §A1 |
| strip `readOnly` / `writeOnly` | ~25 | §A3 |
| security scheme `apiKey`-header → `http`/`bearer` | 8 | §A7 |
| merge compute `v1.1` create into base doc | 1 | §D1 |

## Retired patches (upstream shipped the fix)

Two transformations were **removed** once oasgen/RDC 0.18.0 landed — the specs
are now consumed closer to as-published:

| Retired transformation | Was | Now |
|------------------------|-----|-----|
| coerce `additionalProperties: {schema}` → `true` (42×) | typed maps degraded to untyped objects in the CRD | oasgen 0.18.0 emits a **typed map** ([#45](https://github.com/braghettos/krateo-oasgen-provider/issues/45)) |
| rename baremetal monitor param `{id}` → `{operationId}` (1×) | the vendor document had to be edited to satisfy a hardcoded parameter name | `async.poll.handleParam: id` uses Aruba's path **unmodified** ([#46](https://github.com/braghettos/krateo-oasgen-provider/issues/46)) |

The `nullable` count moved 4117 → 4119 purely as a side effect: value schemas
inside object-form `additionalProperties` are now traversed instead of being
replaced wholesale by `true`, so two more `nullable` keys are reached.

## Each transformation

### Security scheme (required for auth to work)

Before (raw):

```yaml
securitySchemes:
  Bearer: {type: apiKey, in: header, name: Authorization}
security: [{Bearer: []}]
```

After (patched):

```yaml
securitySchemes:
  accessToken: {type: http, scheme: bearer, bearerFormat: JWT}
security: [{accessToken: []}]
```

oasgen-provider wires Bearer only from an `http`/`bearer` scheme; the raw
`apiKey`-header form would never be treated as a credential. This is the same fix
the original blueprint documented in its `oas_changes_references.md`.

### Strip `nullable`

`nullable: true` (an OAS 3.0 keyword removed in 3.1) is unsupported and dropped.
Safe for CRD generation, but it means a field the API may return as `null` becomes
non-nullable in the CRD — a contract change worth knowing when debugging strict
response validation.

### Strip `readOnly` / `writeOnly`

Dropped. Server-managed fields (e.g. `AlertRule.lastReception/lastActivation/
lastEdit`, `VpcTypologyExtraInfo.max*Count`) therefore land in the spec as
writable rather than status-only.

### Merge compute v1.1 create

`compute/CloudServer`'s create lives in a separate document
(`compute-provider_v1.1.json`) from its other verbs. A RestDefinition references a
single `oasPath`, so the v1.1 `POST /cloudServers` is spliced into the base compute
document before generation.

## Left untouched on purpose (documented, not silently "fixed")

- `format` (int32/int64/double/date-time/uuid/uri) — appended to the field
  description by oasgen; harmless. See §A5.
- `number` / `format: double` — coerced to integer by oasgen (precision loss on
  monetary `price` fields). Left as-is and flagged in §A4.
- `allOf` — merged natively by oasgen.

## Reproducing / auditing

The patcher is deterministic and idempotent. To refresh from updated upstream
specs, replace the files in `openapi/_source/` and re-run the full pipeline (see
[index](index.md#regenerating-everything)). Diff `openapi/` against
`openapi/_source/` to audit exactly what changed.
