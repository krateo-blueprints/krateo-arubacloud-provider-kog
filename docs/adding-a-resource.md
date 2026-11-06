---
type: Usage
title: Adding a resource
description: The exact steps to cover a new Aruba resource with a RestDefinition.
tags: [aruba, kog]
timestamp: 2026-08-19T00:00:00Z
---

# Adding or customising a resource

RestDefinitions are produced by `scripts/generate_restdefinitions.py` from the
patched specs. Most resources need no manual work — if the OAS has a
collection + item endpoint pair, the generator emits a correct RestDefinition.
This page explains the rules so you can predict the output and override the
irregular cases.

## How the generator maps an API to a RestDefinition

For each provider spec it:

1. **Pairs endpoints** — a collection path (`.../things`) with every one-segment-
   deeper item path (`.../things/{param}`). Verbs are wired from what exists:
   `findby` (collection GET), `create` (collection POST), `get`/`update`/`delete`
   (item GET/PUT/DELETE). It handles APIs that use a **different path-param name
   per verb** (e.g. GET `{databaseName}` but DELETE `{name}`) by mapping each verb
   to its own item path.
2. **Requires observability** — a resource must have `get`, or `create` + `findby`.
   Pure POST action endpoints (poweron, attach, download, …) are skipped.
3. **Detects the metadata wrapper** — if the create body has a top-level
   `metadata` object, it uses the nested-identifier recipe.
4. **Emits** identifiers, status fields, excluded fields, per-verb
   `requestFieldMapping`, and `configurationFields` from each operation's query
   parameters (`api-version` for all verbs; list params for `findby`; etc.).

### Field rules

| Case | identifiers | additionalStatusFields | excludedSpecFields | id mapping |
|------|-------------|------------------------|--------------------|------------|
| metadata-wrapped | `metadata.name` | `metadata.id` | item id param | `{id} ← status.metadata.id` |
| flat, server id | `name` (or override) | `id` | item id param | `{id} ← status.id` |
| name-keyed sub-resource | the natural key | — | non-key path params | `{param} ← spec.<key>` |
| read-only | `name`/`metadata.name` | — | — | — |

## The override tables (top of the generator)

| Table | Purpose |
|-------|---------|
| `KIND_MAP` | seg → Kind singularisation (e.g. `kaas` → `Kaas`, `kms` → `Kms`) |
| `PROVIDER_KIND` | disambiguate repeated names across providers (`database/backups` → `DatabaseBackup`, `container/backups` → `KaasBackup`) — kinds must be unique in the shared API group |
| `SKIP` | collections that are really action endpoints (`schedule/executions`) |
| `READONLY` | collections exposed as get/findby only (`network/loadBalancers`) |
| `OVERRIDES` | per-resource shape: `metaWrap`, `idField`, `statusId`, `apiRefs`, `note` |

### Overriding a resource

Add an entry keyed `"<provider>/<seg>"` to `OVERRIDES`. Examples already present:

- `project/folders` — `metaWrap=False, idField=name, statusId=status.id` (flat).
- `database/databases` — name-keyed, documents the `{databaseName}`/`{name}` OAS
  inconsistency.
- `compute/cloudServers` — `apiRefs` delegates create/update/delete to Snowplow
  RESTActions while observe stays native (see
  [lifecycle-beyond-crud](lifecycle-beyond-crud.md)).

### Delegating verbs (lifecycle beyond CRUD)

Set `apiRefs` in an override:

```python
"compute/cloudServers": dict(
    metaWrap=True,
    apiRefs=dict(namespace=CM_NS, extras={"api-version": "1.0"},
                 create="…-create", update="…-update", delete="…-delete")),
```

The generator then keeps only `findby`/`get` verbs and adds
`createApiRef`/`updateApiRef`/`deleteApiRef`. Author the matching RESTActions under
`restactions/<provider>/`.

## Adding a brand-new resource

1. Make sure the endpoint pair exists in `openapi/<provider>.json` (add
   the provider spec if it is new, and register it in `PROVIDERS`).
2. If it follows the metadata or flat pattern, just regenerate — it appears
   automatically. If it is irregular, add an `OVERRIDES` entry.
3. Regenerate and validate:

```sh
python3 scripts/generate_restdefinitions.py
python3 scripts/gen_configmaps.py
python3 scripts/gen_samples_and_coverage.py
python3 scripts/gen_provider_docs.py
python3 scripts/validate.py
```

4. Optionally, check the new sample against the CRD that will actually admit it:

```sh
scripts/check_samples.py --from-cluster            # a cluster that has the CRDs
scripts/check_samples.py --from-cluster --context kind-aruba-pin
```

`validate.py` needs no cluster, which is why it cannot know what the generated CRD
contains. That comparison otherwise exists only in the kind smoke job's
`kubectl apply --dry-run=server`, so a sample that no longer fits its CRD is a push
and a four-minute wait away. `check_samples.py` asks the same question locally
against CRDs read from a cluster (or from a directory of rendered ones).

It is not a replacement for the dry-run: the apiserver is the authority, and the
smoke job remains the gate. Two things it reports that are easy to misread:

* **a sample whose CRD is absent is a failure, not a skip.** Pointed at a cluster
  that carries an older or partial set of definitions, it names every sample it
  could not check and exits non-zero. A checker that skips silently reports clean.
* `scripts/check_samples.py --prove` runs the comparison against hand-written
  schemas, including the two shapes that really broke, and needs no cluster. Run it
  if a clean result ever looks too good.

## Immutability

`resourceGroup`, `resource.kind`, `identifiers`, `additionalStatusFields`,
`excludedSpecFields`, and `configurationFields` are **immutable** after the
RestDefinition is created (enforced by CEL in the CRD). Changing any of them means
deleting and recreating the RestDefinition (and its generated CRD). Reconcile
behaviour fields (`fieldMapping`, `*ApiRef`, `compareScope`, …) are mutable.
