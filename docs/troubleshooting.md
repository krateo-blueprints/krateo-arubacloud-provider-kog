---
type: Runbook
title: krateo-arubacloud-provider-kog — Troubleshooting
description: Common failures — RestDefinition not Ready, silent handleParam/delegated-delete breaks, auth and query-config issues — and how to diagnose them.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [troubleshooting, restdefinition, rdc, diagnose]
timestamp: 2026-08-11T00:00:00Z
---

# Troubleshooting

## RestDefinition never becomes `Ready`

```sh
kubectl get restdefinitions.ogen.krateo.io -A | awk 'NR==1 || /arubacloud/'
kubectl describe restdefinition <name> -n krateo-system
kubectl logs deploy/oasgen-provider -n krateo-system
```

Common causes:

- **Wrong provider image.** These RestDefinitions need the **krateo** fork
  (nested identifiers, `fieldMapping`, `*ApiRef`, …). A stock (non-krateo)
  oasgen-provider will reject or mis-handle them. Check the deployment image is
  `ghcr.io/krateo-blueprints/krateo-oasgen-provider`.
- **ConfigMap missing or in the wrong namespace.** `oasPath` is
  `configmap://krateo-system/arubacloud-<provider>-openapi/<provider>.json`; apply
  `configmaps/` into the **same namespace** oasgen-provider reads
  (`krateo-system` here).
- **OAS parse error.** If you referenced a raw `openapi/_source/` spec instead of
  the patched `openapi/` one, unsupported constructs (`nullable`, object
  `additionalProperties`) can break generation. Always reference the patched spec
  (that is what the ConfigMaps embed).

## A resource CR gets `401 Unauthorized`

- The token Secret is missing/expired, or `configurationRef` points at the wrong
  `<Kind>Configuration`. See [authentication](authentication.md).
- Aruba tokens are short-lived (~1h). Update `stringData.token` in the Secret.
- Ensure the token has **no** `Bearer ` prefix and no surrounding quotes.

## A resource is repeatedly re-created (findby never matches)

The reconciler decides existence via `findby` + identifiers. If it keeps creating
duplicates:

- **IDs must have no dashes.** The Aruba web UI shows resource IDs with dashes,
  but the API expects them **without**. Use the dash-free id form in
  `projectId`/`vpcId`/… (carried over from the original blueprint's known issue).
- **List envelope.** Aruba list responses wrap items in `{total, values:[…]}`;
  the controller must read `.values`. If a custom resource's findby never matches,
  confirm the response shape (evolution report §B3).
- **Identifier mismatch.** For metadata-wrapped resources the identifier is
  `metadata.name`; make sure `spec.metadata.name` is set (not a top-level `name`).

## The resource updates on every reconcile (false drift)

- **`readOnly` fields leaked into spec.** `patch_oas.py` strips `readOnly`, so
  server-managed fields (timestamps, counters) are writable and can be compared as
  drift. Move them out of your spec, or set `compareScope: identifiersAndStatus` on
  the RestDefinition to compare only identifiers + status.
- **`number`/`double` coercion.** Monetary/float fields are coerced to integer
  (evolution report §A4); avoid putting them in the desired spec.
- **Untyped maps.** `additionalProperties` objects became untyped maps
  (annotations/labels); shape differences can read as drift.

## Editing a RestDefinition fails with an immutability error

`resourceGroup`, `kind`, `identifiers`, `additionalStatusFields`,
`excludedSpecFields`, `configurationFields` are immutable. Delete and recreate the
RestDefinition (this recreates the generated CRD). See
[adding-a-resource](adding-a-resource.md#immutability).

## Delegated (`*ApiRef`) actions fail with "no snowplow client is configured"

RDC only enables RESTAction delegation when its `-snowplow-url` flag /
`URL_SNOWPLOW` env is set (empty = disabled), plus `URL_AUTHN` for authenticated
calls — and the chart's generated RDC deployment sets **neither**. Add them to
the RDC deployment's environment. Verified against RDC `main.go` and the chart's
`rdc/` assets ([adversarial review](adversarial-review.md) finding #3).

## A delegated delete hangs forever (finalizer never released)

**First check the RDC version — this is almost always version skew.** These
manifests require **RDC >= 0.18.0**, which forwards the CR spec on every
`*ApiRef` direction. On an older RDC, delete invocations receive no spec, so the
RESTAction's `.spec.projectId` is null, every guard skips, snowplow reports
success, RDC's existence check finds the resource still alive, and the finalizer
is never released. Nothing in the logs points at the version.

```sh
kubectl get deploy -n krateo-system -o jsonpath='{range .items[*]}{.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}' | grep rest-dynamic-controller
```

If it is below 0.18.0, upgrade to chart **0.9.19+** (the first release pairing
oasgen 0.18.0 with RDC 0.18.0), or override `rdc.image.tag` if you are pinned to
an older chart — see [prerequisites](../README.md#prerequisites). Otherwise, check the
RESTAction's jq against the contract in
[lifecycle-beyond-crud](lifecycle-beyond-crud.md#runtime-contracts-verified-against-rdc-source).

## Async polling never completes (or the RestDefinition is rejected)

- **Rejected at admission** with a poll-path message: oasgen >= 0.18.0 validates
  that `async.poll.path` is an exact key of the OAS **and** contains the
  `{handleParam}` token. Fix the path or declare the right `handleParam`.
- **Accepted but never polls**: the deployed RDC is older than 0.18.0, so
  `handleParam` is ignored and the handle binds to `operationId` — which the
  Aruba monitor path (`…/monitor/{id}`) does not contain. Same version check as
  above.

## CloudServer never converges

- Apply the RESTActions and their Endpoint Secret:
  `kubectl apply -f restactions/compute/`.
- The RESTActions assume the CR spec is reachable under `.spec` in the Snowplow
  context and that Aruba's action payloads match; both need validation on a live
  cluster (see [lifecycle-beyond-crud](lifecycle-beyond-crud.md#caveats)). Enable
  `krateo.io/verbose: "true"` on the RESTAction to inspect each step.
- Desired power state is assumed "on"; there is no `spec.powerState` field
  (evolution report §C1).

## Validate everything locally

```sh
python3 scripts/validate.py      # RD paths vs OAS, oasPath resolution, YAML, helm lint/template
```

If `helm` is missing, chart checks are skipped with a warning; install it with
`go install helm.sh/helm/v3/cmd/helm@latest`.
