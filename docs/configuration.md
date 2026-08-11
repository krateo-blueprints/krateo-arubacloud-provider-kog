---
type: Configuration
title: krateo-arubacloud-provider-kog — configuration
description: The whole config surface — the Aruba token Secret, the per-kind <Kind>Configuration (authentication + per-verb query config), the RestDefinition fields that shape a CRD, and the Composition chart values.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [configuration, secret, configuration-cr, restdefinition, chart-values]
timestamp: 2026-08-11T00:00:00Z
---

# Configuration

Configuration lives at three layers: the shared auth **Secret**, a per-kind
**`<Kind>Configuration`**, and — for the whole-environment path — the
**Composition chart values**. The `RestDefinition` fields that shape each CRD are
also documented here for reference.

## 1. The auth Secret

A single Kubernetes `Secret` holds the Aruba Bearer token (short-lived JWT). It is
referenced by every `<Kind>Configuration`. Tokens are short-lived; rotation is the
operator's responsibility. See `samples/arubacloud-token-secret.yaml`.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: arubacloud-token
  namespace: default
type: Opaque
stringData:
  token: REPLACE_WITH_ARUBA_JWT
```

## 2. The `<Kind>Configuration`

Per resource kind, a `<Kind>Configuration` (group
`arubacloud.ogen.krateo.io/v1alpha1`) references the token Secret and carries
per-verb query config. Each CR references it via `spec.configurationRef`. Example
(`samples/network/subnet-configuration.yaml`):

```yaml
apiVersion: arubacloud.ogen.krateo.io/v1alpha1
kind: SubnetConfiguration
metadata:
  name: subnet-config
  namespace: default
spec:
  authentication:
    bearer:
      tokenRef:
        name: arubacloud-token
        namespace: default
        key: token
  configuration:
    query:
      findby:
        api-version: '1.0'
      get:
        api-version: '1.0'
        ignoreDeletedStatus: false
      create:
        api-version: '1.0'
      update:
        api-version: '1.0'
      delete:
        api-version: '1.0'
```

- `authentication.bearer.tokenRef` — points at the Secret above (name, namespace,
  key).
- `configuration.query.<verb>` — the query parameters injected for that verb. The
  available parameters per verb come from the `configurationFields` block of the
  `RestDefinition` (each maps an OpenAPI query parameter to one or more actions).
  `api-version` is bound to `*` (all actions); `findby` additionally accepts
  `filter`, `sort`, `projection`, `offset`, `limit`; `get` accepts
  `ignoreDeletedStatus`.

## 3. RestDefinition fields (how a CRD is shaped)

Each `RestDefinition` (`ogen.krateo.io/v1alpha1`) declares:

| Field | Purpose |
|-------|---------|
| `oasPath` | `configmap://<ns>/<configmap>/<file.json>` — the patched spec to generate from |
| `resourceGroup` | the API group of the generated CRD (`arubacloud.ogen.krateo.io`) |
| `resource.kind` | the generated Kind |
| `resource.identifiers` | nested identifier(s), e.g. `[metadata.name]` — no flattening proxy |
| `resource.additionalStatusFields` | fields surfaced into `status`, e.g. `[metadata.id]` |
| `resource.excludedSpecFields` | fields the API returns but that must not appear in spec |
| `resource.verbsDescription[]` | one entry per verb: `action`, `method`, `path`, optional `requestFieldMapping` |
| `resource.configurationFields[]` | maps OpenAPI query params to actions → the `<Kind>Configuration` surface |
| `resource.createApiRef` / `updateApiRef` / `deleteApiRef` | delegate that verb to a Snowplow RESTAction (multi-call lifecycle) |

`requestFieldMapping` feeds a status field back into a path parameter (e.g.
`inPath: id` ← `inCustomResource: status.metadata.id`), which is what lets
`get`/`update`/`delete` address a resource by its server-assigned id.

## 4. Composition chart values {#composition-chart-values}

The `aruba-cloudserver-environment` chart takes one high-level input set and fans
it out into `Vpc` + `Subnet` + `SecurityGroup` + `CloudServer` and their
Configurations. Values (`compositions/aruba-cloudserver-environment/values.yaml`,
typed by `values.schema.json`):

| Value | Required | Default | Meaning |
|-------|----------|---------|---------|
| `namespace` | — | `default` | target namespace for the generated CRs |
| `projectId` | **yes** | `REPLACE_PROJECT_ID` | Aruba project id the environment lives in |
| `location` | **yes** | `ITBG-Bergamo` | Aruba location/region value |
| `apiVersion` | — | `"1.0"` | Aruba API version for the query config |
| `name` | **yes** | `demo` | name prefix applied to every generated resource |
| `tokenSecret.{name,namespace,key}` | **yes** | `arubacloud-token`/`default`/`token` | reference to the auth Secret |
| `vpc.cidr` | — | `10.0.0.0/16` | VPC network address |
| `subnet.cidr` | — | `10.0.1.0/24` | subnet network address |
| `subnet.type` | — | `Advanced` | one of `Basic`, `Advanced` |
| `securityGroup` | — | `{}` | security group settings (open object) |
| `cloudServer.flavorName` | — | `A1` | server flavor |
| `cloudServer.bootVolume` | — | `{sizeGb: 40}` | boot volume, passed through to `spec.properties` |
| `cloudServer.userData` | — | `""` | cloud-init user data, passed through verbatim |

> Cross-resource references (wiring the created VPC/subnet/SG runtime URIs into the
> `CloudServer`) are a known gap — the chart passes desired selections through
> `spec.properties`; see [Lifecycle beyond CRUD](lifecycle-beyond-crud.md).
