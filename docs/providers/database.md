# Aruba Cloud Database (DBaaS) provider

- **OpenAPI**: `Aruba.CmpService.DatabaseProvider.Api` v1.0.0 (`openapi/database-provider.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme (patched)**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-database-openapi` in `krateo-system`
- **Resources**: 5


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Database` | findby, get, create, delete | `name` | — |
| `DatabaseBackup` | findby, create, delete | `metadata.name` | — |
| `DatabaseUser` | findby, get, create, delete | `username` | — |
| `Dbaas` | findby, get, create, update, delete | `metadata.name` | — |
| `Grant` | findby, get, create, delete | `user` | — |


## Database

> Database is name-keyed: create body {name}; there is no server id and no update verb. NOTE the source OAS is inconsistent - GET uses {databaseName} while DELETE uses {name} for the same path segment.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases` |
| get | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{name}` |

excluded from spec: `databaseName`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/database/database.yaml`](../../samples/database/database.yaml) · [`database-configuration.yaml`](../../samples/database/database-configuration.yaml)

## DatabaseBackup

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/backups` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/backups` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/backups/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/database/databasebackup.yaml`](../../samples/database/databasebackup.yaml) · [`databasebackup-configuration.yaml`](../../samples/database/databasebackup-configuration.yaml)

## DatabaseUser

> DatabaseUser is name-keyed (create {username, password}). 'password' is a plaintext spec field in the source OAS; sourcing it from a Kubernetes Secret needs the secretRef resolver plus an OAS-declared *SecretRef field - see docs/oasgen-provider-evolution.md. No update verb (password change is a separate PUT .../password sub-endpoint).

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users` |
| get | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}` |

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/database/databaseuser.yaml`](../../samples/database/databaseuser.yaml) · [`databaseuser-configuration.yaml`](../../samples/database/databaseuser-configuration.yaml)

## Dbaas

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas` |
| get | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/dbaas` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Database/dbaas/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/dbaas/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/database/dbaas.yaml`](../../samples/database/dbaas.yaml) · [`dbaas-configuration.yaml`](../../samples/database/dbaas-configuration.yaml)

## Grant

> Grant is name-keyed (create {user, role}); item path segment is {username}. No update/dedicated id.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/grants` |
| get | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/grants/{username}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/grants` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/grants/{username}` |

excluded from spec: `username`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/database/grant.yaml`](../../samples/database/grant.yaml) · [`grant-configuration.yaml`](../../samples/database/grant-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| POST | `/projects/{projectId}/providers/Aruba.Database/backups/{id}/download` | Generate DatabaseBackup download link |
| POST | `/projects/{projectId}/providers/Aruba.Database/backups/{id}/restore` | Restore DatabaseBackup |
| GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/backups/scheduled` | List Scheduled Backups |
| POST | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/backups/scheduled/{backupName}/download` | Generate Scheduled Backup download link |
| POST | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/databases/{databaseName}/backups/scheduled/{backupName}/restore` | Restore Scheduled Backup |
| GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}/grants` | List Grants by User |
| GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}/grants/{databaseName}` | Get Grant by User |
| PUT | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}/password` | Set Password |
