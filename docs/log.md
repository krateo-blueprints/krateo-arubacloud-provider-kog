---
type: Log
title: krateo-arubacloud-provider-kog — log
description: Curated chronological history of the Aruba Cloud Provider KOG blueprint — notable changes and decisions, not a generated changelog.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [log, history]
timestamp: 2026-09-05T00:00:00Z
---

# Log

## 2026-09 — GA campaign: 15 resources proven against the live API

Drove resources through their real lifecycle on a live Aruba account rather than
reviewing them. 4 GA at the start, 15 at the end, with every promotion backed by a
recorded run and the account verified clean afterwards.

Six defects were found and fixed **upstream**, all of which had passed static review:
[#75](https://github.com/krateo-platformops/oasgen-provider/issues/75) read-only
resources materialised no identifier;
[#76](https://github.com/krateo-platformops/oasgen-provider/issues/76) an empty array in
a spec matched any remote array, so emptying a list was unenforceable;
[#77](https://github.com/krateo-platformops/oasgen-provider/issues/77) delete released
the finalizer before the resource was gone;
[#98](https://github.com/krateo-platformops/oasgen-provider/issues/98) the fix for it
turned a 404 into an infinite hang;
[#101](https://github.com/krateo-platformops/oasgen-provider/issues/101) the same hang
for any non-404 error — hit three separate times since;
[#106](https://github.com/krateo-platformops/oasgen-provider/issues/106) a dotted
identifier is emitted as a flat key while the controller reads it nested.

Four defects were **ours**, and each one orphaned or would have orphaned a real
resource: the status id field was assumed to be `id` when the response calls it `keyId`
(and `id` for its sibling, which the first fix then broke); `metadata`-wrapping was
derived from the create body when only the response can decide it, which would have made
`AlertRule` POST without bound; read-only resources assumed a flat item; and
`compareScope: updatable` was never applied where the update body is narrower than the
create body.

Infrastructure that now defends this: a CI gate that stands up a kind cluster and proves
34/34 RestDefinitions Ready plus 69/69 samples on every PR, weekly upstream OAS drift
detection, ESO minting Aruba tokens unattended (proven across ~29 hours), and a
declarative chain runner (`scripts/ga_chain.py`) whose teardown and residue checks are
verified rather than assumed.

Aruba's OpenAPI documents were consumed **unmodified** throughout. Roughly a dozen
constraints the API enforces are absent from the specs — `resourceType` must be exactly
`"volume"`, `Backup.type` is required, KaaS `preset` fails when a VPC exists, schedule
steps allow only `poweron`/`poweroff`, `K1A2` is documented but not orderable — every
one discoverable only by sending a request and reading the 400.

Curated, human-written history of notable changes and decisions. Newest first.
This is not a generated changelog — see the Git history and release tags for the
full record.

## 2026-08 — OKF documentation standard adopted

Adopted the Krateo Documentation Standard (OKF): the invariant core doc set
(`docs/{index,overview,usage,configuration,api,examples,release,log}.md` +
`docs/llms.txt`), OKF frontmatter added to every pre-existing doc, a runnable
`examples/cloudserver-environment/`, the six-section README, and a `lint-docs` CI
job. Part of krateo-platformops/installer#52.

## Proxy-free coverage — 34 resources, 10 providers

The blueprint replaced the predecessor's single-resource, Go-proxy design
(`subnet-plugin`) with declarative RestDefinitions covering all manageable Aruba
resources with **zero plugins**, using the krateo fork's nested identifiers,
`requestFieldMapping`, `fieldMapping`, `secretRef`, `async` and `*ApiRef`
delegation. Every load-bearing claim was
[adversarially verified](adversarial-review.md) against the fork's executor
source.

## CloudServer lifecycle via RESTAction delegation

`compute/CloudServer`'s multi-call, action-driven lifecycle
(create/update/delete) was solved without a proxy by delegating to idempotent
Snowplow RESTActions (`restactions/compute/`) through the fork's `*ApiRef`, with a
whole-environment Composition (`compositions/`) for coordinated provisioning. See
[Lifecycle beyond CRUD](lifecycle-beyond-crud.md).

## Fork pairing pinned to oasgen 0.18.0 / RDC 0.18.0 / chart 0.9.19

Pinned the minimum krateo-oasgen-provider chart to **0.9.19** — the first release
that pairs oasgen 0.18.0 with RDC 0.18.0. Earlier charts (≤ 0.9.18) ship RDC
0.16.1 against oasgen 0.18.0 and fail *silently* (`handleParam` ignored;
delegated deletes receive no spec). See [Troubleshooting](troubleshooting.md).
