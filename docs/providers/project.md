# Aruba Cloud Project provider

- **OpenAPI**: `Aruba.CmpService.Project.Api` v1.0.0 (`openapi/project.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme (patched)**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-project-openapi` in `krateo-system`
- **Resources**: 2


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Folder` | findby, get, create, update, delete | `name` | — |
| `Project` | findby, get, create, update, delete | `metadata.name` | — |


## Folder

> Folder create body is flat {name, default}; id is a top-level response field.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/folders` |
| get | GET | `/folders/{id}` |
| create | POST | `/folders` |
| update | PUT | `/folders/{id}` |
| delete | DELETE | `/folders/{id}` |

status fields: `id` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/project/folder.yaml`](../../samples/project/folder.yaml) · [`folder-configuration.yaml`](../../samples/project/folder-configuration.yaml)

## Project

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects` |
| get | GET | `/projects/{id}` |
| create | POST | `/projects` |
| update | PUT | `/projects/{id}` |
| delete | DELETE | `/projects/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/project/project.yaml`](../../samples/project/project.yaml) · [`project-configuration.yaml`](../../samples/project/project-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/folders/metadata` | Get Metadata |
| POST | `/folders/{id}/automaticrenew` | Enable automatic renew |
| DELETE | `/folders/{id}/automaticrenew` | Disable automatic renew |
| GET | `/projects/resources` | List Resources |
| GET | `/projects/{projectId}/resources` | List Project Resources |
