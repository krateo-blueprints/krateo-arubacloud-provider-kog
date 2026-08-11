---
type: Log
title: krateo-arubacloud-provider-kog — log
description: Curated chronological history of the Aruba Cloud Provider KOG blueprint — notable changes and decisions, not a generated changelog.
resource: oci://ghcr.io/krateo-blueprints/charts/aruba-cloudserver-environment
tags: [log, history]
timestamp: 2026-08-11T00:00:00Z
---

# Log

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
