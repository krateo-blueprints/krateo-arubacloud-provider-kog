---
type: Architecture
title: Aruba Cloud Database (DBaaS) provider
description: Database (DBaaS) provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-09-28T00:00:00Z
---

# Aruba Cloud Database (DBaaS) provider

- **OpenAPI**: `Aruba.CmpService.DatabaseProvider.Api` v1.0.0 (`openapi/database-provider.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-database-openapi` in `krateo-system`
- **Resources**: 5


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Database` | findby, get, create, delete | `name` | — |
| `DatabaseBackup` | findby, create, delete | `metadata.id` | — |
| `DatabaseUser` | findby, get, create, delete | `username` | — |
| `Dbaas` | findby, get, create, update, delete | `metadata.name` | — |
| `Grant` | findby, get, create, delete | `user.username` | — |


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

> Aruba names a backup itself: a create asking for 'ga-dbbackup' produces 'mysql_<database>_<UTC timestamp>'. The requested name is accepted and discarded, and the OAS does not say so. With the default identifier metadata.name the spec can therefore never equal the response, so every reconcile reads as drift and the controller requests an update -- and there is no update verb to call, because Aruba exposes only DELETE on /backups/{id}. Observed live: one create, then 'API action update not found' once per reconcile while the CR sat at Ready=False/Creating, with the backup Active on Aruba twelve seconds after creation. metadata.id is the server-assigned key the delete verb already takes; findby matches it through isInResource's status fallback, exactly as it matched the renamed metadata.name before. compareScope must move with it: identifiersAndStatus restricts drift to identifiers plus additionalStatusFields, and every other spec field here is create-only (projectId, properties.dbaas, properties.database, billingPlan, dataCenter). 'updatable' is the closer fit in spirit but is refused by CEL without an update verb. Both keys are needed: identifiersAndStatus alone still compares metadata.name and still reports drift forever.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/backups` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/backups` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/backups/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/database/databasebackup.yaml`](../../samples/database/databasebackup.yaml) · [`databasebackup-configuration.yaml`](../../samples/database/databasebackup-configuration.yaml)

## DatabaseUser

> DatabaseUser is name-keyed (create {username, password}). 'password' is dropped from the spec and sourced from a Secret at create time via the secretRef resolver. The Secret is named after the CR and keyed by the username, both read from the CR itself - which is what makes this work without adding a *SecretRef field the source OAS does not declare, and the OAS is not ours to change. Its VALUE must be the base64 of the password: Aruba requires that and does not document it (their Terraform provider encodes client-side, dbaasuser_resource.go:130). A jq valueMapping of @base64 could not do that encoding for us on 0.22.2, which ran jq on the response direction only and silently skipped it on a request. oasgen#148 implements the request direction and shipped in 0.26.0, so the 0.27.1 we now pin CAN do it and this Secret could hold the plaintext instead. It still does not: that change alters what every existing DatabaseUser Secret must contain, so it gets its own commit and its own live run rather than riding along with the version bump - see docs/authentication.md. No update verb (password change is a separate PUT .../password sub-endpoint).

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users` |
| get | GET | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users/{username}` |

excluded from spec: `password`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/database/databaseuser.yaml`](../../samples/database/databaseuser.yaml) · [`databaseuser-configuration.yaml`](../../samples/database/databaseuser-configuration.yaml)

## Dbaas

> Dbaas creation is asynchronous: the API returns the object while status.state is still InCreation, and refuses every update until it reaches Active (400 DBaaS.Read.InvalidStatus). Without the async block the CR reports Ready in seconds for a resource that is unusable for a quarter of an hour. Polls its own item path, since this provider publishes no monitor endpoint. State values come from security-provider.json, which declares the vocabulary the database spec omits.

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

> Grant is name-keyed. The create body nests the name as {user:{username}} and the item path segment is {username}, so the identifier must be the LEAF user.username, NOT the object `user`. Binding the object produced GET .../grants/map%5Busername:gauser%5D -- Go stringified the map into the path -- so observe never matched and the controller re-POSTed until the API answered 'A grant already exists for the specified Database User'. No update verb and no dedicated id.

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
