# Aruba Cloud Bare Metal provider

- **OpenAPI**: `Aruba.BaremetalProvider.Api` v1.0.0 (`openapi/_source/baremetal.json` → patched `openapi/baremetal.json`)
- **Security scheme (patched)**: `accessToken` (HTTP Bearer)
- **ConfigMap**: `arubacloud-baremetal-openapi` in `krateo-system`
- **Resources**: 1


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Hpc` | findby, get, create | `metadata.name` | — |


## Hpc

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs` |
| get | GET | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, calculatePrices, filter, limit, offset, projection, sort`

Sample: [`samples/baremetal/hpc.yaml`](../../samples/baremetal/hpc.yaml) · [`hpc-configuration.yaml`](../../samples/baremetal/hpc-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs/monitor/{id}` | Check HPC creation status |
| PUT | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs/{id}/automaticrenew` | Set HPC automatic renew |
| PUT | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs/{id}/name` | Rename HPC |
| GET | `/projects/{projectId}/providers/Aruba.Baremetal/hpcs/{id}/services` | Get HPC services |
