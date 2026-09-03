---
type: Architecture
title: Aruba Cloud Metering (Insight) provider
description: Metering (Insight) provider reference — every resource, its verbs, identifiers and uncovered endpoints. Generated.
tags: [providers, aruba, reference]
timestamp: 2026-08-19T00:00:00Z
---

# Aruba Cloud Metering (Insight) provider

- **OpenAPI**: `Aruba.Insight.API` v1.0 (`openapi/metering.json`, vendored unmodified — see [OAS policy](../oas-patches.md))
- **Security scheme**: `Bearer` (HTTP Bearer)
- **ConfigMap**: `arubacloud-metering-openapi` in `krateo-system`
- **Resources**: 1


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `AlertRule` | findby, get, create, update, delete | `name` | — |


## AlertRule

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Insight/alertRules` |
| get | GET | `/projects/{projectId}/providers/Aruba.Insight/alertRules/{alertRulesId}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Insight/alertRules` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Insight/alertRules/{alertRulesId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Insight/alertRules/{alertRulesId}` |

status fields: `id` · excluded from spec: `alertRulesId`

Configuration query params: `api-version, filter, limit, offset, projection, serviceName, serviceTypology, sort`

Sample: [`samples/metering/alertrule.yaml`](../../samples/metering/alertrule.yaml) · [`alertrule-configuration.yaml`](../../samples/metering/alertrule-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Insight/alerts` | List Alerts |
| GET | `/projects/{projectId}/providers/Aruba.Insight/metrics` | List metrics |
