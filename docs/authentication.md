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

Aruba's specs declare **two different** security schemes, and oasgen generates a
different `authentication` block for each. The samples in `samples/` are derived
from each document's own scheme, so they already carry the right one.

### `http`/`bearer` — compute, database, schedule

```yaml
authentication:
  bearer:
    tokenRef: {name: arubacloud-token, namespace: default, key: token}
```

RDC sends `Authorization: Bearer <token>`.

### `apiKey` in header — network, container, security, storage, baremetal, project, metering

```yaml
authentication:
  apiKey:
    tokenRef: {name: arubacloud-token, namespace: default, key: token}
    header: Authorization      # defaulted by oasgen from the scheme's declared name
    valuePrefix: 'Bearer '     # NOTE the trailing space
```

RDC sends `Header.Set(header, valuePrefix + token)`.

> [!IMPORTANT]
> **`valuePrefix: 'Bearer '` — with the trailing space — is required.** OAS
> `apiKey` means "send this value verbatim", so oasgen deliberately does *not*
> default a prefix (a Secret already holding `Bearer x` would otherwise become
> `Bearer Bearer x`). But Aruba declares `apiKey` while expecting bearer framing,
> so without the prefix the header goes out as `Authorization: <token>` and every
> call 401s. Without the trailing space it becomes `Bearerxyz`, which fails
> identically to a wrong credential.

Requires oasgen ≥ 0.19.0 and RDC ≥ 0.19.0
([#49](https://github.com/braghettos/krateo-oasgen-provider/issues/49)); earlier
versions skipped the `apiKey` scheme silently and generated no auth block at all.

## RESTAction credentials

The CloudServer RESTActions run under Snowplow's identity and therefore use their
own **Endpoint** Secret (`restactions/compute/endpoint.yaml`) carrying the same
`server-url` + `token`. Keep it in sync with the token Secret above.
