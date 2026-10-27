---
type: Architecture
title: Aruba Cloud Schedule provider
description: Schedule provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-09-28T00:00:00Z
---

# Aruba Cloud Schedule provider

- **OpenAPI**: `Aruba.CmpService.ScheduleProvider.Api` v1.0.0 (`openapi/schedule-provider.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-schedule-openapi` in `krateo-system`
- **Resources**: 3


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `BackupPolicy` | findby, get, create, update, delete | `metadata.name` | — |
| `BackupPolicyAssignment` | findby, get, create, update, delete | `metadata.name` | — |
| `ScheduledJob` | findby, get, create, update, delete | `metadata.name` | — |


## BackupPolicy

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies` |
| get | GET | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies/{backupPolicyId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Schedule/backupPolicies/{backupPolicyId}` |

status fields: `metadata.id, status.failureReason` · excluded from spec: `backupPolicyId, id`

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

status fields: `metadata.id, status.failureReason` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/schedule/backuppolicyassignment.yaml`](../../samples/schedule/backuppolicyassignment.yaml) · [`backuppolicyassignment-configuration.yaml`](../../samples/schedule/backuppolicyassignment-configuration.yaml)

## ScheduledJob

> Job reads return values the writes did not send: steps[].actionUri comes back with a leading slash the create REFUSES to accept, and executeUntil comes back as +00:00 where the CR sent Z. Both are keys present on each side, so the drift comparison can never pass and the controller re-PUTs the job on every resync. A responseTransform on the get verb normalises both before the comparison. A step's actionUri must be relative (`poweroff`); only poweron and poweroff are supported, so a Job requires a CloudServer to target -- created out of band by scripts/ga_job_probe.py, since a RestDefinition cannot create one.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs` |
| get | GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Schedule/jobs` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}` |

status fields: `metadata.id, status.failureReason` · excluded from spec: `jobId`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/schedule/scheduledjob.yaml`](../../samples/schedule/scheduledjob.yaml) · [`scheduledjob-configuration.yaml`](../../samples/schedule/scheduledjob-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/executions` | List Job Executions |
| POST | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/executions` | Force Job Execution |
| GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/executions/{jobExecutionId}` | Get Job Executions |
| GET | `/projects/{projectId}/providers/Aruba.Schedule/jobs/{jobId}/plannings` | List Job Plannings |
