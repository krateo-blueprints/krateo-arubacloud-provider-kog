---
type: Runbook
title: RDC delegated-delete diagnosis
description: Why CloudServer's delegated delete never reached Aruba, what was ruled out, and the Debug decision table that discriminates dispatch from counting.
tags: [oasgen, rdc, snowplow, kog, experimental]
timestamp: 2026-10-29T00:00:00Z
---

# RDC delegated-delete diagnosis

A real `CSO1A2` was created live (`id=6ae3b9eb490b8f9ff0decb79`, Active in 61s) and
its delete **never reached Aruba**. From 3189 log lines of the generated controller:

```
GET    /projects/.../cloudServers[/{id}]   148
POST   /projects/.../cloudServers            1   (the create)
DELETE /                                     0
```

A hand-issued DELETE with the **same id and the same token read from the same cluster
Secret** returned `202` and the server was 404 ten seconds later. So the API accepts
it, the token is authorised, and the identifier is correct.

## What this is NOT — ruled out, don't re-check

| Hypothesis | Verdict |
|---|---|
| The `metadata.id` jq bug in the delete RESTAction | **not the cause.** The bug was real (`$s.id` yields `[]`, `$s.metadata.id` yields the id, verified against the live response shape) but the RESTAction **never executes**, so its jq never evaluates. It was fixed downstream of a path that is never walked. |
| oasgen#262 — missing delegation prerequisites | **no.** `URL_AUTHN` and `URL_SNOWPLOW` are in the generated ConfigMap, the `audience: authn` projected token is mounted at `/var/run/secrets/krateo.io/serviceaccount`, and the authn identity + `krateo-rdc` group RBAC exist. |
| The CRD pruned `deleteApiRef` on write | **no.** Stored (`kubectl get restdefinition … -o jsonpath='{.spec.resource.deleteApiRef}'` returns it) and declared by the installed CRD. |
| `cs.For(ctx, "delete")` failing before the delegated branch | **no.** That path calls `log.Error`; `Building REST client` is absent among 146 ERROR lines. |
| The getter matching the wrong RestDefinition, or dropping the field | **no** — oasgen verified both: it matches on group **and** kind, and marshals the whole `spec.resource`. |
| `mutateViaRestAction` returning nil without calling snowplow | **no** — `Resolve` is unconditional after the nil check. |

Every link checks out and the behaviour contradicts all of them. That is the point to
stop reading code and measure, because further hypotheses are plausible stories.

## The decision table

`analyse_debug.sh` applies oasgen-provider's line map for the 0.36.0 delegated delete
path. `:100` immediately precedes `:103`, so the pair discriminates:

| `:50` *Mutated via RESTAction* | `:100` *still present after delete* | Conclusion |
|---|---|---|
| yes | yes | branch entered, snowplow returned → dispatch works, the **counting** is suspect |
| **no** | **yes** | `resolveDeleteOutcome` ran without the mutation → the branch was **skipped**, dispatch is the defect |
| no | no | earlier than either → send the whole Debug dump, do not guess |

```sh
kubectl logs deploy/arubacloud-compute-cloudserver-controller -n krateo-system --tail=-1 > rdc.log
bash tests/experimental/rdc-delegation/analyse_debug.sh rdc.log
```

## Two things the script refuses to do

**It will not interpret an absence when Debug is off.** `Mutated via RESTAction` is a
DEBUG line. If the log has no DEBUG level, every "no" above is indistinguishable from
"the level was off", so the script exits 3 and renders no verdict.

That hole cost the first run its answer, and the cause was mine: `pin_verify.sh` has
carried `RDC_DEBUG` (default `0`) and `--set-string rdc.env.REST_CONTROLLER_DEBUG=true`
all along, with a comment I wrote about the `got boolean, want string` schema trap —
and I diagnosed this for an hour without remembering it existed. Run with
**`RDC_DEBUG=1`**.

**It will not trust that credentials are redacted.** `-debug` is *"dump verbose
output"*, so the log carries full request/response dumps. 0.36.0 is above the 0.26.1
fix that redacts credential headers by name, but on **0.24.0** this same combination
printed **90 live bearer tokens** in one controller's log. So the script greps for
`Bearer ey…` and requires **zero**, failing with exit 1 and telling you to treat the
log as a credential rather than attach it to an issue. "I didn't see a token" is a
different claim from "there is no token".

## The ConfigMap shortcut does not work

Patching `REST_CONTROLLER_DEBUG` into the generated ConfigMap by hand **is reverted**:
oasgen re-renders that asset and applies it every reconcile (`deploy.go:331`), so the
patch is drift. Measured: gone after ~90s; a second patch survived 60s, which makes
persistence a race rather than a mechanism. It has to go through `rdc.env` on the
chart. The patch route looks like it works, which is worse than failing outright.

## The verdict logic is tested

Six cases, no cluster needed — including the two that matter most: the decisive
`:100`-without-`:50` row, and a **planted credential leak** that must exit 1.

A `grep -c "$1" || echo 0` bug was caught this way: `grep -c` already prints `0` *and*
exits 1 when the count is zero, so the function returned `"0\n0"`, every integer test
errored, and the verdict fell through to "a combination the table does not cover" — on
the decisive row. The one answer this run exists to produce would have come back a
shrug.
