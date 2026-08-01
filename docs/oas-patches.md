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
| strip `nullable: true` | ~4117 | §A1 |
| coerce `additionalProperties: {schema}` → `true` | ~42 | §A2 |
| strip `readOnly` / `writeOnly` | ~25 | §A3 |
| security scheme `apiKey`-header → `http`/`bearer` | 8 | §A7 |
| merge compute `v1.1` create into base doc | 1 | §D1 |
| rename baremetal monitor param `{id}` → `{operationId}` | 1 | §C2 addendum |

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

### Coerce `additionalProperties` objects

Only the boolean form is supported, so typed free-form maps (e.g.
`metadata.annotations`, `metadata.labels`, alert `labels`/`parameters`, cloud
server network-interface `properties`) become `additionalProperties: true` — an
**untyped** map in the CRD. The value type is lost.

### Strip `readOnly` / `writeOnly`

Dropped. Server-managed fields (e.g. `AlertRule.lastReception/lastActivation/
lastEdit`, `VpcTypologyExtraInfo.max*Count`) therefore land in the spec as
writable rather than status-only.

### Merge compute v1.1 create

`compute/CloudServer`'s create lives in a separate document
(`compute-provider_v1.1.json`) from its other verbs. A RestDefinition references a
single `oasPath`, so the v1.1 `POST /cloudServers` is spliced into the base compute
document before generation.

### Rename the async monitor path parameter

`GET …/hpcs/monitor/{id}` becomes `GET …/hpcs/monitor/{operationId}`. This is a
hard runtime contract of rest-dynamic-controller's async poller, not a style
choice: the poll path is resolved by **exact string** lookup in the OAS and the
extracted operation handle binds to a path parameter literally named
`operationId`. With the original `{id}` name, every poll call fails with "path
not found". See [adversarial-review](adversarial-review.md) finding #1.

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
