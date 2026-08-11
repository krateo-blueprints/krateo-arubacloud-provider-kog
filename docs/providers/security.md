---
type: Architecture
title: krateo-arubacloud-provider-kog — Aruba Cloud Security (KMS) provider
description: Security (KMS) provider reference — every resource, its verbs, endpoints, config and samples.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [providers, security, kms, reference]
timestamp: 2026-08-11T00:00:00Z
---

# Aruba Cloud Security (KMS) provider

- **OpenAPI**: `Aruba.SecurityProvider.Api` v1.0.0 (`openapi/_source/security.json` → patched `openapi/security.json`)
- **Security scheme (patched)**: `accessToken` (HTTP Bearer)
- **ConfigMap**: `arubacloud-security-openapi` in `krateo-system`
- **Resources**: 3


| Kind | Verbs | Identifier(s) | Delegation |
|------|-------|---------------|------------|
| `Key` | findby, get, create, update, delete | `name` | — |
| `Kmip` | findby, get, create, update, delete | `name` | — |
| `Kms` | findby, get, create, update, delete | `metadata.name` | — |


## Key

> Key create body is flat {name, algorithm}; item keyed by server id {keyId}.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/keys` |
| get | GET | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/keys/{keyId}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/keys` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/keys/{keyId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/keys/{keyId}` |

status fields: `id` · excluded from spec: `keyId`

Configuration query params: `api-version, filter, limit, offset, projection, sort`

Sample: [`samples/security/key.yaml`](../../samples/security/key.yaml) · [`key-configuration.yaml`](../../samples/security/key-configuration.yaml)

## Kmip

> Kmip create body is flat {name}; item keyed by server id {kmipId}.

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/kmip` |
| get | GET | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/kmip/{kmipId}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/kmip` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/kmip/{kmipId}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/kmip/{kmipId}` |

status fields: `id` · excluded from spec: `kmipId`

Configuration query params: `api-version, filter, includeDeleted, limit, offset, projection, sort`

Sample: [`samples/security/kmip.yaml`](../../samples/security/kmip.yaml) · [`kmip-configuration.yaml`](../../samples/security/kmip-configuration.yaml)

## Kms

| Verb | Method | Path |
|------|--------|------|
| findby | GET | `/projects/{projectId}/providers/Aruba.Security/kms` |
| get | GET | `/projects/{projectId}/providers/Aruba.Security/kms/{id}` |
| create | POST | `/projects/{projectId}/providers/Aruba.Security/kms` |
| update | PUT | `/projects/{projectId}/providers/Aruba.Security/kms/{id}` |
| delete | DELETE | `/projects/{projectId}/providers/Aruba.Security/kms/{id}` |

status fields: `metadata.id` · excluded from spec: `id`

Configuration query params: `api-version, filter, ignoreDeletedStatus, limit, offset, projection, sort`

Sample: [`samples/security/kms.yaml`](../../samples/security/kms.yaml) · [`kms-configuration.yaml`](../../samples/security/kms-configuration.yaml)

## Endpoints not exposed as a resource

Action-only, list-only or delegated endpoints (see [lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and [coverage](../coverage.md)):

| Method | Path | Summary |
|--------|------|---------|
| GET | `/projects/{projectId}/providers/Aruba.Security/kms/{id}/kmip/{kmipId}/download` | Download Kmip certificate |
