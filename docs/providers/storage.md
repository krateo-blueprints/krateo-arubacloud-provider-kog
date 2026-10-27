---
type: Architecture
title: Aruba Cloud Storage provider
description: Storage provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-09-28T00:00:00Z
---

# Aruba Cloud Storage provider

- **OpenAPI**: `Aruba.StorageProvider.Api` v1.0.0 (`openapi/storage-provider.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-storage-openapi` in `krateo-system`
- **Resources**: 5


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Backup` | findby, get, create, update, delete | `metadata.name` | — |
| `BlockStorage` | findby, get, create, update, delete | `metadata.name` | — |
| `Image` | findby, get, create, update, delete | `metadata.name` | — |
| `Restore` | findby, get, create, update, delete | `metadata.name` | — |
| `Snapshot` | findby, get, create, update, delete | `metadata.name` | — |


## Backup

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Storage/backups` |
| get | GET | `/projects/{projectId}/providers/Aruba.Storage/backups/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Storage/backups` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Storage/backups/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Storage/backups/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/backup.yaml`](../../samples/storage/backup.yaml) · [`backup-configuration.yaml`](../../samples/storage/backup-configuration.yaml)

## BlockStorage

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Storage/blockStorages` |
| get | GET | `/projects/{projectId}/providers/Aruba.Storage/blockStorages/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Storage/blockStorages` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Storage/blockStorages/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Storage/blockStorages/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/blockstorage.yaml`](../../samples/storage/blockstorage.yaml) · [`blockstorage-configuration.yaml`](../../samples/storage/blockstorage-configuration.yaml)

## Image

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Storage/images` |
| get | GET | `/projects/{projectId}/providers/Aruba.Storage/images/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Storage/images` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Storage/images/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Storage/images/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/image.yaml`](../../samples/storage/image.yaml) · [`image-configuration.yaml`](../../samples/storage/image-configuration.yaml)

## Restore

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores` |
| get | GET | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/restore.yaml`](../../samples/storage/restore.yaml) · [`restore-configuration.yaml`](../../samples/storage/restore-configuration.yaml)

## Snapshot

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Storage/snapshots` |
| get | GET | `/projects/{projectId}/providers/Aruba.Storage/snapshots/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Storage/snapshots` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Storage/snapshots/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Storage/snapshots/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/snapshot.yaml`](../../samples/storage/snapshot.yaml) · [`snapshot-configuration.yaml`](../../samples/storage/snapshot-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Storage/images/{id}/associate-clients` | Get associated users for an Image |
| POST | `/projects/{projectId}/providers/Aruba.Storage/images/{id}/associate-clients` | Update associated users for an Image |
| POST | `/projects/{projectId}/providers/Aruba.Storage/images/{id}/export` | Export image |
| GET | `/providers/Aruba.Storage/templates/catalog/images` | List images |
