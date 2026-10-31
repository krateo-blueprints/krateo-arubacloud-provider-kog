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
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from providers import PROVIDERS  # one list; see scripts/providers.py

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
#   beta         verified live short of the full GA lifecycle, with the specific
#                verb that is unproven named in the row. Written generally because
#                the old wording -- "observe verified live; mutation unproven" --
#                was FALSE for its only occupant: BackupPolicyAssignment has its
#                mutation proven and its DELETE is what does not complete.
#   experimental generated and statically valid; not proven live -- INCLUDING the
#                case where this account cannot reach the provider at all, which is
#                not the same as something being wrong with the definition
#   blocked      known non-functional, OR impossible through the API as declared,
#                with the reason recorded
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
    ("profile", "AiKey"): (
        "blocked",
        "**no live evidence is possible on this account, and the reason is not a gap "
        "in testing.** `Aruba.Profile/aiKeys` declares **no POST** — only GET on the "
        "collection, GET/PUT/DELETE on the item, and enable/disable — so there is no "
        "API way to create one; and a sweep on 2026-10-28 found **zero AiKeys in all "
        "five projects** on the account, so there is nothing to adopt either. Every "
        "verb it declares is therefore unexercised, including `findby`, which can only "
        "be shown returning an empty collection. That is recorded here explicitly "
        "rather than left to be read as untested-by-omission: the next person should "
        "ask Aruba for a way to create an AI key over the API, not wonder whether "
        "anyone tried.\n\n"
        "What *is* verified is generation, against the real 45 KB document: it parses "
        "(requires oasgen-provider **>= 0.35.0** for "
        "[libopenapi#2](https://github.com/krateo-platformops/libopenapi/pull/2), since "
        "a `+` in a schema name made it unparseable — "
        "[Arubacloud/api#25](https://github.com/Arubacloud/api/issues/25)) and its "
        "update verb carries a body (requires **>= 0.36.0** for "
        "[oasgen-provider#236](https://github.com/krateo-platformops/oasgen-provider/issues/236), "
        "below which the PUT would have sent `{\"metadata\": {\"name\": ...}}` against a "
        "full-replacement body and silently dropped `location` and `tags`). So the "
        "claim this row supports is *\"the spec carries the update body\"*, not "
        "*\"update works\"*. `clientSecret` is deliberately **not** mapped into the CR",
    ),
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
        "ga",
        "full lifecycle live incl. drift correction on the **released 0.27.1 stack** "
        "(2026-10-23, `6adb1322`), under `tests/ga/chains/project-folder.yaml`; delete "
        "verified 404 at `/folders/{id}`.\n\n"
        "It sat at beta on a note that was wrong, and the correction is worth keeping. "
        "That note said drift could not be exercised: flat `{name, default}`, no tags "
        "to perturb, *\"a name that is the immutable identifier\"*, and `default` an "
        "account-wide flag deciding where new projects land. The name is **not** "
        "immutable. It is what findby MATCHES on, but Folder's get, update and delete "
        "all build their path from `status.id` — never from the name — and "
        "`FolderUpdateDto` accepts `name` outright. So it can be perturbed upstream "
        "and converged back without the resource ever being lost and without "
        "`default` being touched at all.\n\n"
        "What actually blocked it was the **probe**, not the resource. The generic "
        "drift injector perturbs `metadata.tags`; `FolderUpdateDto` is "
        "`additionalProperties: false`, so every body shape it tried was a 400 and the "
        "result read as a property of the resource rather than a limitation of the "
        "harness. Chains can now declare `driftBody`/`driftAssert` and name the field "
        "to perturb — **free** — [live-cluster-test](live-cluster-test.md)",
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
        "create → observe → **drift corrected** re-proven live 2026-10-31 on the "
        "injection-confirming probe, so this no longer rests on the era when a 2xx "
        "counted as correction. **Delete DOES NOT COMPLETE, and that is now earned "
        "rather than assumed.**\n\n"
        "```\n"
        "drift BackupPolicyAssignment/ga-bpa-c: ok\n"
        "--- deleting BackupPolicyAssignment/ga-bpa-c\n"
        "    DELETE NOT CONFIRMED after 45 min: .../6ae5b316c7425cdb05e69c68 still\n"
        "    reads 200 — the CR is STILL PRESENT, deletionTimestamp=09:27:31Z,\n"
        "    finalizers=[composition.krateo.io/finalizer] — wedged, not orphaned\n"
        "```\n\n"
        "Three independent observations, and the one that matters is not the biggest "
        "number:\n\n"
        "| assignment | in `Deleting` | target volume | rules out |\n"
        "|---|---|---|---|\n"
        "| `ga-bpa` | **56 days** | deleted by hand | \"it just takes a while\" |\n"
        "| `ga-bpa-b` | **8 days** | **still present** | \"you removed the volume it "
        "binds\" |\n"
        "| `ga-bpa-c` | 45 min, instrumented | **still present** | nothing uncontrolled "
        "|\n\n"
        "The case rests on `ga-bpa-b`. `ga-bpa`\u2019s 56 days is **confounded** — that "
        "is the one whose BlockStorage was force-deleted out of dependency order by "
        "hand, and a vendor could dismiss it in one line and be right to. The 8-day "
        "case, with `ga-bpa-vol` demonstrably intact and reporting `NotUsed`, is what "
        "removes that explanation.\n\n"
        "**And the 8-day case was on the account before the 45-minute run was "
        "commissioned.** The question had already been answered by a natural experiment "
        "nobody had read correctly: I checked that `ga-bpa`\u2019s volume was gone, "
        "concluded the evidence was confounded, and did not check whether "
        "`ga-bpa-b`\u2019s was intact. It was. The billable run is a clean, "
        "instrumented, same-day confirmation and it was not *necessary* — look at what "
        "the account already holds before provisioning more of it.\n\n"
        "**A wedged assignment permanently blocks its target volume**, which is the "
        "consequence with the widest blast radius. While one sits in `Deleting`, no new "
        "assignment can be made to the volume it references:\n\n"
        "```\n"
        "400 {\"fieldName\":\"BackupPolicyAssignment\",\n"
        "     \"errorMessage\":\"There is already a backup policy assigned to the "
        "resource\"}\n"
        "```\n\n"
        "The volume reports `state: NotUsed` throughout. Nothing in its own "
        "representation shows it is held, so from the volume\u2019s side the refusal is "
        "unexplainable — and it is why renaming the policy and assignment was not enough "
        "to re-run this chain; the volume needed a fresh name too.\n\n"
        "**`BackupPolicy`\u2019s GA is NOT affected.** All three policies are also stuck "
        "in `Deleting`, but each is referenced by a wedged assignment: that is "
        "dependency order, not a defect in the policy, and its own create/observe/drift/"
        "delete hold when nothing wedged points at it. Reported to Aruba 2026-10-31 with "
        "the six resource ids; the mechanism is **not** claimed, because "
        "`400 Invalid status` is indistinguishable from an ordinary transient state "
        "refusal and a confident vendor-defect claim is how the PUT-400 went wrong "
        "earlier the same day.\n\n"
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
        "That correction left one measurement outstanding: does the delete complete "
        "if the volume stays alive and the probe waits long enough? **It was taken "
        "on 2026-10-23, and the answer is no.**\n\n"
        "```\n"
        "DRIFT CORRECTED for BackupPolicyAssignment/ga-bpa-b\n"
        "--- deleting BackupPolicyAssignment/ga-bpa-b\n"
        "    error: timed out waiting for the condition      (30 min, finalizer held)\n"
        "    DELETE NOT CONFIRMED after 45 min: .../6adb152d97493a7adf79529d still reads 200\n"
        "```\n\n"
        "75 minutes, with the referenced BlockStorage alive and the policy `Active` "
        "throughout — the dependency order violated in September was respected here. "
        "The resource entered `Deleting` and stayed. So the stall is a property of "
        "the resource, not residue of the earlier mistake, and this stays beta on a "
        "measurement rather than an assumption: `create`, `observe` and `drift` are "
        "all proven; only the delete does not complete.\n\n"
        "The cost was the one the chain header warns about — a third wedged "
        "assignment. Its volume was **deliberately preserved**: the teardown was "
        "stopped before it reached the volume, so Aruba support has a case with its "
        "dependencies intact, which the September pair is not.\n\n"
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
    ("schedule", "ScheduledJob"): (
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
    ("storage", "Image"): (
        "blocked",
        "**create works and delete does not — do not ship this resource.** Probed at "
        "the API level on 2026-10-27. `POST .../images` with "
        "`source: {type: Volume, value: <full volume URI>}` returns **201** and the "
        "image goes straight to `InCreation`, where it has now stayed for **78 "
        "minutes and counting**, through **54 DELETEs**, every one refused with "
        "`400 {\"field\":\"Status\",\"errorMessage\":\"Status is invalid\"}` — the "
        "transitional-state refusal. Its `status` never carried more than "
        "`{creationDate, state}`: no `failureReason`, no `error`, no terminal state. "
        "It never reached `Failed`, so it is not even the ~30-minute failure band "
        "that registries and KaaS backups land in.\n\n"
        "A controller cannot own a resource it can create but not delete: the "
        "finalizer would never release and the CR would wedge with it. That is why "
        "this is `blocked` and not `experimental`, despite the RestDefinition "
        "generating cleanly with a full verb set.\n\n"
        "**The confound is now eliminated.** The first probe had deleted the source "
        "volume ~13 minutes in, while the capture was still running, so it could not "
        "say whether losing the source caused the wedge. "
        "`tests/ga/chains/storage-image.yaml` was written to settle exactly that and "
        "run on a pin_verify cluster (released 0.27.1, no image overrides). The "
        "chain tears down in reverse dependency order, so **the source volume stayed "
        "alive for the entire image delete attempt** — and the image still would not "
        "go. The volume was then deleted cleanly, first time, verified 404. Losing "
        "the source was never the cause.\n\n"
        "What the chain run added, all on the released stack:\n\n"
        "- `update` is refused too: `400 Status InCreation is invalid for Action "
        "Update`, so drift cannot even be injected. Image supports **create and read "
        "only**, in practice.\n"
        "- The CR does not disappear — it **wedges**. `ga-chain-image` sat "
        "`Terminating` with `composition.krateo.io/finalizer` held and a "
        "`deletionTimestamp` that never cleared, while the upstream resource kept "
        "answering 200. That is the predicted failure and the reason for this tier.\n"
        "- The residue check found the leftover on all six attempts, so the chain "
        "reports it rather than passing.\n\n"
        "Two live records, both still in `InCreation`: `ga-image` "
        "(`6ae04d3e984f54e9730dfce5`, the contaminated probe) and `ga-chain-image` "
        "(`6ae08924984f54e9730e0598`, the clean chain run). Both are reported to "
        "Aruba — [live-cluster-test](live-cluster-test.md)",
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
        "experimental",
        "create/observe/delete proven live under `tests/ga/chains/container.yaml` "
        "(`Ready, id=6ad7aea9`), which only became reachable once Kaas itself could "
        "report Ready. Two values the OAS cannot give you, both measured 2026-10-20: "
        "`type` accepts **only `Full`** — the API says so in an error attached to the "
        "wrong field, `('RetentionDays', 'Type is not valid. Allowed values are "
        "Full')` — and only **one backup may be in creation at a time** (`There is "
        "another backup in creation`), so backups cannot be created in parallel. "
        "**`billingPeriod` is not validated**: a probe created a backup with "
        "`billingPeriod: \"NotAPeriod\"` and the API persisted that string.\n\n"
        "**It does not provision on this account**, and that is now measured rather "
        "than inferred. With a 45-minute settle window and the state logged on every "
        "transition (2026-10-23, released 0.27.1 stack):\n\n"
        "```\n"
        "state 6adb1e4e: (first read) -> InCreation at t+0s\n"
        "state 6adb1e4e: InCreation   -> Failed     at t+1637s\n"
        "drift: ... cannot inject (state at refusal: Failed)\n"
        "```\n\n"
        "Two earlier claims here were wrong and are worth keeping visible. The first "
        "was that the backup \"stayed `InCreation` for over 25 minutes, outlasting a "
        "900s settle wait\", with the implication that a longer window would earn the "
        "drift test. It leaves `InCreation` promptly enough — into `Failed`. The "
        "second was blaming the earlier `Failed` on the unvalidated "
        "`billingPeriod: \"NotAPeriod\"`; this run sent `Hour` and failed identically, "
        "so that explanation does not survive. The drift PUT was never going to land "
        "at any timeout, because a `Failed` resource refuses writes with the same "
        "`{fieldName: Status, errorMessage: Invalid status}` a busy one does.\n\n"
        "The signature is **identical to `container/Registry`** — accepted, ~30 "
        "minutes `InCreation`, then `Failed` with no `failureReason` and no `error` — "
        "on an account where the parent `Kaas` reaches `Active` in 266s. Two "
        "sub-resources of one provider failing the same way is a stronger signal than "
        "either alone, and it is with Aruba support. `create`, `observe` and a "
        "**404-verified delete** all work; what fails is the provisioning behind them "
        "— **billable** — [live-cluster-test](live-cluster-test.md)",
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
        "ga",
        "**FULL LIFECYCLE PROVEN LIVE (2026-10-31) \u2014 create, observe, drift and "
        "delete, with the drift injection CONFIRMED present before its removal was "
        "measured:**\n\n"
        "```\n"
        "state 6ae58c3f: (first read) -> InCreation at t+0s\n"
        "state 6ae58c3f: InCreation -> Active at t+73s\n"
        "drift: using PATCH \u2014 the OAS declares no PUT for this path\n"
        "drift injected (tags=[drifted-by-hand]) and CONFIRMED present upstream\n"
        "DRIFT CORRECTED for CloudServer/ga-cs-server\n"
        "5/5 deletes verified and attributed \u00b7 leftovers = 0 \u00b7 exited 0\n"
        "```\n\n"
        "**Attributed, not merely observed.** RDC logged `Mutated via RESTAction` with "
        "`op: Update`, snowplow resolved `cloudserver-update`, and the tag went from "
        "confirmed-present to absent: dispatch \u2192 action resolved \u2192 condition "
        "removed. The delegated DELETE is proven on three separate runs, each with the "
        "controller releasing its own finalizer.\n\n"
        "**It was briefly GA on a wrong premise, then beta, and is now GA on "
        "measurement.** The withdrawn premise was that its update was blocked by a "
        "vendor defect; the real cause was this repo sending PUT to the one item path "
        "in the vendored set that declares PATCH and no PUT. "
        "\n\n**A drift result from 2026-10-30 is VOID**, caught before it was "
        "recorded as evidence. The run reported `DRIFT CORRECTED` for CloudServer "
        "while snowplow\u2019s log showed the reconciling stage making **zero** api "
        "calls \u2014 so nothing corrected anything, because the injected tag was "
        "never there to correct.\n\n"
        "**An earlier version of this entry said Aruba accepted the PATCH and dropped "
        "`metadata.tags`. That is WITHDRAWN.** The tag demonstrably persists \u2014 the "
        "2026-10-31 run confirmed it present upstream before measuring its removal. So "
        "the cause of the non-landing is **open**, not explained. The only material "
        "difference on record is the state trace: the void run observed "
        "`Active at t+0s` with no InCreation phase, where the good run saw "
        "`InCreation -> Active at t+73s`. An injection into a resource reporting "
        "settled while still being created is a plausible mechanism and nothing more. "
        "It is left as a question rather than replaced with a second guess, because "
        "the first guess was confident and wrong, and a correction deserves the same "
        "scrutiny as the claim it replaces.\n\n"
        "injection landed, polled once, saw `tags == []` and declared success.\n\n"
        "`_drift_explicit` had warned about precisely this in its own docstring "
        "\u2014 *a probe that injects without asserting cannot tell correction from "
        "the API quietly ignoring the write* \u2014 while the tag-trick path, which "
        "every other resource uses, carried no such assertion. `ga_chain.py` now "
        "confirms the tag is present upstream before waiting for it to disappear, and "
        "returns *skipped* rather than *passed* when the injection does not land.\n\n"
        "**Scope, stated carefully rather than over-corrected.** The demonstrated "
        "false pass is specific to CloudServer, the only resource here with no native "
        "update verb \u2014 its correction could only have come from the delegated "
        "action that made no calls. The other 28 resources with drift evidence DO "
        "have a native update verb, so a real correction mechanism existed and their "
        "passes have a plausible cause. Their evidence is nonetheless weaker than "
        "previously stated, because it predates the confirmation step: it shows the "
        "tag ending up absent, not that it was ever present. Recorded rather than "
        "silently relied on.\n\n"
        "Open and unanswered: **why** Aruba accepts a PATCH carrying `metadata.tags` "
        "and drops it. The PATCH schema declares `metadata` as `MetadataDto` with "
        "`tags: array<string>`, so the field is documented as writable.\n\n"
        "its update was blocked by a vendor defect. That premise was wrong \u2014 see "
        "the withdrawal below \u2014 and the promotion is reverted with it: an API that "
        "declares PATCH and has never been exercised with it is *unproven*, which is "
        "exactly what beta means. One run with the corrected method settles it.\n\n"
        "**FULL DELEGATED LIFECYCLE PROVEN LIVE (2026-10-30)**, after two defects that made every "
        "delegated delete a silent no-op. A `CSO1A2` was created, observed, deleted by "
        "its own delegated RESTAction, and the finalizer released by the controller "
        "**without intervention**: 5/5 deletes verified and attributed, 0 wait-timeouts, "
        "0 unattributed, 0 instrument disagreements, snowplow logging one "
        "`calling api name=delete` and no `skipping api call`, and the account clean by "
        "id afterwards. GA on the `create \u2192 observe \u2192 delete` bar.\n\n"
        "**Its update is UNTESTED, and the \u201cvendor blocker\u201d claim that stood "
        "here is WITHDRAWN.** It said Aruba\u2019s PUT answers 400 for every body shape "
        "tried \u2014 `Flavor: Please specify flavor.; Product: not found`, reproduced "
        "on three separate servers. The reproduction was real and the conclusion was "
        "wrong: **the OAS declares `DELETE, GET, PATCH` for "
        "`cloudServers/{cloudServerId}` and no PUT**, and this repo\u2019s drift "
        "machinery hardcoded PUT at both call sites. Across the whole vendored set, 29 "
        "item paths declare PUT and exactly one declares PATCH without it \u2014 this "
        "one. So the 400 was Aruba rejecting a PUT to a PATCH-only endpoint, reaching a "
        "create-like handler, which is why it asked for a `Flavor` and a `Product`: that "
        "is CREATE-body validation. The error named the cause and it was read as the "
        "symptom, three times.\n\n"
        "`scripts/ga_chain.py` now DERIVES the method from the OAS (`update_method()`), "
        "so drift uses PATCH where no PUT is declared. CloudServer\u2019s drift has "
        "therefore never been exercised with the correct verb and is **unproven rather "
        "than blocked** \u2014 a materially weaker claim than the one withdrawn, and "
        "nothing upstream was filed on the wrong one.\n\n"
        "**The two defects were both in this repo\u2019s RESTActions and both were "
        "invisible by construction.** Recorded because the shapes generalise to any "
        "delegated resource:\n\n"
        "1. *A stage `filter` is evaluated on an envelope* \u2014 "
        "`{extras, <stage name>}` \u2014 not on the response body. The filters read "
        "`.spec.metadata.name` and `.values`, which is what `path` and "
        "`dependsOn.iterator` see (those get the resolver dict). In a filter both are "
        "absent, and jq resolves them to null **silently**, so the filter returned a "
        "well-formed result that was simply always empty. `self` was null on every "
        "call, both iterators yielded `[]`, nothing was sent. snowplow#998.\n"
        "2. *`$.` is not valid jq* \u2014 `$` must be followed by a variable name. Six "
        "path templates used `$.spec.projectId`, so jq rejected the whole expression "
        "and snowplow logged `skipping api call: path jq expression errored` at DEBUG, "
        "invisible at its default `LOG_LEVEL=warn`. Every delegated write stage was "
        "disabled, update included. snowplow#1001.\n\n"
        "Both are gated in CI now: `verify_restaction_filter_envelope` rejects a filter "
        "reading a root key outside `{extras, slice, <stage>}`, and "
        "`verify_restaction_jq_compiles` compiles all 20 expressions with real jq. They "
        "catch different things \u2014 jq compiles a filter reading absent keys "
        "perfectly happily, which is exactly why the first bug outlived the second.\n\n"
        "**What reported success while nothing happened**, stated because the absence of "
        "a signal was the only signal: snowplow answered 200, "
        "`X-Snowplow-Stage-Outcomes` was `[]` (it lists only stages that did NOT "
        "succeed, so `[]` means all succeeded), and RDC logged "
        "`Mutated via RESTAction`. Every layer was correct and the server stayed Active "
        "through 23 attempts. Only oasgen#261\u2019s not-addressable guard noticed, by "
        "refusing to drop the finalizer \u2014 without it this would have orphaned a "
        "billable server on **every** delete for as long as the bug persisted. "
        "snowplow#989\u2019s per-stage iteration counts would have shown "
        "`delete: 0 items` on the first call.\n\n"
        "---\n\n"
        "*Everything below predates 2026-10-30 and is kept verbatim. Each paragraph was "
        "true when written, including the two confident claims that turned out wrong.*\n\n"
        "**observe proven live** (2026-10-20): a server was created directly over the "
        "API and the CR converged on it, reporting back the same id the POST returned "
        "(`6ad732b8`), which exercises the only two verbs it declares — `findby` and "
        "`get`. Before this the sample had only ever been applied against an empty "
        "collection, which proves nothing. Run it with "
        "`scripts/ga_cloudserver_observe.py`.\n\n"
        "**Create is NATIVE and wired as of 2026-10-28**, and the long note that used "
        "to stand here — that it was unreachable and quarantined under "
        "`tests/experimental/` — is obsolete. The create endpoint exists only in "
        "`compute-provider_v1.1.json`, so the verb carries a per-verb `oasPath` onto "
        "that document, which requires oasgen "
        "[#227](https://github.com/krateo-platformops/oasgen-provider/pull/227) "
        "(0.34.0); below it the field parses and is silently ignored and the create "
        "resolves against the 1.0 document, which has no POST for the path.\n\n"
        "**Routing is proven; the create is REFUSED for account reasons.** Aruba "
        "answers the POST with field-level validation errors — `FlavorId`, "
        "`DataCenter`, `Product` — and not `operation not found: POST`, which is what "
        "proves the per-verb `oasPath` reaches the 1.1 endpoint. What it then says is "
        "`{\"fieldName\":\"FlavorId\",\"errorMessage\":\"<uuid> not available for "
        "trial\"}`, for `CSO1I1`, the smallest of the 40 flavors the catalog returns. "
        "The catalog exposes only `{name, cpu, ram}`, so which flavors an account may "
        "use is not discoverable before the attempt — "
        "[Arubacloud/api#29](https://github.com/Arubacloud/api/issues/29).\n\n"
        "**CREATE IS PROVEN LIVE (2026-10-29).** A `CloudServer` was created from a CR "
        "with `flavorName: CSO1A2` and went `InCreation -> Active` in **61s**, the "
        "first ever created from a CR in this repository. Four of the five resources "
        "in its chain also had drift injected and corrected.\n\n"
        "**AND THE SAME RUN FOUND THAT THE DELEGATED LIFECYCLE SENT NOTHING AT ALL, "
        "WHICH ORPHANED THAT SERVER.** A collection item on this API has exactly "
        "`{metadata, properties, status}` — the identifier is at **`metadata.id`** — "
        "and every iterator in the delete and update RESTActions tested `$s.id`, "
        "which is therefore always null. Each yielded `[]`, so **no request was ever "
        "sent**: 12 occurrences covering delete, volume detach, power state, subnets, "
        "security groups and data volumes. The entire delegated lifecycle was inert.\n\n"
        "A stage iterating zero items **succeeds**, so Snowplow answered `200` with "
        "`X-Snowplow-Stage-Outcomes: []` — byte-identical to the header for \"nothing "
        "to do\" — while RDC retried forever with `delete of ga-cs-server did not "
        "remove the external resource, which is still present`. The CR wedged on its "
        "finalizer and the server billed until it was deleted by hand; a single "
        "hand-issued DELETE removed it at once, so the API was never the problem.\n\n"
        "**If you created a CloudServer with this provider before 2026-10-29, check "
        "your account.** The controller did not delete it, and said nothing.\n\n"
        "**This class of bug becomes detectable upstream.** The ambiguity that hid it "
        "— a stage iterating zero items being reported identically to a stage with "
        "nothing to do — is now "
        "[snowplow#989](https://github.com/krateo-platformops/snowplow/issues/989), "
        "**not scheduled for any release** — corrected 2026-10-31 on the "
        "maintainers\u2019 word after this doc said \"scheduled for 1.12.62 or later\", which implied a slot that does not exist; 1.12.62 is the export release "
        "and carries something else. The proposal is a separate additive header "
        "`X-Snowplow-Stage-Iterations` listing each looping stage with its item count, "
        "leaving `X-Snowplow-Stage-Outcomes` unchanged. A `delete: 0 items` would have "
        "made this visible on the first run instead of after a stranded server. When "
        "it ships, assert on it in any chain that delegates.\n\n"
        "Fixed, and verified with jq against the real response shape (`$s.id` -> `[]`; "
        "`$s.metadata.id` -> the id). **The fix is not yet proven live** — that needs "
        "another real server — which is why this stays at **beta**: `findby`, `get` "
        "and now `create` are proven; `update` and `delete` are not.\n\n"
        "Drift is **SKIPPED** for a reason the fix also explains: the chain reported "
        "`PUT returned 400 for every body shape tried — Flavor: Please specify "
        "flavor.; Product: not found`, and the delegated update that should have "
        "handled it had all four of its stages inert.\n\n"
        "Undeclared constraints the OAS does not state, each found by a refusal: the "
        "boot volume must be `bootable: true` **with** an image code (a plain volume "
        "never boots); `securityGroups` is **required** (`GenericResource: "
        "securityGroup cannot be null`) though the request schema marks nothing "
        "required; and `dataCenter` is **not the location** — it is a zone WITHIN one "
        "(`ITBG-1`, not `ITBG-Bergamo`), a mapping that exists only at "
        "`GET /catalog/locationAndDataCenter`, undocumented on the portal "
        "([Arubacloud/api#26](https://github.com/Arubacloud/api/issues/26)).\n\n"
        "**A correction, because this document carried the opposite claim.** It was "
        "recorded here that the API returns only the FIRST validation error, which was "
        "used to explain why a flavor probe could not work. That is wrong, and it was "
        "measured on 2026-10-28: a POST with two deliberately invalid fields returns "
        "BOTH — `[{DataCenter, not found on selected location}, {Product, not found}]`. "
        "The reason a probe still cannot settle flavor eligibility is different and "
        "narrower: the trial check runs only after every reference in the request "
        "resolves, so a request deliberately broken elsewhere never reaches it. "
        "Reaching it needs an otherwise-valid request, which is a real server.\n\n"
        "**Delegation needs FOUR prerequisites, and the chart provides none of them** "
        "— `rdc.env.URL_SNOWPLOW`, `rdc.env.URL_AUTHN`, an `audience: authn` projected "
        "token mounted at `/var/run/secrets/krateo.io/serviceaccount`, and a "
        "`serviceaccount.authn.krateo.io` CR matching the controller's ServiceAccount "
        "plus RBAC for its groups. **Proven sufficient for a CALLER, not yet for RDC** — "
        "and the distinction is a correction to what this entry said on 2026-10-28. A "
        "probe pod using RDC's own mechanism (an `audience: authn` projected token "
        "exchanged at `/serviceaccount/login`, then the JWT to `/call`) authenticated "
        "and received valid `X-Snowplow-Stage-Outcomes` on snowplow 1.12.57 and "
        "1.12.58. **RDC's own delegated call has never been observed**, because "
        "verification fails before dispatch when the id is missing "
        "([oasgen#261](https://github.com/krateo-platformops/oasgen-provider/issues/261)) "
        "— so the absence of 401s in the controller log is the absence of errors on a "
        "path that was never walked, which is not evidence. Wired in "
        "`scripts/pin_verify.sh` under `WITH_SNOWPLOW=1`; filed upstream as "
        "[oasgen-provider#262](https://github.com/krateo-platformops/oasgen-provider/issues/262).\n\n"
        "**And a create that fails leaves the CR undeletable** — "
        "[oasgen-provider#261](https://github.com/krateo-platformops/oasgen-provider/issues/261), "
        "fixed in 0.41.0. The `deleteApiRef` path verifies the delete through the "
        "observe verb, which needs an id a failed create never produced, so it loops "
        "on `verifying deletion via get: missing path parameter: cloudServerId` and "
        "holds the finalizer indefinitely; ours sat eight hours. The native delete "
        "path handles the same case correctly (`External resource is not addressable, "
        "releasing finalizer without calling the API` — verified with a `Vpc` whose "
        "create also failed), so the guard exists and is simply not applied on the "
        "delegated path. Since Aruba refuses every flavor on a trial account, the "
        "FIRST CloudServer a user creates here is one they must then unwedge by hand.\n\n"
        "**Requires snowplow >= 1.12.57**, a version floor rather than a preference, "
        "for two separate confirmed defects "
        "([snowplow#903](https://github.com/krateo-platformops/snowplow/issues/903) and "
        "[#904](https://github.com/krateo-platformops/snowplow/issues/904), both "
        "confirmed in code 2026-10-26).\n\n"
        "**#904 — a failed stage does not stop a later write, and `/call` still "
        "answers 200.** Verified by snowplow against a read -> detach (409) -> delete "
        "sequence: the DELETE is sent anyway, the stage error is recorded under "
        "`errorKey`, and a filter can hide it completely. For this resource the "
        "consequence is specific and our existing safety net does **not** catch it: "
        "RDC's delete verification checks the *server* via the observe verb, so a "
        "failed volume detach followed by a successful server delete verifies as "
        "clean — the server is genuinely gone, the finalizer correctly releases, and "
        "the data volumes are orphaned with nothing referencing them. The net catches "
        "\"the delete did not happen\", not \"the delete happened and left the wrong "
        "things behind\".\n\n"
        "Note that #904's fix does **not** automatically cover this RESTAction, which "
        "is why the floor is stated with the caveat. The fix skips a write step whose "
        "`dependsOn` step failed, unless `continueOnError` is set. Here `delete` "
        "depends on `server`, not on `detachVolumes` — they are siblings off the same "
        "read — and all three stages set `continueOnError: true`, so both exemptions "
        "apply. The fix is three lines — `delete.dependsOn` from `server` to "
        "`detachVolumes`, and drop `continueOnError` from the delete step **and from "
        "`detachVolumes`** — and it is **deliberately not made yet**. The third line "
        "was missing from an earlier version of this note, which called it a two-line "
        "fix; it was corrected against "
        "[snowplow#911](https://github.com/krateo-platformops/snowplow/pull/911), the "
        "#904 fix, where leaving `continueOnError` on `detachVolumes` was measured to "
        "delete the server after a **failed server read**: detach runs anyway, its "
        "iterator over an absent server yields zero items, zero items succeeds, and "
        "the delete is waved through (POST 0, DELETE 1). Snowplow pinned down the "
        "semantics that cut both ways here: an iterator "
        "yielding **zero items has succeeded**, so a server with no data volumes (the "
        "common case, and every server the GA chain creates) is still deleted, while "
        "a failed detach skips the delete and the response header says so. Below "
        "1.12.57 the delete is sent regardless, so making the change early would add "
        "ordering and no protection while reading as though the hole were closed. The "
        "exact diff and its reasoning are recorded in "
        "`restactions/compute/cloudserver-delete.restaction.yaml`, where whoever "
        "raises the floor will find them.\n\n"
        "**#903 — background work can send write stages.** Below 1.12.57, Snowplow background work can send a "
        "RESTAction's WRITE stages using the endpointRef credential — its "
        "\"background only reads\" guard covers only its own service account. These "
        "three RESTActions are not exposed in normal operation: no widget references "
        "them, they carry no `userAccessFilter`, so pre-warm and seeding never pick "
        "them up, and a resolve that reached the Aruba API is never cached, so the "
        "refresher has nothing to replay. The exception is narrow and real: if the "
        "endpointRef Secret is absent at the moment of a resolve the result can be "
        "cached truncated, and a later refresh — once the Secret exists again — would "
        "run the full read → detach → delete sequence against a live server with no "
        "user action. **Never let a widget reference these RESTActions.** From "
        "1.12.57 background work sends no write step for any credential, and writes "
        "run only on a real request such as the controller's `/call`.\n\n"
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
FILES = PROVIDERS



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
            #   oasgen 0.27.1  NESTED is correct  (the previous pin)
            #   oasgen 0.34.0  NESTED is correct  (measured)
            #   oasgen 0.30.0  NESTED is correct  (kind-aruba-030)
            # The pin is now 0.36.0, which is NOT in this table: nested is assumed
            # there on the strength of every version since 0.24.0, not measured. The
            # line above used to be annotated "(the pin now)" and silently became a
            # claim about a version nobody had checked.
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
        # What does NOT count, written here rather than in a commit message because
        # every instance below was a true statement that answered a question nobody
        # asked -- and each one was believed for a while.
        f.write(
            "### What does not count as evidence\n\n"
            "Each of these was recorded in this repository as a result, and each is a "
            "true statement that answers a question nobody asked.\n\n"
            "**An absence on a path that was never walked.** *\"No 401, no 403 and no "
            "missing-token error across ~20k lines of controller log\"* was reported as "
            "proof that delegation worked. The controller had never called the "
            "delegate at all — verification failed before dispatch — so the absence of "
            "errors was an absence of traffic. Before reporting that something did not "
            "fail, establish that it ran.\n\n"
            "**A correct observation on a path you did not test.** Stronger, and more "
            "expensive, than the absence above — because here the measurement is "
            "*sound*. `X-Snowplow-Stage-Outcomes: []` was measured on a cluster with "
            "no cloud server, checked against the Snowplow maintainers' own written "
            "prediction, and recorded as confirmation. It was correct. It was also "
            "produced by an iterator that could never send anything, so the identical "
            "header meant \"nothing to do\" in the case tested and \"this lifecycle is "
            "inert\" in the case that mattered — and the second case silently orphaned "
            "a billable server. No amount of care about the *measurement* would have "
            "caught it: the verification was valid and the inference from it was not. "
            "Agreement with a prediction is not evidence that the thing under test "
            "works, unless the test could have disagreed.\n\n"
            "**A check that cannot fail.** A `|| die` guard is read as proof the "
            "failure mode was considered, and it was — but the command before it "
            "succeeded unconditionally, so the guard was unreachable while the thing "
            "it protected was being overwritten. The misleading part was the safety "
            "net's *existence*: a reviewer's eye stops at the `||` and reads "
            "reassurance. The question that finds these is not \"is this check "
            "correct\" but \"what can this check structurally not report\".\n\n"
            "**A one-shot sample of a varying quantity.** Host CPU was reported at "
            "1196% of 800% and used to justify deleting a cluster; a later single "
            "sample read 125%. Both were single instants. Disk at *0 bytes available* "
            "was the trustworthy reading from the same afternoon, because it is a "
            "state rather than a sample. Distinguish the two before acting on either.\n\n"
            "**A conclusion from the cheapest sample.** `CSO1I1` was refused *\"not "
            "available for trial\"* and read as \"this account cannot create cloud "
            "servers\". `CSO1A2` is accepted. The refusal was per-flavor, and the "
            "flavor that had been tried was the smallest one — chosen to minimise "
            "cost. **Cheapest-first is right for spending and wrong for sampling**: "
            "when testing whether a capability exists, vary the thing under test, not "
            "the thing being economised on.\n\n"
            "**An empty reading rendered as a confident number.** `$(cmd)` that fails "
            "yields an empty string, and every aggregator turns an empty string into a "
            "plausible value: `awk '{s+=$1} END {print s}'` over no input prints `0`. A "
            "peer measuring host CPU wrapped each sample in `timeout 60 docker stats` "
            "— **`timeout` does not exist on macOS** — so every invocation failed, the "
            "stream was empty, and the instrument reported `0%` three times in a "
            "cleanly formatted line. The same summation here would have done the same "
            "thing, and the same `timeout` was missing in this repository's own "
            "commands earlier the same day. This is not a fourth axis but a "
            "**precondition on all of them: assert the reading is non-empty before "
            "interpreting it.** `grep -c .` on the raw output before any arithmetic is "
            "enough, and the count belongs in the reported line so a reader can see it "
            "was taken.\n\n"
            "**A measurement taken before the thing under test was running.** An image "
            "swap was read as \"the fix does not work\" while the controller was still "
            "on the old image: oasgen re-renders each generated controller on its own "
            "reconcile schedule, so a cluster-level check can report a candidate the "
            "controller under test has not yet picked up. Verify the artefact is "
            "running, per controller, before reading anything.\n\n"
            "**A conclusion that explains the symptom without explaining the other "
            "evidence.** The costliest one here, at roughly four hours. Two findings "
            "were held at once: *RDC never called snowplow at all*, and *the delete "
            "iterator yields `[]` because it reads `$s.id` on a shape whose identifier "
            "is at `metadata.id`*. Both were measured, both were recorded, and they "
            "cannot both be the cause \u2014 if the RESTAction never runs, its jq "
            "never evaluates. The second explained the stranded server so neatly that "
            "the contradiction was never examined, and it was reported upstream to "
            "three sessions as the root cause. Phrasing owed to the oasgen-provider "
            "session: a correction deserves the same scrutiny as the claim it "
            "replaces, and it arrives with the confidence of having just learned "
            "something, which is when it is least examined.\n\n"
            "**A measurement of the wrong process.** \u201cZero DELETE requests, "
            "beyond doubt\u201d was true and meaningless: on a delegated path snowplow "
            "issues the request, not RDC, so RDC\u2019s verbose dump could never have "
            "shown one. The same dump also does not instrument the snowplow client, so "
            "\u201czero calls to /call\u201d proved nothing either. Before trusting "
            "an absence, establish that the thing you are watching is the thing that "
            "would act.\n\n"
            "**An error message read as the symptom rather than the cause.** Aruba "
            "answered a drift PUT with "
            "`400 {\"fieldName\":\"Flavor\",\"errorMessage\":\"Please specify "
            "flavor.\"}`, reproduced on three separate servers and recorded as a "
            "vendor defect in the update verb. It is CREATE-body validation: the OAS "
            "declares `DELETE, GET, PATCH` for that path and no PUT \u2014 the one "
            "item path in the whole vendored set that does \u2014 so the PUT reached a "
            "create-like handler. The message named the cause. Three servers of "
            "consistent evidence confirmed a reading of it that was never checked "
            "against the schema.\n\n"
            "**A number that lands near a plausible ratio.** A chart install took 13s "
            "at 316 MB and 5s at 32 MB on a GitHub runner \u2014 a 2.6x ratio, close "
            "to the 2.8x that the *uncompressed* sizes give and nowhere near the 10x "
            "the wire bytes give. Both measurements were mostly fixed overhead on a "
            "runner sitting next to the registry, so neither tests anything; but the "
            "ratio agreed with a figure well enough to be quotable. A number that "
            "agrees is waved through where a number that disagrees is "
            "investigated.\n\n"
            "**Two readings of a changing quantity, compared as though simultaneous.** "
            "A cross-check reported `INSTRUMENT DISAGREEMENT \u2026 one of the two is "
            "wrong` on its first real run. Neither was wrong: the per-resource check "
            "ran during that resource\u2019s teardown and the residue listing ran nine "
            "minutes later, after the delete had succeeded in between. The check now "
            "re-reads the item before claiming a contradiction. This is the "
            "one-shot-sample error wearing a different hat \u2014 two samples of a "
            "state that moved.\n\n"
            "**A capability missing from your command line, reported as missing from "
            "the tool.** An hour was spent diagnosing a delegated mutation that logs "
            "its decisive line at DEBUG, and the conclusion reported upstream was that "
            "generated controllers have no verbosity control. `scripts/pin_verify.sh` "
            "has carried `RDC_DEBUG` and `--set-string "
            "rdc.env.REST_CONTROLLER_DEBUG=true` throughout, with a comment explaining "
            "the schema trap that makes `--set` fail \u2014 written by the same person "
            "who then reported the lever absent. Check your own tooling before "
            "reporting a gap in someone else\u2019s.\n\n"
            "**A generated schema read off a cluster on an old pin.** Three times in "
            "one day, `kind-aruba-110` gave a confident wrong answer about generated "
            "CRDs \u2014 most consequentially `spec.metadata` appearing to expose only "
            "`['name']`, which nearly settled that a CR could not declare tags at all. "
            "On the current pin it is `['location','name','tags']`. That cluster exists "
            "only to hold a credential; schema questions get read off a cluster on the "
            "pin under test.\n\n"
        )
        f.write("| Tier | Bar | Count |\n|------|-----|-------|\n")
        f.write("| **GA** | full `create → observe → drift → delete` against the live API | "
                f"{counts.get('ga', 0)} |\n")
        f.write("| beta | verified live short of the full lifecycle; WHICH verb is "
                "unproven is recorded per row | "
                f"{counts.get('beta', 0)} |\n")
        f.write("| experimental | generated and valid; not proven live — including "
                "where THIS ACCOUNT cannot reach the provider at all | "
                f"{counts.get('experimental', 0)} |\n")
        f.write("| **blocked** | known non-functional, or impossible through the "
                "API as declared; reason recorded | "
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
            "real DELETE look identical.\n\n"
            "A **third case** was documented here and is **withdrawn**: an API that "
            "declares an update verb which does not work. It was written for "
            "`compute/CloudServer`, whose drift reported SKIPPED on a 400 \u2014 but "
            "that 400 came from this repo sending PUT to the one item path in the whole "
            "vendored set that declares PATCH and no PUT. The vendor was not at fault "
            "and the rule had no instance. `scripts/ga_chain.py` derives the method "
            "from the OAS now, so the two original cases stand unamended: drift is "
            "*inapplicable* with no update verb, *unproven* otherwise. CloudServer is "
            "in the second category until its drift runs with PATCH.\n\n")
        # One experimental resource does NOT meet the experimental bar, and a count
        # that hides that is the kind of thing this table exists to prevent. Written
        # as prose rather than a footnote marker so it cannot be skimmed past.
        f.write(
            "**`profile/AiKey` moved from experimental to blocked on 2026-10-31, and "
            "the argument this paragraph used to carry is worth keeping visible.** It "
            "said AiKey was \"not blocked: nothing is known to be broken about it\" — "
            "reasoning from the code outward, where the definitions are valid and "
            "nothing has failed. Reading from the API inward gives the opposite "
            "answer: `Aruba.Profile/aiKeys` declares no POST, so no sequence of calls "
            "brings one into existence, and a sweep of all five projects on 2026-10-28 "
            "found zero to adopt. A resource that can be neither created nor found is "
            "non-functional in the only sense this tier means — permanently, and by "
            "the API's own shape rather than by anything this repo does. The earlier "
            "reading was not wrong about the code; it was answering a question nobody "
            "asks of this table.\n\n"
        )
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
