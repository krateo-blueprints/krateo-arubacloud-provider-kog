---
type: Configuration
title: Authentication
description: The short-lived Aruba JWT: how it is minted, stored and rotated.
tags: [aruba, kog]
timestamp: 2026-10-15T00:00:00Z
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
([#49](https://github.com/krateo-platformops/oasgen-provider/issues/49)); earlier
versions skipped the `apiKey` scheme silently and generated no auth block at all.

## A credential that is not a spec field

`DatabaseUser` needs a password. The source OAS declares it as a plain body string, and
a body string becomes a spec field — so the obvious generation puts a credential in the
CR, where anyone with `get databaseusers` can read it. Base64 does not help: it is an
encoding, not a protection.

The field is instead **dropped from the CRD** and supplied at create time by oasgen's
`secretRef` resolver:

```yaml
excludedSpecFields:
- password
verbsDescription:
- action: create
  method: POST
  path: /projects/{projectId}/providers/Aruba.Database/dbaas/{dbaasId}/users
  fieldMapping:
  - inBody: password
    resolver:
      type: secretRef
      secretRef:
        nameFromCustomResource: metadata.name
        keyFromCustomResource: spec.username
```

So the Secret is **named after the CR** and **keyed by the username**, and is read from
the CR's own namespace. Both paths point at fields the CR already has, which is the
whole trick: the resolver normally wants a `*SecretRef` field to read the name and key
from, and this OAS declares none — [and the OAS is not ours to
change](oas-patches.md). Reusing `metadata.name` and `spec.username` needs nothing
added.

What the pattern buys:

- The plaintext form no longer exists. There is no spec field to fill in by mistake,
  because the CRD has no such property — `kubectl explain` shows `configurationRef`,
  `projectId`, `dbaasId`, `username` and nothing else.
- RDC registers the resolved value as a **sensitive value**, so it is redacted from
  request logging rather than merely absent from the CR.
- RBAC is self-provisioned and narrow: RDC grants itself `get` on *that Secret name*,
  not on Secrets generally.

Two sharp edges, both load-bearing:

- **The stored value must be the base64 of the password**, because Aruba requires
  base64 and does not document it (their Terraform provider encodes client-side, at
  `internal/provider/dbaasuser_resource.go:130`). The Secret holds exactly the bytes
  the API wants.
- **`valueMapping` cannot do that encoding for you — on the version we pin.** A `jq`
  transform of `@base64` looks like the right answer and is silently discarded on RDC
  ≤ 0.24.0: it wires `jq` for the response direction only, and on a request it
  `continue`s past the entry rather than sending an untransformed value, so the field
  is simply missing from the body. You then debug the password, because what Aruba
  reports is a password-policy error.

  [oasgen#148](https://github.com/krateo-platformops/oasgen-provider/pull/148)
  implements the request direction for `inPath`, `inQuery` and `inBody`, and **shipped
  in 0.26.1**. So on 0.26.1 or later this Secret could hold the plaintext password and
  the RestDefinition could encode it — the better shape, since the operator then stores
  the value they actually chose.

  Both preconditions for that switch are now met, which was not true when this was
  first written:

  - **The redaction leak #148 opened is fixed and released.** With a resolver *and* a
    transform, the resolved value was registered as sensitive but the *transformed*
    value — the one that goes on the wire — was not, so request logging would redact
    the raw credential and print its base64. Worse than no redaction, because the log
    reads as audited. Fixed in `3184958`, which merged inside #148 and is therefore in
    0.26.1 as well.
  - Aruba's base64 requirement does not go away. It moves from the Secret's value into
    the RestDefinition.

  **We still have not made that switch**, but the reason has changed. It used to be
  that the pin (0.22.2) predated #148 and the generated RestDefinitions must work on
  the version we pin. The pin is now 0.36.0, which carries it — so the blocker is
  gone and only the work remains. It is deliberately not bundled into the bump
  commit: moving the base64 from the Secret's value into the RestDefinition changes
  what every existing DatabaseUser Secret must contain, and that deserves its own
  commit and its own live run rather than riding along with a version change.

> **Verified live:** the regenerated CRD carries no `password` property, the
> RestDefinition reaches `Ready=True` with the resolver attached, and the create call
> carries the resolved value — `DatabaseUser/ga-dbuser` reached `Ready` with
> `id=gauser` on four consecutive runs of `tests/ga/chains/database.yaml`, which also
> exercises the RBAC widening to the Secret's name, since the create could not have
> succeeded without it.

## Verbose connectors print the bearer token, below 0.26.1

Every sample in `samples/` carries:

```yaml
annotations:
  krateo.io/connector-verbose: 'true'
```

On a provider older than **0.26.1** that annotation dumps the whole HTTP exchange to
the controller's log, *including the request headers*:

```
GET /folders?api-version=1.0 HTTP/1.1
Authorization: Bearer eyJhbGciOiJSUzI1NiIsInR5cCIgOiAiSldUIi...
```

Measured on `kind-aruba-110` (provider 0.24.0) on 2026-10-20: **90** such lines in
`arubacloud-network-vpc-controller` alone, each one a live OAuth token. Pod logs are
routinely shipped to an aggregator that nothing has told about this, and the tokens
are valid for an hour from the moment they are printed.

[oasgen#166](https://github.com/krateo-platformops/oasgen-provider/pull/166) redacts
credential headers; it is first tagged in **0.26.1**, and the pin is now 0.36.0, so
anything validated by CI is safe. The exposure is real for anyone running these
samples on an older provider:

- **≥ 0.26.1** — keep the annotation; headers are redacted.
- **< 0.26.1** — remove `krateo.io/connector-verbose` before applying to anything you
  care about, or accept that the token is in your logs.

This is the second credential defect in the same area; the first was a transform that
registered the raw value as sensitive but not the transformed one, so logs printed the
base64 of a password and read as audited. Both argue the same thing: verbose request
logging and a credential in a header are a bad pair, and the version floor is the only
thing standing between them.

## RESTAction credentials

The CloudServer RESTActions run under Snowplow's identity and therefore use their
own **Endpoint** Secret (`restactions/compute/endpoint.yaml`) carrying the same
`server-url` + `token`. Keep it in sync with the token Secret above.


## Rotation: use External Secrets Operator

Aruba tokens last ~1 hour, so a static Secret is a dead end. The intended answer
is **ESO**, not a refresh loop inside oasgen/RDC — credential lifecycle is a
platform concern, and the pieces already compose:

- RDC re-reads the Secret **on every reconcile** (no cache), so a rotated value
  takes effect on its own — [verified on a live cluster](live-cluster-test.md#credential-rotation-works-without-a-restart-eso-is-viable).
- The Secret holds the **raw token only**. The `Bearer ` framing lives in the
  Configuration's `valuePrefix`, precisely so a rotation path "writes the raw
  credential and never has to know about HTTP framing" (oasgen's own words).

The manifests live in the repo rather than in this page, because an earlier sketch
here drifted from what actually works and cost an afternoon:

- `eso/clustersecretstore.yaml` — the webhook generator performing the
  client-credentials exchange
- `eso/externalsecret.yaml` — writes `default/arubacloud-token`, key `token`
- `scripts/eso-bootstrap.sh` — stores the client credential once; this is the only
  step that needs the client secret, and it prompts on the terminal so the value
  never reaches your shell history or a process listing

Four details the sketch got wrong, each of which fails without explaining itself:

- **`ClusterSecretStore`, not `SecretStore`.** The credential lives in
  `krateo-system` while the token Secret is written to `default`; a namespaced
  store cannot cross that boundary.
- **One `secrets` entry, addressed by field.** Two entries named `clientId` /
  `clientSecret` make `{{ .clientId }}` render the *whole Secret* as a Go map —
  `map[client_id:… client_secret:…]` — which Aruba rejects as `invalid_client`.
  Bind one `creds` entry and reference `{{ .creds.client_id }}`.
- **`| urlquery` on both values.** A secret containing `+` or `&` otherwise
  corrupts the form body.
- **Two mandatory labels on the credential Secret.**
  `reconcile.external-secrets.io/managed=true` (ESO's informer caches only
  labelled Secrets, so without it the lookup fails with a bare "not found" while
  the Secret plainly exists) and `external-secrets.io/type=webhook`. The bootstrap
  script applies both.

> **Verified so far:** on `kind-aruba-110`, ESO installs, both manifests are
> accepted, and the ExternalSecret reaches `SecretSyncedError: could not get secret
> data from provider` — the correct failure for a missing credential. The token
> mint itself is unverified, because that needs the client secret typed at a
> terminal. What is independently verified is the premise all of this depends on:
> rotation propagates without a restart.
