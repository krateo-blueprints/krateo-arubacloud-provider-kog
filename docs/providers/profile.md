---
type: Architecture
title: Aruba Cloud Profile (AI keys) provider
description: Profile (AI keys) provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-09-28T00:00:00Z
---

# Aruba Cloud Profile (AI keys) provider

- **OpenAPI**: `Aruba.CmpService.ProfileService.Api` v1.0.0 (`openapi/profile.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-profile-openapi` in `krateo-system`
- **Resources**: 1


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `AiKey` | findby, get, update, delete | `metadata.name` | — |


## AiKey

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Profile/aiKeys` |
| get | GET | `/projects/{projectId}/providers/Aruba.Profile/aiKeys/{id}` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Profile/aiKeys/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Profile/aiKeys/{id}` |

status fields: `metadata.id, status.failureReason, status.error` · excluded from spec: `id`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/profile/aikey.yaml`](../../samples/profile/aikey.yaml) · [`aikey-configuration.yaml`](../../samples/profile/aikey-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| POST | `/projects/{projectId}/providers/Aruba.Profile/aiKeys/{id}/disable` | Disable AiKey |
| POST | `/projects/{projectId}/providers/Aruba.Profile/aiKeys/{id}/enable` | Enable AiKey |
