---
type: Architecture
title: krateo-arubacloud-provider-kog — Authentication
description: The Aruba Bearer token Secret and the per-kind <Kind>Configuration, per-verb query config, and token rotation.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [authentication, secret, bearer-token, configuration-cr]
timestamp: 2026-08-11T00:00:00Z
---

# Authentication

Every Aruba Cloud call is authenticated with a **Bearer token** (a short-lived
JWT). Two Kubernetes objects wire it in — both are required.

## 1. The token Secret

Holds the raw Aruba JWT (no `Bearer ` prefix, no quotes). Generate the token per
<https://api.arubacloud.com/docs/authentication/>.

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: arubacloud-token
  namespace: default
type: Opaque
stringData:
  token: <YOUR_TOKEN>
```

Sample: [`samples/arubacloud-token-secret.yaml`](../samples/arubacloud-token-secret.yaml).

> Tokens are short-lived (≈1 hour by default). Rotation is the operator's
> responsibility — update the Secret's `token`; controllers pick up the new value
> on the next reconcile. Automated rotation is out of scope for this repo.

## 2. The `<Kind>Configuration`

oasgen-provider generates, alongside each resource CRD, a companion
**`<Kind>Configuration`** CRD (e.g. `SubnetConfiguration`). It carries:

- **authentication** — a reference to the token Secret;
- **configuration.query** — per-verb query parameters (chiefly `api-version`).

```yaml
apiVersion: arubacloud.ogen.krateo.io/v1alpha1
kind: SubnetConfiguration
metadata:
  name: subnet-config
  namespace: default
spec:
  authentication:
    bearer:
      tokenRef:
        name: arubacloud-token
        namespace: default
        key: token
  configuration:
    query:
      findby: {api-version: "1.0"}
      get:    {api-version: "1.0", ignoreDeletedStatus: false}
      create: {api-version: "1.0"}
      update: {api-version: "1.0"}
      delete: {api-version: "1.0"}
```

A single Configuration can be shared by many CRs of the same kind. The Secret may
live in a different namespace than the CR.

## 3. Reference it from the resource

```yaml
apiVersion: arubacloud.ogen.krateo.io/v1alpha1
kind: Subnet
metadata:
  name: example-subnet
spec:
  configurationRef:
    name: subnet-config
    namespace: default
  # …
```

## How the token reaches the API

The security scheme in every patched OAS is HTTP Bearer (`accessToken`), so the
RDC attaches `Authorization: Bearer <token>` to each request. In the raw Aruba
specs the scheme was declared as an `apiKey` header, which oasgen-provider would
not treat as a credential — `scripts/patch_oas.py` rewrites it (see
[oas-patches](oas-patches.md) §A7).

## RESTAction credentials

The CloudServer RESTActions run under Snowplow's identity and therefore use their
own **Endpoint** Secret (`restactions/compute/endpoint.yaml`) carrying the same
`server-url` + `token`. Keep it in sync with the token Secret above.
