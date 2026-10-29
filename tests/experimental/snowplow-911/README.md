# snowplow #911 — an independent check of the stage-outcome contract

Test-only. Nothing here is applied by `scripts/pin_verify.sh`, referenced by a
RestDefinition, or shipped. It exists so a claim we make to the snowplow team is
reproducible by them rather than taken on our word.

## What it answers

snowplow#911 (in 1.12.57): a write stage whose `dependsOn` stage **failed or was
skipped** is not sent, and `X-Snowplow-Stage-Outcomes` reports it as `Skipped` with
its `dependsOn`.

Two fixtures, because the shipped one cannot exercise that:

| fixture | `server` stage | writes | expected header |
|---|---|---|---|
| `restactions/compute/cloudserver-delete.restaction.yaml` (shipped) | GET 200, filter matches nothing → **succeeded** | iterate zero items, `continueOnError: true` | `[]`, nothing sent |
| `restaction.yaml` (here) | GET the API answers **404** → **failed** | no `continueOnError` | `server: NotFound`, then two `Skipped` with `dependsOn: server` |

The shipped fixture tests that nothing **spurious** is sent. It does not test #911,
for three reasons that were not obvious and are worth keeping written down:

1. a GET that answers 200 and whose filter matches nothing has **succeeded** —
   "matched nothing" is not "failed";
2. its writes take their data through an **iterator** guarded on `$s.id != null`,
   so they succeed over zero items rather than being skipped;
3. all three stages carry `continueOnError: true`, which on a **write** means "send
   me even if my dependency did not succeed" — it disables the skip outright.

## Why no oasgen-provider

`/call` is answered by snowplow, so the provider and its generated controllers are
unrelated moving parts. They are also actively harmful here: 36 generated controllers
starting on one kind node saturate the node's systemd, and pods stick in
`ContainerCreating` with `Timeout waiting for systemd to create ... .slice`. The rig
installs authn, snowplow, the endpoint Secret, both RESTActions, one identity and a
curl pod — nothing else.

## Running it

```
SNOWPLOW_VERSION=1.12.57 \
SNOWPLOW_DIGEST="sha256:1a1a8ee9...328592b sha256:bff1e1e2...3b43519 sha256:1626598...c8c9bfd6" \
  ./snowplow_rig.sh

./call_probe.sh arubacloud-compute-cloudserver-delete ga-cs-server   # shipped fixture
./call_probe.sh snowplow-911-probe                    ga-cs-server   # this fixture
```

`SNOWPLOW_DIGEST` takes a **space-separated list** and passes if the running image
matches any of them. A multi-arch tag has an index digest and one manifest digest per
platform, and containerd's `imageID` reports the **index** digest even on arm64 — so
predicting which one the runtime will name costs a run. Pass them all.

The probe authenticates the way RDC does: an `audience: authn` projected token
exchanged at authn's `/serviceaccount/login`, not a privileged shortcut. It reads the
header from snowplow's own response rather than from a controller log, because a
controller decides what it logs.

## The identity, which is four things and not two

`*ApiRef` delegation — and therefore anything that calls `/call` — needs
`URL_SNOWPLOW`, `URL_AUTHN`, the audience-bound projected token mount, **and** a
`serviceaccount.authn.krateo.io` CR whose `spec.serviceAccountRef` matches the caller
plus RBAC for its groups. authn authors no RBAC. Filed upstream as
[oasgen-provider#262](https://github.com/krateo-platformops/oasgen-provider/issues/262).
