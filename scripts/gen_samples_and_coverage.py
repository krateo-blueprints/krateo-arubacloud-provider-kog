#!/usr/bin/env python3
"""Generate, from the generated RestDefinitions + patched OAS:
  * docs/coverage.md          - the full resource/verb coverage matrix
  * samples/<provider>/*.yaml - a <Kind>Configuration and a <Kind> CR skeleton
                                per resource (real field names, placeholder values)
  * samples/arubacloud-token-secret.yaml - the shared auth Secret example

CR skeletons intentionally only fill the fields we can derive faithfully from the
OAS (parent path parameters, the name, and the top-level create-body groups). The
nested `properties.*` are left as a stub pointing at the OAS, rather than invented.
"""
import json
import os
import re

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
OAS = os.path.join(ROOT, "openapi")
RD = os.path.join(ROOT, "restdefinitions")
SAMPLES = os.path.join(ROOT, "samples")
DOCS = os.path.join(ROOT, "docs")
GROUP = "arubacloud.ogen.krateo.io"


# The generated <Kind>Configuration CRD is always served at v1alpha1, regardless of
# the OAS info.version that drives the resource CRD.
CONFIG_VERSION = "v1alpha1"


# Support tier per resource, keyed (provider, Kind).
#
# A tier is EARNED BY EXECUTED EVIDENCE, never by how correct a RestDefinition looks.
# That rule exists because every serious defect this repo has found was invisible to
# review and surfaced only on a cluster. So the default is deliberately pessimistic:
# anything absent from this table is experimental, and promoting a resource means
# adding a row here with the evidence that justifies it.
#
#   ga           full create -> observe -> drift -> delete against the live Aruba API
#   beta         observe verified live; mutation unproven
#   experimental generated, statically valid, admitted by the API server
#   blocked      known non-functional, with the reason recorded
#
# WHERE THE API HAS NO UPDATE VERB, drift is INAPPLICABLE rather than unproven, and
# the GA bar is create -> observe -> delete, re-runnable from a chain. Nothing can
# diverge that a controller could correct, and RDC will never issue an update, so
# demanding a drift test would make a whole class of resources permanently
# un-GA-able for a property of Aruba's API rather than anything about this repo.
#
# That rule is written down because the table was inconsistent without it: KeyPair
# was GA on create/observe/delete with no update verb, while Database, DatabaseUser,
# Grant and DatabaseBackup sat at beta on the same verb shape. One of those readings
# had to be wrong. This is the one being adopted -- and it raises the bar in one
# respect, because "delete" now has to mean a delete VERIFIED AGAINST THE API while
# the resource's parent still exists. A CR that disappears as its parent is torn down
# proves nothing: a cascade and a DELETE look identical from Kubernetes.
#
# See docs/ga-readiness.md for what each tier claims about production use.
TIERS = {
    ("network", "Subnet"): (
        "ga",
        "full lifecycle live incl. drift correction, re-runnable on demand under "
        "`tests/ga/chains/network-security.yaml`. The chain declares `type: Advanced` "
        "with an explicit CIDR on purpose: `Basic` lets the CMP pick the address, "
        "which makes the declared value unmatchable — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "Vpc"): (
        "ga",
        "full lifecycle live incl. drift correction, re-runnable on demand under "
        "`tests/ga/chains/network-security.yaml` — [live-cluster-test](live-cluster-test.md)",
    ),
    ("compute", "KeyPair"): (
        "ga",
        "full lifecycle live, re-runnable on demand under "
        "`tests/ga/chains/compute-keypair.yaml`. No update verb, so drift is N/A; the "
        "GA claim had previously rested on prose rather than on a chain anything runs "
        "— [live-cluster-test](live-cluster-test.md)",
    ),
    ("project", "Project"): (
        "ga",
        "full lifecycle live incl. drift correction, re-runnable on demand under "
        "`tests/ga/chains/project-folder.yaml`. Takes no `projectId` — it IS the "
        "project — [live-cluster-test](live-cluster-test.md)",
    ),
    ("project", "Folder"): (
        "beta",
        "create/observe/delete proven live, now under "
        "`tests/ga/chains/project-folder.yaml`. Drift is **not** exercised and cannot "
        "be: the body is flat `{name, default}`, with no tags to perturb, a name that "
        "is the immutable identifier, and a `default` flag that decides account-wide "
        "where new projects land. Nothing is safe to change, so it stays beta — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("baremetal", "Hpc"): (
        "experimental",
        "**cannot be exercised on this account**: "
        "`GET /projects/{id}/providers/Aruba.Baremetal/hpcs` returns **404** "
        "(verified 2026-10-20, api-version 1.0 and 1.1), so the provider is not "
        "enabled here. Two reasons nothing was created to find out more: the OAS "
        "declares **no DELETE for hpcs at all** — only `post`, `get`, and PUTs for "
        "`name` and `automaticrenew` — so a bare-metal cluster created through this "
        "RestDefinition could not be removed through it, and bare metal is the one "
        "resource class here where an accident is not measured in cents",
    ),
    ("metering", "AlertRule"): (
        "experimental",
        "**cannot be exercised on this account**: "
        "`Aruba.Insight/alertRules` returns **404** on both api-version 1.0 and 1.1 "
        "(verified 2026-10-20) — a collection GET that 404s rather than returning an "
        "empty list — and `Aruba.Metering/alertRules` is refused as `Unknown "
        "Provider`. Its `meta` was also the one derivation corrected from the "
        "response rather than the create body across all 34: the POST is "
        "metadata-wrapped while both reads return a flat DTO, so the original "
        "`identifiers: [metadata.name]` could never match and the controller would "
        "have re-POSTed every reconcile, without bound, against a live account",
    ),
    ("network", "SecurityGroup"): (
        "ga",
        "full lifecycle live incl. drift correction, re-runnable on demand under "
        "`tests/ga/chains/network-security.yaml` — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "SecurityRule"): (
        "ga",
        "full lifecycle live incl. drift correction, re-runnable on demand under "
        "`tests/ga/chains/network-security.yaml`. Its drift probe read as impossible "
        "for a while — a 400 for every body shape tried — which was a probe bug, not "
        "a resource limitation: a write is refused with `{\"field\": \"Status\", "
        "\"message\": \"invalid status\"}` while the rule is still `Updating`, and the "
        "identical body succeeds once it is `Active` "
        "([async-readiness](async-readiness.md#every-write-is-refused-mid-transition-not-just-dbaass)) "
        "— [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "VpcPeering"): (
        "ga",
        "full lifecycle live incl. drift correction, free (no `billingPlan`), under "
        "`tests/ga/chains/peering-route.yaml` — drift block added there 2026-10-22, "
        "because that chain had none and this note was claiming coverage it did not "
        "have (the original drift evidence came from the older bash harness). Its "
        "delete is verified against the API "
        "at `.../vpcs/{id}/vpcPeerings/{id}` (→ 404) **while its VPC was still "
        "alive** — re-earned 2026-10-22 under the stricter rule, because this chain's "
        "residue list covers `/vpcs` only and a peering that vanished with its VPC "
        "would have looked identical to one the controller deleted — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "VpcPeeringRoute"): (
        "ga",
        "full lifecycle live in a 6-resource chain (two VPCs, two **Advanced** subnets "
        "whose CIDRs the route references, a peering) under "
        "`tests/ga/chains/peering-route.yaml`. Drift is now exercised and **corrected** "
        "(2026-10-22, `6ada2e75`) — the last count holding it at beta. It took three "
        "runs to get that verdict, and neither earlier failure was the resource's "
        "fault: the first had no drift block in the chain at all, and the second "
        "injected the drift, then lost the poll to a single "
        "`TimeoutError: The read operation timed out` that nothing caught. Delete is "
        "verified against the API at its own four-level path "
        "`.../vpcs/{id}/vpcPeerings/{id}/vpcPeeringRoutes/{id}` (→ 404) **while both "
        "the peering and the VPC were still alive**, which is the only way to tell a "
        "real DELETE from a cascade — **billable** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("schedule", "BackupPolicy"): (
        "ga",
        "full lifecycle live incl. drift correction — [live-cluster-test](live-cluster-test.md)",
    ),
    ("schedule", "BackupPolicyAssignment"): (
        "beta",
        "create → observe → **drift corrected** proven live (`6a9aea83`). **Delete "
        "stalls**: the first DELETE is accepted and the resource enters `Deleting`, and "
        "every subsequent DELETE returns 400 `Invalid status`.\n\n"
        "A correction to an earlier, stronger claim here. This note used to say delete "
        "was *proven not to complete*; the evidence does not carry that weight. The "
        "400s are the ordinary **state refusal** this API gives to any write on a "
        "resource mid-transition — the same shape that produced three wrong "
        "explanations for `VpnTunnel`'s drift before the right one turned out to be "
        "\"wait longer\" "
        "([async-readiness](async-readiness.md#every-write-is-refused-mid-transition-not-just-dbaass)). "
        "And the one run that could have settled it was spoiled by hand: when teardown "
        "stalled, the referenced BlockStorage was force-deleted out of dependency "
        "order, and an assignment cannot finish deleting once the volume it binds has "
        "vanished. That is what made `6a9aea83` permanent — residue of a mistake, not "
        "a demonstration of a defect.\n\n"
        "So what is actually established is narrower: delete **stalls**, and has never "
        "been observed to complete *with its dependency intact*. Whether it would, "
        "given patience, is **unknown** — and that is the one measurement that could "
        "still move this resource to GA.\n\n"
        "Two attempts to re-verify it on 2026-10-22 produced no new evidence, and the "
        "reason is worth recording because it is a provider defect rather than bad "
        "luck: `findby` matches by **name**, and the wedged resources hold the "
        "fixture's names. Both runs therefore **adopted a resource in `Deleting`** and "
        "reported it Ready — first the policy (whose dependent assignment then failed "
        "its update with 400), then, after only the policy had been renamed, the "
        "assignment itself (`delete of ga-bpa did not remove the external resource, "
        "which is still present`, reported while the resource was supposedly being "
        "created). Observing a resource on its way out of existence as healthy is a "
        "defect in its own right.\n\n"
        "`tests/ga/chains/backup-assignment.yaml` now uses distinct names and is "
        "deliberately **excluded from routine runs**: with create, observe and drift "
        "already proven and delete already proven broken, another run would buy no "
        "information and would leave a third permanently undeletable resource on the "
        "account. It is kept for testing a *fix* to the delete path — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("schedule", "Job"): (
        "ga",
        "full lifecycle live incl. drift correction and an API-verified delete, "
        "against a real CloudServer (2026-10-22). Run it with "
        "`scripts/ga_job_probe.py`, which creates that server out of band — a Job's "
        "only supported step actions are poweron/poweroff, so it needs one, and a "
        "RestDefinition cannot create a CloudServer "
        "([oasgen#108](https://github.com/krateo-platformops/oasgen-provider/issues/108)). "
        "A step is **`POST` with a RELATIVE `actionUri`** (`poweroff`); a full path is "
        "rejected as *not configured for this resource typology*, and the fixture "
        "carried a full path until now, so it had never created a Job at all.\n\n"
        "It was the last of the never-Ready three, and its cause was the server "
        "normalisation the Kaas investigation went looking for and did not find: "
        "`steps[].actionUri` is sent as `poweroff` and returned as `/poweroff` — the "
        "API refusing the leading slash on write and adding it on read — and "
        "`executeUntil` is sent as `…Z` and returned as `…+00:00`. Both keys are "
        "present on each side, so the drift comparison could never pass and the "
        "controller re-PUT the job on every resync: five accepted, no-op updates in "
        "one short run "
        "([oasgen#213](https://github.com/krateo-platformops/oasgen-provider/issues/213)). "
        "A `responseTransform` on the get verb normalises both before the comparison "
        "— **free** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("storage", "BlockStorage"): (
        "ga",
        "full lifecycle live incl. drift correction — **billable** (`billingPeriod`), "
        "run at 20 GB and deleted immediately — [live-cluster-test](live-cluster-test.md)",
    ),
    ("storage", "Snapshot"): (
        "ga",
        "full lifecycle live incl. drift correction — **billable** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("storage", "Backup"): (
        "ga",
        "full lifecycle live incl. drift correction — **billable** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("storage", "Restore"): (
        "ga",
        "create → observe → delete proven live in the storage chain. Drift is **not "
        "applicable**: its update body (`RestoreUpdatePropertiesDto`) is an empty "
        "schema with zero properties, so there is nothing to converge — the same bar "
        "as the resources with no update verb. Delete re-verified 2026-10-22 against "
        "the API at `.../backups/{id}/restores/{id}` (→ 404) **while the parent backup "
        "was still alive**: the residue list covers blockStorages, snapshots and "
        "backups but never restores, so this was the one claim in that chain resting "
        "on the parent being torn down afterwards — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("security", "Kms"): (
        "ga",
        "full lifecycle in one clean run incl. drift correction (`6a9ae569`) — "
        "**billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("security", "Key"): (
        "ga",
        "create → observe → delete proven live. Drift is **not applicable**: its "
        "update body is `{name}` only, and `name` is the identifier, so there is no "
        "non-identifying field to converge — the same reasoning as `Restore`. Delete "
        "re-verified 2026-10-22 against the API at `.../kms/{id}/keys/ga-key` (→ 404) "
        "**while the parent KMS was still alive**: this chain's residue list covers "
        "`/kms` only, so a key that vanished with its KMS would have been "
        "indistinguishable from one the controller deleted — **billable parent** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("security", "Kmip"): (
        "ga",
        "create → observe → delete proven live on the **released 0.27.1 stack** "
        "(2026-10-23, `b132fb62`) under `tests/ga/chains/security.yaml`, run via "
        "`scripts/pin_verify.sh`. Drift is **inapplicable**: `KmipNameDto` is `{name}` "
        "only and `name` is the identifier, so there is no non-identifying field to "
        "converge — the same bar as `Key` and `Restore`.\n\n"
        "It had never reported `Ready`, and the cause was a **name collision between a "
        "path parameter and a response field**. The path is `/kms/{id}/kmip/{kmipId}`, "
        "so the spec carries `id` meaning *the KMS*, while every response carries `id` "
        "meaning *the Kmip*:\n\n"
        "```\nCR spec  id = 6ada8934d94d05eaad355b01   (the KMS that owns it)\n"
        "response id = b132fb62-998d-479f-9c9d-...    (the Kmip itself)\n```\n\n"
        "One key, both sides, two unrelated identities — an unsatisfiable comparison, "
        "so the controller PUT `{\"name\":\"ga-kmip\"}` every 180s forever against a "
        "response that already said `name: ga-kmip`. That is the third distinct route "
        "into [oasgen#213](https://github.com/krateo-platformops/oasgen-provider/issues/213) "
        "found here, after `Kaas` (request/response **type** mismatch) and `Job` "
        "(server **normalisation**).\n\n"
        "Fixed with `compareScope: updatable`, and the choice of scope is the whole "
        "fix: `identifiersAndStatus` was tried first and changed nothing, because it "
        "projects the spec onto `identifiers + additionalStatusFields` and `id` **is** "
        "an additionalStatusField here — that is how the Kmip's own id reaches status "
        "to build `{kmipId}`. `updatable` derives its field set from the update body "
        "instead, which is the only one that drops `id` without breaking URL "
        "construction or the status mapping. A `responseTransform` cannot help: the "
        "response's `id` is what populates status.\n\n"
        "**Delete is a tombstone**, and the chain now accepts one as proof of absence: "
        "after a successful DELETE the resource vanishes from its own collection — "
        "including with `includeDeleted=true` — while `GET` on its id still answers "
        "**200**, with `status: Deleted` and a `deletionDate` a live read does not "
        "carry. Demanding a 404 there would report an unconfirmed delete forever. The "
        "CR's finalizer does release on 0.27.1, but by the *fallback* rather than by "
        "the feature: a `notFoundBody` predicate is declared on the get verb for "
        "exactly this, and on the delete path RDC passes it the whole "
        "`restclient.Response` struct instead of the body, so it errors and the delete "
        "is treated as unverifiable — where a successful DELETE is then trusted "
        "([oasgen#218](https://github.com/krateo-platformops/oasgen-provider/issues/218), "
        "one line, present since the feature shipped in 0.23.0 and in every release "
        "through 0.32.0). The predicate is correct and already does its job on the "
        "observe path; this repo's chain verifies the tombstone against the API "
        "directly, which is how the discrepancy surfaced — "
        "**billable parent** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("container", "KaasBackup"): (
        "beta",
        "create/observe/delete proven live under `tests/ga/chains/container.yaml` "
        "(`Ready, id=6ad7aea9`), which only became reachable once Kaas itself could "
        "report Ready. Two values the OAS cannot give you, both measured 2026-10-20: "
        "`type` accepts **only `Full`** — the API says so in an error attached to the "
        "wrong field, `('RetentionDays', 'Type is not valid. Allowed values are "
        "Full')` — and only **one backup may be in creation at a time** (`There is "
        "another backup in creation`), so backups cannot be created in parallel. "
        "**`billingPeriod` is not validated**: a probe created a backup with "
        "`billingPeriod: \"NotAPeriod\"` and the API persisted that string, then the "
        "backup went to `Failed` — almost certainly because of it, since the same "
        "resource with `Hour` provisions normally. Not GA, and the reason is now measured "
        "rather than missing: it has an `update` verb and "
        "`tests/ga/chains/container.yaml` carries a drift block for it, but the probe "
        "cannot inject. A backup refuses every write while `InCreation` "
        "(`{fieldName: Status, errorMessage: Invalid status}`) and it stayed "
        "`InCreation` for over 25 minutes, outlasting a 900s settle wait plus four "
        "retries. Stretching that window until it passes would buy a drift test nobody "
        "will re-run, so the window is recorded instead — **billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("container", "Registry"): (
        "experimental",
        "create is **accepted** (201) with `size: Small` — and `size` is why no "
        "Registry had ever been created here: the chain sent `\"20\"`, refused with "
        "`'20' is not a valid size`. Measured 2026-10-20, the legal values are "
        "`Small`, `Medium`, `HighPerf`, case-sensitive, established by POSTing each "
        "candidate with a deliberately nonexistent volume uri so nothing could be "
        "created. The OAS cannot tell you this: the create DTO describes the field as "
        "a connection count, the response DTO as \"the id of registry size\", and the "
        "`RegistrySize` catalogue it defines is referenced by no path. "
        "**It still does not provision**: the registry sits `InCreation` for 1818s "
        "and then goes to `Failed`, and the status carries no `failureReason` or "
        "`error` — the API says only `Failed`. The account is a TRIAL "
        "(`legacy_profilename: TRIAL`), the obvious suspect, but that is **not** "
        "established: `Kaas` is the same container provider and did reach `Active` on "
        "this same account, so \"container is unavailable on trial\" is contradicted "
        "by evidence. Cause unestablished. It carries the async gate anyway, which is "
        "what makes the failure legible instead of a 30-minute hang — "
        "`failureValues: [Failed]` is now confirmed live. A `Failed` registry also "
        "needs its DELETE issued **twice**; the first returns 202 and leaves it in "
        "place — **billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("container", "Kaas"): (
        "ga",
        "full lifecycle live incl. drift correction (K2A4 / 1.33.2), under "
        "`tests/ga/chains/container.yaml`. It spent this whole campaign at beta "
        "because the CR never reached `Ready`, and the cause was in Aruba's spec, not "
        "in ours: `nodePools[].instance` and `nodePools[].dataCenter` are **strings** "
        "in `NodePoolsDto` (what writes send) and **objects** in "
        "`NodePoolsResponseDto` (what reads return). RDC compares the CR's keys "
        "against the response, so a key present on both sides with a different type "
        "is permanent drift — and no `compareScope` avoids it, because "
        "`KaasUpdatePropertiesDto` declares only `nodePools`, which is the field that "
        "differs. The symptom was not just a stuck condition: the controller PUT a "
        "healthy cluster every 180s forever, each accepted write driving it from "
        "`Active` into `Updating`, which then refused the next one "
        "([oasgen#213](https://github.com/krateo-platformops/oasgen-provider/issues/213)). "
        "Fixed here with a `responseTransform` on the get verb — whole-document gojq "
        "applied before the comparison — folding each object back to its `.name`. "
        "`nodeCidr` (returned as `nodecidr`) and `securityGroup.name` (returned as "
        "`uri`) need no handling: those keys are absent from the response, so the "
        "comparison skips them. Four undeclared create constraints also apply: "
        "`preset: false` where the location already has a VPC, `nodeCidr` required "
        "though the OAS marks it optional, a **new** `securityGroup.name` (the "
        "cluster makes its own), and `K2A4` as the smallest orderable flavour — "
        "`K1A2` is listed in Aruba's metadata but is not orderable — **billable** "
        "~EUR 0.026/hr worker — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "Dbaas"): (
        "ga",
        "full lifecycle live incl. drift correction (`6a9a846d`, mysql-8.0 / DBO1A2 / "
        "20 GB), under `tests/ga/chains/database.yaml`. Two things had to be fixed "
        "before the drift test could even run: `Ready` is now gated on `status.state: "
        "Active` (it used to fire while Aruba still had the cluster `InCreation`), and "
        "the update body must be in **request** shape — the GET returns "
        "`networking.vpc.uri` where the PUT wants `networking.vpcUri`, so echoing the "
        "response back reads as nulling Vpc and earns `400 \"The field 'Vpc' cannot be "
        "modified\"` for fields the caller never touched. Two earlier notes here "
        "blamed the update body for refusing a full re-PUT; both were wrong "
        "— **billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "DatabaseBackup"): (
        "ga",
        "create/observe/delete proven live across five consecutive chain runs. Needed "
        "`identifiers: [metadata.id]` and `compareScope: identifiersAndStatus`: the "
        "backup is keyed by a server-assigned id, so the default `fullSpec` comparison "
        "looped forever on fields it cannot send and `Ready` was unreachable. Both "
        "counts that held it at beta are now closed. **id-keyed findby is proven "
        "against a list** (2026-10-21): a second backup was created beside this one "
        "and the CR held its own id across ten consecutive observes with two backups "
        "in the project — before that, \"matched correctly\" and \"returned the only "
        "item\" were indistinguishable. And drift is **inapplicable**, not unproven: "
        "the OAS gives backups no update verb. Delete is verified against the API with "
        "the parent Dbaas still alive, because teardown removes the backup first and "
        "the project-wide `/backups` endpoint is in the chain's residue list — "
        "**billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "Database"): (
        "ga",
        "create/observe/delete proven live, name-keyed (`status.name = gadb`). Drift "
        "is **inapplicable**: the OAS gives it no update verb. Its delete used to rest "
        "on the parent being torn down afterwards, which cannot tell a cascade from a "
        "DELETE; it is now verified against the API at its own URL "
        "(`.../dbaas/{id}/databases/gadb` → 404) **while the Dbaas was still alive** — "
        "**billable parent** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "DatabaseUser"): (
        "ga",
        "create/observe/delete proven live (`gauser`). The password is **not a spec "
        "field**: it is sourced from a Secret named after the CR and keyed by the "
        "username, via the `secretRef` resolver "
        "([authentication](authentication.md#a-credential-that-is-not-a-spec-field)). "
        "Its stored value must be **base64-encoded** — undocumented, which makes a "
        "plaintext value read as a policy rejection that it is not. Drift is "
        "**inapplicable**: no update verb (a password change is a separate "
        "`PUT .../password` sub-endpoint). Delete is verified against the API at "
        "`.../dbaas/{id}/users/gauser` (→ 404) **while the Dbaas was still alive**, so "
        "a cascade cannot account for it. An earlier note here said the create had not "
        "been re-run since the password moved out of the spec; that is long out of "
        "date — it reaches `Ready, id=gauser` on every run of "
        "`tests/ga/chains/database.yaml` — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "Grant"): (
        "ga",
        "create/observe/delete proven live once its identifier was bound to the leaf "
        "`user.username` — binding the object `user` put `map[username:gauser]` in the "
        "URL. Drift is **inapplicable**: no update verb. Delete is verified against "
        "the API at `.../users/gauser/grants/gadb` (→ 404) **while the Dbaas was still "
        "alive** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "VpnTunnel"): (
        "ga",
        "full lifecycle live incl. drift correction (Site-To-Site / ikev2), under "
        "`tests/ga/chains/vpn.yaml`. Its drift injection used to be rejected with a "
        "400 and three successive explanations were offered for it, two of them wrong. "
        "The cause was the same state refusal as SecurityRule's: this tunnel is still "
        "`InCreation` for longer than the probe's settle wait, so the write has to be "
        "retried rather than reinterpreted "
        "([async-readiness](async-readiness.md#every-write-is-refused-mid-transition-not-just-dbaass)). "
        "Nothing about the resource or its body needed changing — **billable** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "VpnRoute"): (
        "ga",
        "full lifecycle live incl. drift correction (`6a9bf909`). `cloudSubnet` must be "
        "the CIDR of a **standalone, Active** subnet in the VPC — never the tunnel's own "
        "provisioning subnet — a rule found in Aruba's CLI e2e, not in the OAS — "
        "**billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "ElasticIp"): (
        "ga",
        "full lifecycle live incl. drift correction — **billable** "
        "(`billingPlan.billingPeriod`), created and deleted within one hour — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "LoadBalancer"): (
        "blocked",
        "identifier corrected to `metadata.name` (its findby items are "
        "metadata-wrapped). The selector half of this is **fixed**: it used to be "
        "emitted as a flat key while RDC reads it nested "
        "([oasgen-provider#106](https://github.com/krateo-platformops/oasgen-provider/issues/106), "
        "shipped in 0.23.0), and since the pin moved to 0.27.1 the sample emits "
        "`metadata: {name: ...}` and can match. Still blocked on the other half, which "
        "no version fixes: no load balancer can be created, because the API has no "
        "create verb and one exists only as a side effect of a Service of type "
        "LoadBalancer inside a KaaS cluster",
    ),
    ("compute", "CloudServer"): (
        "beta",
        "**observe proven live** (2026-10-20): a server was created directly over the "
        "API and the CR converged on it, reporting back the same id the POST returned "
        "(`6ad732b8`), which exercises the only two verbs it declares — `findby` and "
        "`get`. Before this the sample had only ever been applied against an empty "
        "collection, which proves nothing. Run it with "
        "`scripts/ga_cloudserver_observe.py`.\n\n"
        "Its create is **proven working** but not reachable from a RestDefinition: a "
        "single `POST` at `api-version=1.1` returns 201 with "
        "vpc/subnets/securityGroups/bootVolume/elasticIp in one call, and that "
        "document is a second OAS — "
        "[oasgen-provider#108](https://github.com/krateo-platformops/oasgen-provider/issues/108). "
        "Undeclared constraints the OAS does not state: the boot volume must be "
        "`bootable: true` **with** an image, and `securityGroups` is **required** "
        "(`GenericResource: securityGroup cannot be null`) though the request schema "
        "marks nothing required.\n\n"
        "**Delete needs Snowplow.** This RestDefinition routes delete through a "
        "RESTAction (`deleteApiRef`), so on a cluster without a Snowplow client the "
        "CR cannot be deleted at all — every attempt logs `no snowplow client is "
        "configured` and the CR wedges on its finalizer while the remote server is "
        "long gone. That is why this is beta and not GA — **billable** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
}

TIER_DEFAULT = ("experimental", "applies and reaches `Ready`; sample admitted by its CRD")

TIER_BADGE = {
    "ga": "**GA**",
    "beta": "beta",
    "experimental": "experimental",
    "blocked": "**blocked**",
}


def tier_for(prov, kind):
    return TIERS.get((prov, kind), TIER_DEFAULT)


def crd_version(spec):
    """The RESOURCE CRD's version is DERIVED FROM THE OAS `info.version`: oasgen
    calls crdgen.NormalizeVersionName(doc.Version()), turning 1.0.0 -> v1-0-0 and
    1.0 -> v1-0. The companion <Kind>Configuration CRD is NOT versioned that way --
    it is always v1alpha1 (see CONFIG_VERSION below).

    Both facts were established on a live cluster; samples that hardcoded v1alpha1
    for the resource were rejected with "no matches for kind ... in version".
    """
    v = (spec.get("info") or {}).get("version") or ""
    return "v" + v.replace(".", "-") if v else "v1alpha1"
# short provider name -> published filename (consumed UNMODIFIED)
FILES = {'network': 'network-provider.json',
         'compute': 'compute-provider.json',
         'container': 'container-provider.json',
         'database': 'database-provider.json',
         'storage': 'storage-provider.json',
         'security': 'security-provider.json',
         'schedule': 'schedule-provider.json',
         'baremetal': 'baremetal-provider.json',
         'project': 'project.json',
         'metering': 'metering.json'}



def deref(spec, node, seen=None):
    if seen is None:
        seen = set()
    if isinstance(node, dict) and "$ref" in node:
        r = node["$ref"]
        if r in seen:
            return {}
        cur = spec
        for p in r.lstrip("#/").split("/"):
            cur = cur.get(p, {})
        return deref(spec, cur, seen | {r})
    return node


def merged_props(spec, sch):
    sch = deref(spec, sch)
    props, req = {}, []
    if isinstance(sch, dict):
        for s in sch.get("allOf", []):
            p, r = merged_props(spec, s)
            props.update(p); req += r
        props.update(sch.get("properties") or {})
        req += sch.get("required", [])
    return props, req


def placeholder(spec, sch):
    sch = deref(spec, sch)
    t = sch.get("type")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    if "enum" in sch:
        return sch["enum"][0]
    if t == "integer":
        return 0
    if t == "number":
        return 0
    if t == "boolean":
        return False
    if t == "array":
        return []
    if t == "object":
        return {}
    return "REPLACE"


def load_rds():
    out = []
    for prov in sorted(os.listdir(RD)):
        d = os.path.join(RD, prov)
        if not os.path.isdir(d):
            continue
        for fn in sorted(os.listdir(d)):
            doc = yaml.safe_load(open(os.path.join(d, fn)))
            out.append((prov, doc))
    return out


def path_params(path):
    return [m for m in re.findall(r"\{([^}]+)\}", path)]


def create_body_props(spec, create_path):
    op = spec["paths"].get(create_path, {}).get("post")
    if not op:
        return {}, []
    rb = deref(spec, op.get("requestBody", {}))
    for cv in (rb.get("content") or {}).values():
        return merged_props(spec, cv.get("schema", {}))
    return {}, []


def auth_block(spec):
    """Build the `authentication` block the generated <Kind>Configuration expects,
    derived from the document's OWN security scheme (oasgen >= 0.19.0):

      type: http, scheme: bearer  -> authentication.bearer
      type: apiKey, in: header    -> authentication.apiKey {tokenRef, header, valuePrefix}

    The apiKey shape carries the header name (oasgen defaults it from the scheme
    when the document declares exactly one) and a valuePrefix that RDC prepends to
    the credential: `req.Header.Set(header, valuePrefix+token)`.

    Aruba declares `apiKey` in an `Authorization` header but expects BEARER framing,
    so valuePrefix must be "Bearer " -- with the trailing space. oasgen deliberately
    does not default it (a Secret already holding "Bearer x" would become
    "Bearer Bearer x"), so it is set explicitly here. Leaving it empty sends the raw
    token and every call 401s.
    """
    ref = {"name": "arubacloud-token", "namespace": "default", "key": "token"}
    for sch in (spec.get("components", {}).get("securitySchemes") or {}).values():
        if sch.get("type") == "http" and sch.get("scheme") == "bearer":
            return {"bearer": {"tokenRef": ref}}
        if sch.get("type") == "apiKey" and sch.get("in") == "header":
            return {"apiKey": {
                "tokenRef": ref,
                "header": sch.get("name", "Authorization"),
                "valuePrefix": "Bearer ",
            }}
    return {"bearer": {"tokenRef": ref}}


def make_configuration(kind, verbs, auth, apiver, cfg_fields):
    """Build a <Kind>Configuration.

    The per-verb query block is derived from the RestDefinition's OWN
    configurationFields, never hardcoded: a query parameter is only a valid field
    here if that resource declared it. Hardcoding `ignoreDeletedStatus` on every
    `get` was rejected by the API server as an unknown field on the 18 resources
    whose endpoints do not declare it (strict decoding).

    Only `api-version` is given a value -- it is the one parameter every Aruba
    operation requires. The other declared params (filter/sort/limit/offset/
    projection/ignoreDeletedStatus/...) are optional; add them per verb as needed.
    """
    applies = {}
    for c in cfg_fields:
        src = c.get("fromOpenAPI", {})
        if src.get("in") != "query" or src.get("name") != "api-version":
            continue
        for a in c.get("fromRestDefinition", {}).get("actions", []):
            for v in (verbs if a == "*" else [a]):
                applies.setdefault(v, {})["api-version"] = "1.0"
    q = {v: applies[v] for v in verbs if v in applies}
    return {
        "apiVersion": apiver,
        "kind": f"{kind}Configuration",
        "metadata": {"name": f"{kind.lower()}-config", "namespace": "default"},
        "spec": {
            "authentication": auth,
            "configuration": {"query": q},
        },
    }


def make_cr(spec, prov, doc, apiver):
    res = doc["spec"]["resource"]
    kind = res["kind"]
    create = next((v for v in res["verbsDescription"] if v["action"] == "create"), None)
    meta = any(i == "metadata.name" for i in res.get("identifiers", []))
    cr = {
        "apiVersion": apiver,
        "kind": kind,
        "metadata": {"name": f"example-{kind.lower()}", "namespace": "default",
                     "annotations": {"krateo.io/connector-verbose": "true"}},
        "spec": {"configurationRef": {"name": f"{kind.lower()}-config", "namespace": "default"}},
    }
    spec_body = cr["spec"]
    excluded = set(res.get("excludedSpecFields", []))
    if create:
        for pp in path_params(create["path"]):
            if pp not in excluded:
                spec_body[pp] = "REPLACE"
        props, req = create_body_props(spec, create["path"])
        for name, sch in props.items():
            # excludedSpecFields applies to BODY properties too, not just path params.
            # It used to be checked only on the latter, so a field dropped from the CRD
            # was still emitted here -- the sample then failed strict decoding with
            # `unknown field`. DatabaseUser.password is the live case: it is sourced
            # from a Secret and no longer exists in the spec at all.
            if name in excluded:
                continue
            schd = deref(spec, sch)
            if name == "metadata":
                mp, _ = merged_props(spec, schd)
                m = {}
                for mn, ms in mp.items():
                    m[mn] = placeholder(spec, ms) if mn != "name" else f"example-{kind.lower()}"
                spec_body["metadata"] = m
            elif name == "properties":
                spec_body["properties"] = {"__doc__": f"fill from openapi/{prov}.json"}
            else:
                spec_body[name] = placeholder(spec, sch)
    else:  # read-only
        for v in res["verbsDescription"]:
            for pp in path_params(v["path"]):
                if pp not in excluded:
                    spec_body[pp] = "REPLACE"
        # Emit the identifier as a SELECTOR. A read-only resource has no create body,
        # so path parameters alone leave the user unable to say WHICH object is meant --
        # the sample applies cleanly and matches nothing. oasgen 0.22.1 generates the
        # selector field (#75); this makes the sample actually use it.
        for ident in res.get("identifiers", []):
            # Nested-aware presence test: now that `metadata.name` is written as a
            # nested object, `ident in spec_body` would never see an existing one and
            # the selector would overwrite a real value with the placeholder.
            node = spec_body
            for part in ident.split("."):
                node = node.get(part) if isinstance(node, dict) else None
            if ident in excluded or node is not None:
                continue
            # A dotted identifier becomes a NESTED object: `metadata.name` is written
            # as `metadata: {name: ...}`, which is how RDC has always read it
            # (spec.metadata.name).
            #
            # It used to be emitted FLAT -- one property whose name contains a dot --
            # because that is what oasgen 0.22.2 emitted, and that was a defect, not a
            # design: the flat key could never match. Filed as oasgen-provider#106,
            # CLOSED COMPLETED 2026-09-09, fix shipped in 0.23.0 (c306d86).
            #
            # MEASURED, so nobody has to rediscover it:
            #   oasgen 0.22.2  flat is correct    (the old pin)
            #   oasgen 0.24.0  NESTED is correct  (kind-aruba-110)
            #   oasgen 0.27.1  NESTED is correct  (the pin now)
            #   oasgen 0.30.0  NESTED is correct  (kind-aruba-030)
            # Above 0.22.x the flat form is refused as `unknown field
            # "spec.metadata.name"` -- a message that reads identically whether the key
            # was flat or nested, which is exactly how it could be inverted by someone
            # reading only the error. So LOWERING the pin back under 0.23.0 means
            # flipping this back, in the same commit, and regenerating.
            #
            # The two affected samples are network/loadbalancer and compute/cloudserver
            # -- the only resources whose selector is dotted.
            sel = "REPLACE_selects_the_existing_object_by_this_value"
            node, parts = spec_body, ident.split(".")
            for part in parts[:-1]:
                node = node.setdefault(part, {})
                if not isinstance(node, dict):
                    break
            if isinstance(node, dict):
                node[parts[-1]] = sel
    return cr


def secret_docs_for(doc):
    """Companion Secret samples for every create-body field sourced via a secretRef
    resolver. Without these the CR sample is unusable: the field is gone from the spec
    (that is the point) and nothing on the page says where the value comes from.

    The resolver reads the Secret's name and key from JSONPaths into the CR, so the
    sample can only be written by following those paths -- which is also the clearest
    way to show the convention to a reader."""
    res = doc["spec"]["resource"]
    out = []
    for v in res.get("verbsDescription", []):
        for m in v.get("fieldMapping", []) or []:
            r = (m.get("resolver") or {})
            if r.get("type") != "secretRef":
                continue
            sr = r["secretRef"]
            out.append((m.get("inBody") or m.get("inPath") or m.get("inQuery"),
                        sr["nameFromCustomResource"], sr["keyFromCustomResource"]))
    return out


def dump_yaml(obj, path):
    # render the __doc__ stub as a YAML comment-ish placeholder
    text = yaml.safe_dump(obj, sort_keys=False, default_flow_style=False, width=100)
    text = text.replace("__doc__: ", "# ")
    with open(path, "w") as f:
        f.write(text)


def main():
    os.makedirs(SAMPLES, exist_ok=True)
    # shared auth secret
    secret = {
        "apiVersion": "v1", "kind": "Secret",
        "metadata": {"name": "arubacloud-token", "namespace": "default"},
        "type": "Opaque", "stringData": {"token": "REPLACE_WITH_ARUBA_JWT"},
    }
    with open(os.path.join(SAMPLES, "arubacloud-token-secret.yaml"), "w") as f:
        f.write("# Aruba Cloud Bearer token (see https://api.arubacloud.com/docs/authentication/).\n")
        f.write("# The token is short-lived; rotate it as needed.\n")
        yaml.safe_dump(secret, f, sort_keys=False)

    specs = {p: json.load(open(os.path.join(OAS, fn))) for p, fn in FILES.items()}

    rows = []
    for prov, doc in load_rds():
        res = doc["spec"]["resource"]
        kind = res["kind"]
        verbs = [v["action"] for v in res["verbsDescription"]]
        d = os.path.join(SAMPLES, prov)
        os.makedirs(d, exist_ok=True)
        apiver = f"{GROUP}/{crd_version(specs[prov])}"
        dump_yaml(make_configuration(kind, verbs, auth_block(specs[prov]), f"{GROUP}/{CONFIG_VERSION}",
                                     res.get("configurationFields", [])),
                  os.path.join(d, f"{kind.lower()}-configuration.yaml"))
        cr_path = os.path.join(d, f"{kind.lower()}.yaml")
        dump_yaml(make_cr(specs[prov], prov, doc, apiver), cr_path)
        srefs = secret_docs_for(doc)
        if srefs:
            cr = make_cr(specs[prov], prov, doc, apiver)
            with open(cr_path, "a") as f:
                for field, name_path, key_path in srefs:
                    f.write(f"""---
# Supplies spec-absent field `{field}` at create time. `{field}` is deliberately not a
# spec field: a credential in a CR spec is readable by anyone who can read the CR.
#
# name <- {name_path}   key <- {key_path}
# Both are JSONPaths into the CR above, and the Secret is always read from the CR's own
# namespace. Change either value in the CR and the Secret must follow.
apiVersion: v1
kind: Secret
metadata:
  name: replace-matching-{name_path.replace('.', '-').lower()}
  namespace: default
type: Opaque
stringData:
  REPLACE_matching_{key_path.replace('.', '_')}: REPLACE_WITH_THE_VALUE
# The API may want this value in a particular encoding -- check the resource's page
# under docs/providers/ before assuming it takes the raw value.
""")
        ids = ",".join(res.get("identifiers", []))
        rows.append((prov, kind, ",".join(verbs), ids))

    rows.sort()
    with open(os.path.join(DOCS, "coverage.md"), "w") as f:
        # The frontmatter is part of the generated output, not decoration: the
        # lint-docs CI job requires it, so emitting the body alone means every
        # regeneration silently breaks the docs gate.
        f.write(
            "---\n"
            "type: API\n"
            "title: Coverage\n"
            "description: Every provider, resource and verb this repo covers, "
            "generated from the RestDefinitions.\n"
            "tags: [aruba, kog]\n"
            "timestamp: 2026-09-28T00:00:00Z\n"
            "---\n\n"
        )
        f.write("# Aruba Cloud KOG — resource coverage matrix\n\n")
        f.write("Generated by `scripts/gen_samples_and_coverage.py` from the "
                "RestDefinitions under `restdefinitions/`. No proxy/plugin is used "
                "for any resource.\n\n")
        f.write(f"**{len(rows)} manageable resources** across 10 providers.\n\n")

        counts = {}
        for prov, kind, _, _ in rows:
            counts[tier_for(prov, kind)[0]] = counts.get(tier_for(prov, kind)[0], 0) + 1

        f.write("## Support tiers\n\n")
        f.write(
            "A tier states what has actually been *executed* against the live Aruba API, "
            "not how correct a RestDefinition looks. Every serious defect found in this "
            "repository was invisible to review and surfaced only on a cluster, so a "
            "resource is only promoted when there is evidence to link.\n\n"
        )
        f.write("| Tier | Bar | Count |\n|------|-----|-------|\n")
        f.write("| **GA** | full `create → observe → drift → delete` against the live API | "
                f"{counts.get('ga', 0)} |\n")
        f.write("| beta | observe verified live; mutation unproven | "
                f"{counts.get('beta', 0)} |\n")
        f.write("| experimental | generated, valid, reaches `Ready`, sample admitted | "
                f"{counts.get('experimental', 0)} |\n")
        f.write("| **blocked** | known non-functional, reason recorded | "
                f"{counts.get('blocked', 0)} |\n\n")
        f.write(
            "Where the API has **no update verb**, drift is *inapplicable* rather than "
            "unproven and the GA bar is `create → observe → delete`, re-runnable from "
            "a chain: nothing can diverge that a controller could correct. That rule "
            "is stated because the table was inconsistent without it — `KeyPair` was "
            "GA on that basis while `Database`, `DatabaseUser`, `Grant` and "
            "`DatabaseBackup` sat at beta on the same verb shape. It also raises the "
            "bar in one respect: *delete* must be **verified against the API while the "
            "resource's parent still exists**, because from Kubernetes a cascade and a "
            "real DELETE look identical.\n\n")
        f.write("Only **GA** claims fitness for production use. See "
                "[ga-readiness](ga-readiness.md).\n\n")
        # Which stack the evidence came from is part of the claim. Recording it
        # because it was missing: the tiers above were measured on local builds
        # (`local/oasgen-provider:108-live`, `local/rest-dynamic-controller:110-test`)
        # while CI pins 0.27.1 and that is what the install docs tell users to use.
        f.write(
            "### The stack the evidence was measured on\n\n"
            "A tier is a claim about a stack, so the stack belongs next to it. The live "
            "runs behind these tiers were executed on the `kind-aruba-110` cluster, "
            "which is **not** the stack the install docs describe:\n\n"
            "| | live evidence cluster | CI / install docs |\n"
            "|---|---|---|\n"
            "| chart | `oasgen-provider-0.24.0` | `0.27.1` |\n"
            "| provider image | `local/oasgen-provider:108-live` | released `0.27.1` |\n"
            "| RDC image | `local/rest-dynamic-controller:110-test` | released `0.27.1` |\n\n"
            "The provider image is also **drifted from its own Helm release**: the "
            "release's values ask for `110-test`, while the running Deployment is "
            "`108-live`, patched in place afterwards. Anyone running `helm upgrade` on "
            "that cluster would silently revert the provider under the evidence. "
            "Neither local image carries a label recording what it was built from.\n\n"
            "oasgen-provider is a monorepo releasing oasgen-provider and "
            "rest-dynamic-controller in lockstep, so installing `0.27.1` gets RDC "
            "`0.27.1`.\n\n"
            "**This gap is now closed** (2026-10-22). `scripts/pin_verify.sh` builds a "
            "cluster matching CI exactly — pinned node image, pinned charts, no image "
            "overrides — and runs the free `network-security` chain on it. All 34 "
            "generated controllers came up on released "
            "`ghcr.io/krateo-platformops/rest-dynamic-controller:0.27.1` under "
            "`oasgen-provider:0.27.1`, and all four resources went "
            "create → observe → **drift corrected** → delete verified 404, residue "
            "clean. ESO minted its first token on that stack on the first check, so "
            "unattended credential handling holds there too. The behaviour the tiers "
            "describe is the behaviour of the released stack, now measured rather than "
            "inferred.\n\n"
            "What this does and does not put at risk: every provider feature these "
            "RestDefinitions rely on is present in the pinned release — `responseTransform`, "
            "which is what carries `Kaas`, `Registry` and `Job`, exists in `0.27.1` in both "
            "the RestDefinition CRD and the RDC client, and CI's static validation would "
            "reject the field otherwise. So no tier here depends on unreleased code. What "
            "is *not* yet proven is that the live-API runs reproduce on the released "
            "images rather than the local ones. Until one chain is re-run on a cluster "
            "built from the pin, treat that as the one gap between these tiers and a "
            "user's install.\n\n")

        f.write("## Resources\n\n")
        f.write("| Provider | Kind | Tier | Verbs | Identifier(s) | Evidence |\n")
        f.write("|----------|------|------|-------|---------------|----------|\n")
        for prov, kind, verbs, ids in rows:
            tier, why = tier_for(prov, kind)
            f.write(f"| {prov} | `{kind}` | {TIER_BADGE[tier]} | {verbs} | `{ids}` | {why} |\n")
        f.write("\n## Not generated (and why)\n\n")
        f.write("- **Action-only endpoints** (power on/off, attach/detach, "
                "associate/disassociate, download, restore, rename, automaticrenew, "
                "resetAdminPassword): not expressible as CRUD verbs — see "
                "`oasgen-provider-evolution.md` §C1.\n")
        f.write("- **List-only endpoints** (`Aruba.Insight/alerts`, `.../metrics`, "
                "`Aruba.Audit/events`, job plannings): read-only telemetry, no per-item "
                "lifecycle.\n")
        f.write("- **`compute/CloudServer`** is generated with "
                "create/get/findby/delete only; its full lifecycle needs Snowplow "
                "delegation — see §C1/C3/C5.\n")
    print(f"wrote docs/coverage.md and {len(rows)} sample pairs")


if __name__ == "__main__":
    main()
