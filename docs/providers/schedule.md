# Aruba Cloud Schedule provider

- **OpenAPI**: `Aruba.CmpService.ScheduleProvider.Api` v1.0.0 (`openapi/_source/schedule.json` → patched `openapi/schedule.json`)
- **Security scheme (patched)**: `accessToken` (HTTP Bearer)
- **ConfigMap**: `arubacloud-schedule-openapi` in `krateo-system`
- **Resources**: 3


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `BackupPolicy` | findby, get, create, update, delete | `metadata.name` | — |
| `BackupPolicyAssignment` | findby, get, create, update, delete | `metadata.name` | — |
| `Job` | findby, get, create, update, delete | `metadata.name` | — |


## BackupPolicy

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies` |
| get | GET | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies/{backupPolicyId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies/{backupPolicyId}` |

status fields: `metadata.id` · excluded from spec: `backupPolicyId, id`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/schedule/backuppolicy.yaml`](../../samples/schedule/backuppolicy.yaml) · [`backuppolicy-configuration.yaml`](../../samples/schedule/backuppolicy-configuration.yaml)

## BackupPolicyAssignment

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicyAssignments` |
| get | GET | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicyAssignments/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicyAssignments` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicyAssignments/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicyAssignments/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/schedule/backuppolicyassignment.yaml`](../../samples/schedule/backuppolicyassignment.yaml) · [`backuppolicyassignment-configuration.yaml`](../../samples/schedule/backuppolicyassignment-configuration.yaml)

## Job

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs` |
| get | GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Schedule/jobs` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}` |

status fields: `metadata.id` · excluded from spec: `jobId`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/schedule/job.yaml`](../../samples/schedule/job.yaml) · [`job-configuration.yaml`](../../samples/schedule/job-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/executions` | List Job Executions |
| POST | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/executions` | Force Job Execution |
| GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/executions/{jobExecutionId}` | Get Job Executions |
| GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/plannings` | List Job Plannings |
