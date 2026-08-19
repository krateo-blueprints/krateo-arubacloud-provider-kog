---
type: API
title: krateo-arubacloud-provider-kog — API
description: The CRD contracts this blueprint relies on and produces — the RestDefinition CRD it authors, the CompositionDefinition CRD for whole-environment provisioning, and the per-resource CRDs oasgen-provider generates from them.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [api, crd, restdefinition, compositiondefinition, restaction]
timestamp: 2026-08-11T00:00:00Z
---

# API

This blueprint contributes no CRDs of its own binary; it *authors* instances of
CRDs owned by the Krateo platform (oasgen-provider, core-provider, snowplow) and,
through them, causes new per-resource CRDs to be generated. This page documents
each contract.

## `RestDefinition` — `ogen.krateo.io/v1alpha1`

Owned by **oasgen-provider**. One `RestDefinition` per Aruba resource lives under
`restdefinitions/<provider>/<kind>.yaml`. Applying it makes oasgen-provider
generate a CRD for `resource.kind` (group `arubacloud.ogen.krateo.io`) and deploy a
rest-dynamic-controller for it.

Top-level `spec`:

| Field | Type | Description |
|-------|------|-------------|
| `oasPath` | string | `configmap://<namespace>/<configmap>/<file.json>` — the patched OpenAPI spec |
| `resourceGroup` | string | API group of the generated CRD (`arubacloud.ogen.krateo.io`) |
| `resource` | object | the resource description (below) |

`spec.resource`:

| Field | Type | Description |
|-------|------|-------------|
| `kind` | string | the generated Kind |
| `identifiers` | []string | fields that identify the resource, e.g. `[metadata.name]` (nested) |
| `additionalStatusFields` | []string | fields surfaced into `status`, e.g. `[metadata.id]` |
| `excludedSpecFields` | []string | fields returned by the API but excluded from spec |
| `verbsDescription` | []object | one entry per verb (below) |
| `configurationFields` | []object | maps an OpenAPI query param to one or more actions |
| `createApiRef` / `updateApiRef` / `deleteApiRef` | object | delegate that verb to a RESTAction (name + namespace + `extras`) |

`verbsDescription[]`:

| Field | Type | Description |
|-------|------|-------------|
| `action` | string | one of `findby`, `get`, `create`, `update`, `delete` |
| `method` | string | HTTP method |
| `path` | string | OpenAPI path template (path params in `{braces}`) |
| `requestFieldMapping` | []object | `{inPath: <param>, inCustomResource: <field>}` — feeds a CR/status field into a path param |

Example (`restdefinitions/network/subnet.yaml`):

```yaml
apiVersion: ogen.krateo.io/v1alpha1
kind: RestDefinition
metadata:
  name: arubacloud-network-subnet
spec:
  oasPath: configmap://krateo-system/arubacloud-network-openapi/network.json
  resourceGroup: arubacloud.ogen.krateo.io
  resource:
    kind: Subnet
    identifiers: [metadata.name]
    additionalStatusFields: [metadata.id]
    excludedSpecFields: [id]
    verbsDescription:
    - {action: get, method: GET, path: /projects/{projectId}/providers/Aruba.Network/vpcs/{vpcId}/subnets/{id},
       requestFieldMapping: [{inPath: id, inCustomResource: status.metadata.id}]}
    # ...findby/create/update/delete likewise
```

## Generated per-resource CRDs — `arubacloud.ogen.krateo.io/v1alpha1`

For every `RestDefinition`, oasgen-provider generates two CRDs in group
`arubacloud.ogen.krateo.io`:

- `<Kind>` — the managed resource (`spec` derived from the OAS body schema, minus
  `excludedSpecFields`; `status.metadata.id` populated from the API response).
  Every CR references its config via `spec.configurationRef`.
- `<Kind>Configuration` — carries `authentication.bearer.tokenRef` and
  `configuration.query.<verb>` (the query params declared in
  `configurationFields`). See [Configuration](configuration.md).

The full per-provider list of kinds, verbs and endpoints is in the
[Provider reference](providers/README.md) and the
[Coverage matrix](coverage.md).

## `RESTAction` — `templates.krateo.io/v1`

Owned by **snowplow**. Used only where a verb is a multi-call sequence
(`compute/CloudServer`). A `RestDefinition`'s `createApiRef`/`updateApiRef`/
`deleteApiRef` names a `RESTAction`; RDC invokes it every reconcile with the CR's
spec as request extras, so it must be **idempotent**. `spec.api[]` is a list of
steps, each with a `path`, `verb`, optional `payload`, and an optional
`dependsOn.iterator` guard (a step whose iterator yields an empty array runs zero
times — "create only if absent"). See `restactions/compute/` and
[Lifecycle beyond CRUD](lifecycle-beyond-crud.md).

## `CompositionDefinition` — `core.krateo.io/v1alpha1`

Owned by **core-provider**. Registers the `aruba-cloudserver-environment` Helm
chart as a Composition. Applying the definition generates a CRD; applying one
instance of it provisions the whole environment from a single input set.

```yaml
apiVersion: core.krateo.io/v1alpha1
kind: CompositionDefinition
metadata:
  name: aruba-cloudserver-environment
  namespace: krateo-system
spec:
  chart:
    url: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
    version: "0.1.0"
```

`spec.chart`:

| Field | Type | Description |
|-------|------|-------------|
| `url` | string | OCI (or HTTP) location of the packaged chart |
| `version` | string | chart version to pull |

The instance's `spec` is validated by the chart's `values.schema.json`; its fields
are documented in [Configuration](configuration.md#composition-chart-values).
