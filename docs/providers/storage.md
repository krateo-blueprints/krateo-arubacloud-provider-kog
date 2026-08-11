---
type: Architecture
title: krateo-arubacloud-provider-kog — Aruba Cloud Storage provider
description: Storage provider reference — every resource, its verbs, endpoints, config and samples.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [providers, storage, reference]
timestamp: 2026-08-11T00:00:00Z
---

# Aruba Cloud Storage provider

- **OpenAPI**: `Aruba.StorageProvider.Api` v1.0.0 (`openapi/_source/storage.json` → patched `openapi/storage.json`)
- **Security scheme (patched)**: `accessToken` (HTTP Bearer)
- **ConfigMap**: `arubacloud-storage-openapi` in `krateo-system`
- **Resources**: 4


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Backup` | findby, get, create, update, delete | `metadata.name` | — |
| `BlockStorage` | findby, get, create, update, delete | `metadata.name` | — |
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

status fields: `metadata.id` · excluded from spec: `id`

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

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/blockstorage.yaml`](../../samples/storage/blockstorage.yaml) · [`blockstorage-configuration.yaml`](../../samples/storage/blockstorage-configuration.yaml)

## Restore

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores` |
| get | GET | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Storage/backups/{backupId}/restores/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

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

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/storage/snapshot.yaml`](../../samples/storage/snapshot.yaml) · [`snapshot-configuration.yaml`](../../samples/storage/snapshot-configuration.yaml)
