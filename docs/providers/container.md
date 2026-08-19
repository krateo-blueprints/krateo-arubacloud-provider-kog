---
type: Architecture
title: Aruba Cloud Container (Kubernetes) provider
description: Container (Kubernetes) provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-08-19T00:00:00Z
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

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Container/kaas` |
| get | GET | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Container/kaas` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Container/kaas/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

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

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/container/kaasbackup.yaml`](../../samples/container/kaasbackup.yaml) · [`kaasbackup-configuration.yaml`](../../samples/container/kaasbackup-configuration.yaml)

## Registry

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Container/registries` |
| get | GET | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Container/registries` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Container/registries/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

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
| PUT | `/projects/{projectId}/providers/Aruba.Container/registries/{id}/resetAdminPassword` | Update Admin Password Registry |
