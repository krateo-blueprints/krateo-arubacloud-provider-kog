---
type: Architecture
title: Aruba Cloud Container (Kubernetes) provider
description: Container (Kubernetes) provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-09-28T00:00:00Z
---

# Aruba Cloud Container (Kubernetes) provider

- **OpenAPI**: `Aruba.Container.Api` v1.0.0 (`openapi/container-provider.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-container-openapi` in `krateo-system`
- **Resources**: 3


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Kaas` | findby, get, create, update, delete | `metadata.name` | — |
| `KaasBackup` | findby, get, create, update, delete | `metadata.name` | — |
| `Registry` | findby, get, create, update, delete | `metadata.name` | — |


## Kaas

> Kaas reads return a different shape than its writes accept: nodePools[].instance and nodePools[].dataCenter are strings in NodePoolsDto and objects in NodePoolsResponseDto. RDC compares the CR's keys against the response, so those two are permanent drift, and no compareScope avoids them because KaasUpdatePropertiesDto declares only nodePools. A responseTransform on the get verb folds each object back to its .name before the comparison runs. nodeCidr (returned as `nodecidr`) and securityGroup.name (returned as `uri`) need no handling: those keys are absent from the response and are skipped.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Container/kaas` |
| get | GET | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Container/kaas` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/container/kaas.yaml`](../../samples/container/kaas.yaml) · [`kaas-configuration.yaml`](../../samples/container/kaas-configuration.yaml)

## KaasBackup

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Container/kaas/{kaasId}/backups` |
| get | GET | `/projects/{projectId}/providers/Aruba.Container/kaas/{kaasId}/backups/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Container/kaas/{kaasId}/backups` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Container/kaas/{kaasId}/backups/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Container/kaas/{kaasId}/backups/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/container/kaasbackup.yaml`](../../samples/container/kaasbackup.yaml) · [`kaasbackup-configuration.yaml`](../../samples/container/kaasbackup-configuration.yaml)

## Registry

> Registry creation is asynchronous and the OAS does not say so: the create returns the object while status.state is still InCreation, and both update and delete are refused until it leaves that state with 400 {fieldName: Status, errorMessage: Invalid status}. Without the async block the CR reports Ready for a registry nothing can use yet. Measured: InCreation for 1818s, then Failed -- no registry has reached Active on this account, so the gate's value here is failing fast rather than gating Ready. Separately: properties.size takes Small, Medium or HighPerf, case-sensitive -- measured, because the OAS describes it as a connection count and its RegistrySize catalogue is referenced by no path.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Container/registries` |
| get | GET | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Container/registries` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, loadHarborVersion, offset, projection, sort`

Sample: [`samples/container/registry.yaml`](../../samples/container/registry.yaml) · [`registry-configuration.yaml`](../../samples/container/registry-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| POST | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}/detach` | Detach Volume |
| GET | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}/download` | Download the configuration file of the cluster kubernetes |
| POST | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}/nodePools/{nodePoolId}/nodes/{nodeId}/attach` | Attach Volume |
| POST | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}/nodePools/{nodePoolId}/nodes/{nodeId}/detach` | Detach a volume from a kaas node |
| PUT | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}/nodePools/{nodePoolName}` | Update kaas nodepool |
| PATCH | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` | Patch Registry |
| PUT | `/projects/{projectId}/providers/Aruba.Container/registries/{id}/resetAdminPassword` | Update Admin Password Registry |
| GET | `/providers/Aruba.Container/catalog/kaasFlavors` | List kaas flavors |
| GET | `/providers/Aruba.Container/catalog/kubernetesVersions` | List kubernetes versions |
