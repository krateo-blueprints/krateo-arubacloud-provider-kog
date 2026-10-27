#!/usr/bin/env python3
"""Generate Krateo oasgen-provider RestDefinitions for every manageable Aruba
Cloud resource, straight from the patched OpenAPI specs under ``openapi/`` and
WITHOUT any wrapper/proxy web service.

The generator pairs each collection endpoint (``.../things``) with its item
endpoint (``.../things/{id}``) and emits one RestDefinition per resource, wiring
the five oasgen verbs (findby/get/create/update/delete) that actually exist.

Proxy-free metadata handling
----------------------------
Almost every Aruba resource wraps its human name and server id inside a
``metadata`` object (``metadata.name`` on create, ``metadata.id`` on read). The
original blueprint needed a Go "subnet-plugin" to flatten that object. The
braghettos oasgen-provider fork removes the need: it accepts *nested* identifiers
(``metadata.name``) and status fields (``metadata.id``), and ``requestFieldMapping``
sources the ``{id}`` path parameter from ``status.metadata.id``. That is exactly
the pattern the fork ships as its canonical Subnet example, replicated here for
every metadata-wrapped resource.

Irregular resources (non-metadata bodies, name-keyed sub-resources, secret
fields, read-only resources) are described by explicit entries in OVERRIDES.
"""
import json
import os
import re

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
OAS = os.path.join(ROOT, "openapi")
OUT = os.path.join(ROOT, "restdefinitions")
GROUP = "arubacloud.ogen.krateo.io"
# where the OAS ConfigMaps are expected to live at runtime
CM_NS = "krateo-system"

# "<provider>/<seg>" -> Kind, to disambiguate collections whose plural name
# repeats across providers (all resources share one API group, so kinds must be
# globally unique).
PROVIDER_KIND = {
    "database/backups": "DatabaseBackup",
    "container/backups": "KaasBackup",
}

# collections that look like a resource but are really action endpoints - skip.
# storage/associate-clients is GET+POST on .../images/{id}/associate-clients, which
# passes the observability test (create AND list) and so reads as a resource. It is
# not one: it is the Image's client-association list, it has no item path and no id
# of its own, and `kind_for` turns it into the nonsense Kind `Associate-client`.
SKIP = {"schedule/executions", "storage/associate-clients"}

# seg -> Kind (CamelCase, singular). Only irregular singularisations listed;
# the rest fall back to a naive de-pluraliser.
KIND_MAP = {
    "vpcs": "Vpc", "subnets": "Subnet", "securityGroups": "SecurityGroup",
    "securityRules": "SecurityRule", "elasticIps": "ElasticIp",
    "vpcPeerings": "VpcPeering", "vpcPeeringRoutes": "VpcPeeringRoute",
    "vpnTunnels": "VpnTunnel", "vpnRoutes": "VpnRoute", "loadBalancers": "LoadBalancer",
    "cloudServers": "CloudServer", "keyPairs": "KeyPair",
    "kaas": "Kaas", "registries": "Registry",
    "dbaas": "Dbaas", "databases": "Database", "users": "DatabaseUser", "grants": "Grant",
    "kms": "Kms", "keys": "Key", "kmip": "Kmip",
    "backupPolicies": "BackupPolicy", "backupPolicyAssignments": "BackupPolicyAssignment",
    # NOT "Job": the plural `jobs` collides with the built-in batch/jobs, which makes
    # `kubectl get jobs <name>` ambiguous for the BUILT-IN resource. oasgen refuses to
    # mint such a CRD from 0.32.0 (#200, 9801a69) -- hard on a FRESH install (no CRD,
    # no controller, Synced=False) and a permanent BuiltInNameCollisionExisting warning
    # event where the CRD already exists. `ScheduledJob` rather than the message's
    # suggested `ArubacloudJob`: the resource is a scheduled poweron/poweroff task
    # under Aruba.Schedule, and the group already carries the vendor.
    "jobs": "ScheduledJob", "alertRules": "AlertRule",
    "blockStorages": "BlockStorage", "snapshots": "Snapshot", "restores": "Restore",
    "backups": "Backup", "hpcs": "Hpc", "folders": "Folder", "projects": "Project",
}

# Which spec file each provider resource set comes from and the resource kind
# prefix used to disambiguate collections that repeat a name (e.g. "backups"
# exists in database, storage and container).
# Aruba's published OAS documents, consumed UNMODIFIED. The key is the short
# provider name used in ConfigMap/RestDefinition names; the value is the exact
# filename as published at https://api.arubacloud.com/openapi/.
PROVIDERS = {
    "network": "network-provider.json",
    "compute": "compute-provider.json",
    "container": "container-provider.json",
    "database": "database-provider.json",
    "storage": "storage-provider.json",
    "security": "security-provider.json",
    "schedule": "schedule-provider.json",
    "baremetal": "baremetal-provider.json",
    "project": "project.json",
    "metering": "metering.json",
}

# Explicit overrides for resources that do not follow the metadata-wrapper CRUD
# pattern. Keyed by "<provider>/<seg>".
#   idField      - natural key field in the CR spec (identifier)
#   statusId     - JSONPath in status holding the server id ("" = none)
#   metaWrap     - True if the create body wraps name in a metadata object
#   secretFields - body fields to source from a Kubernetes Secret via secretRef
#   metaIdent    - identifier for a metadata-wrapped resource, when metadata.name
#                  is not usable (e.g. the server assigns the name itself)
#   compareScope - drift comparison scope, when the default would never converge
#   note         - short rationale, echoed into the generated file header
OVERRIDES = {
    "compute/cloudServers": dict(
        metaWrap=True,
        # CloudServer lifecycle (power on/off, associate EIPs/SGs/subnets, attach
        # data volumes, restore) is a MULTI-CALL sequence, not one create/update.
        # Instead of a bespoke proxy we delegate create/update/delete to Snowplow
        # RESTActions via the fork's *ApiRef; observe stays native (get/findby).
        # See docs/lifecycle-beyond-crud.md and restactions/compute/.
        apiRefs=dict(
            namespace=CM_NS,
            extras={"api-version": "1.0"},
            create="arubacloud-compute-cloudserver-create",
            update="arubacloud-compute-cloudserver-update",
            delete="arubacloud-compute-cloudserver-delete",
            # RDC >= 0.18.0 forwards the CR spec on EVERY *ApiRef direction, so the
            # delete RESTAction reads spec.projectId like its create/update siblings.
            # Before that fix, delete received no spec and projectId had to be a
            # hardcoded static extra here (which pinned one RestDefinition to one
            # project). See rest-dynamic-controller#41.
        ),
        note=(
            "CloudServer has NO single create/update endpoint; its lifecycle (power "
            "on/off, associate elastic IPs / security groups / subnets, attach data "
            "volumes, restore) is a multi-call sequence. create/update/delete are "
            "delegated to Snowplow RESTActions via *ApiRef (NO proxy); observe stays "
            "native via get/findby. See docs/lifecycle-beyond-crud.md.")),
    "baremetal/hpcs": dict(
        metaWrap=True,
        # HPC provisioning is asynchronous: create returns 201 {monitorUri}; the
        # monitor endpoint reports status InProgress|Succeeded|Failed. This is the
        # operation-handle async case the fork's `async` block handles natively —
        # in requeue mode it is the idiomatic, non-blocking, level-based controller
        # wait (superior to a one-shot CLI wait). See docs/async-readiness.md.
        #
        # handleParam (oasgen >= 0.18.0, RDC >= 0.18.0) names the path parameter the
        # extracted handle binds to, so Aruba's published `.../monitor/{id}` is used
        # UNMODIFIED. Previously the OAS had to be patched to rename it operationId.
        async_=dict(
            action="create",
            mode="requeue",
            operationRef=dict(
                In="body", path="monitorUri",
                # monitorUri is a path; bind the handle to its trailing id
                jq=dict(inline='. | split("/") | last')),
            poll=dict(
                method="GET",
                path="/projects/{projectId}/providers/Aruba.Baremetal/hpcs/monitor/{id}",
                handleParam="id",
                statusPath="status",
                successValues=["Succeeded"],
                failureValues=["Failed"],
                intervalSeconds=5, maxAttempts=120),
            postGet=True),
        note=(
            "HPC provisioning is asynchronous. create returns 201 {monitorUri}; the "
            "controller polls GET .../hpcs/monitor/{id} (status Succeeded|Failed) via "
            "the async block in requeue mode, then re-runs findby to populate status. "
            "handleParam: id binds the handle to Aruba's own parameter name, so the "
            "published OAS is used unmodified. HPC has no delete verb in the API.")),
    "project/folders": dict(
        metaWrap=False, idField="name", statusId="status.id",
        note="Folder create body is flat {name, default}; id is a top-level response field."),
    "database/databases": dict(
        metaWrap=False, idField="name", statusId="",
        note=("Database is name-keyed: create body {name}; there is no server id and "
              "no update verb. NOTE the source OAS is inconsistent - GET uses "
              "{databaseName} while DELETE uses {name} for the same path segment.")),
    "database/dbaas": dict(
        # Dbaas provisioning is asynchronous and the OAS does not say so: create
        # returns the object immediately, findby resolves it immediately, and the CR
        # therefore reported Ready within seconds. Measured live: the resource stayed
        # status.state = "InCreation" for over fourteen minutes after the CR said
        # Ready, and every PUT in that window is refused with
        # 400 DBaaS.Read.InvalidStatus, "DBaaS has an invalid status: InCreation".
        # Ready that does not mean usable is worse than slow: it is the signal every
        # dependent step keys off.
        #
        # The poll targets the resource's own item path -- there is no monitor
        # endpoint here, unlike baremetal/hpcs -- with the handle taken from the
        # create response's metadata.id.
        #
        # The state vocabulary is NOT declared in database-provider.json. It is taken
        # from security-provider.json, which declares the same platform-wide set
        # (Active, InCreation, Deleting, Deleted, Failed) for an analogous resource.
        # "Active" and "Failed" are both confirmed present there; "Active" and
        # "InCreation" are additionally confirmed by live observation. If Aruba has a
        # database-specific terminal failure state outside that set, a failed
        # provisioning polls until maxAttempts instead of failing fast -- which is the
        # safe direction to be wrong in, but it is a guess the OAS cannot settle.
        async_=dict(
            action="create",
            mode="requeue",
            operationRef=dict(In="body", path="metadata.id"),
            poll=dict(
                method="GET",
                path="/projects/{projectId}/providers/Aruba.Database/dbaas/{id}",
                handleParam="id",
                statusPath="status.state",
                successValues=["Active"],
                failureValues=["Failed"],
                intervalSeconds=15, maxAttempts=120),
            postGet=True),
        note=("Dbaas creation is asynchronous: the API returns the object while "
              "status.state is still InCreation, and refuses every update until it "
              "reaches Active (400 DBaaS.Read.InvalidStatus). Without the async block "
              "the CR reports Ready in seconds for a resource that is unusable for a "
              "quarter of an hour. Polls its own item path, since this provider "
              "publishes no monitor endpoint. State values come from "
              "security-provider.json, which declares the vocabulary the database "
              "spec omits.")),
    "schedule/jobs": dict(
        # Job never reached Ready, and the cause is the one the oasgen peer predicted
        # for Kaas and was wrong about there: genuine server normalisation of values,
        # on keys present in both the request and the response.
        #
        #   steps[].actionUri   sent "poweroff"             returned "/poweroff"
        #   executeUntil        sent "...T00:00:00Z"        returned "...T00:00:00+00:00"
        #
        # The first is the API contradicting itself: a step is REFUSED unless actionUri
        # is relative with no leading slash ("The action /projects/.../poweroff with
        # http verb POST is not configured for this resource typology"), and the read
        # then hands it back with one. The second is the same instant in two encodings.
        # Either one alone makes the drift comparison unsatisfiable, so the controller
        # PUT the job on every resync -- five accepted updates in one short run, all
        # 200, all no-ops (see oasgen#213).
        #
        # Both are states about the API that are true, which is what makes a
        # responseTransform the right tool rather than a narrowed compareScope: we are
        # telling the controller how to read Aruba's answer, not telling it to stop
        # looking. Guarded on type so the program is idempotent and cannot corrupt a
        # body with no steps.
        responseTransform={"get": (
            'if (.properties.steps | type) == "array" then\n'
            '  .properties.steps |= map(\n'
            '    if (.actionUri | type) == "string" then .actionUri |= ltrimstr("/")\n'
            '    else . end\n'
            '  )\n'
            'else . end\n'
            '| if (.properties.executeUntil | type) == "string"\n'
            '     and (.properties.executeUntil | endswith("+00:00")) then\n'
            '    .properties.executeUntil |= (.[:-6] + "Z")\n'
            '  else . end\n')},
        note=("Job reads return values the writes did not send: steps[].actionUri "
              "comes back with a leading slash the create REFUSES to accept, and "
              "executeUntil comes back as +00:00 where the CR sent Z. Both are keys "
              "present on each side, so the drift comparison can never pass and the "
              "controller re-PUTs the job on every resync. A responseTransform on the "
              "get verb normalises both before the comparison. A step's actionUri must "
              "be relative (`poweroff`); only poweron and poweroff are supported, so a "
              "Job requires a CloudServer to target -- created out of band by "
              "scripts/ga_job_probe.py, since a RestDefinition cannot create one.")),
    "container/kaas": dict(
        # Kaas could never reach Ready, and the cause is in Aruba's spec, not ours:
        # the node pool's `instance` and `dataCenter` are STRINGS in
        # NodePoolsDto (what create and update send) and OBJECTS in
        # NodePoolsResponseDto (what every read returns). RDC's drift comparison
        # walks the CR's keys, so a key present on both sides with a different type
        # is drift -- and no compareScope helps, because KaasUpdatePropertiesDto
        # declares exactly one property, nodePools, which is the field that differs.
        #
        # Measured consequence before this fix: the controller PUT a healthy cluster
        # every 180s forever, each accepted write driving it from Active into
        # Updating, which then refused the next one. Not merely a stuck condition --
        # a live resource being rewritten indefinitely. See oasgen#213.
        #
        # The transform states what the two shapes have in common, which is a true
        # statement about the API rather than a way of silencing the comparison:
        # the scalar the request sends is the `name` of the object the response
        # returns. Guarded on type so it is idempotent and cannot corrupt a body
        # that is already request-shaped, or one with no node pools at all.
        #
        # NOT included, because they are already tolerated: properties.nodeCidr
        # (the response spells it `nodecidr`, so the key is absent from the response
        # and the comparison skips it) and securityGroup.name (the response carries
        # only `uri`). Adding them would be inventing drift fixes for drift that
        # does not occur.
        responseTransform={"get": (
            'if (.properties.nodePools | type) == "array" then\n'
            '  .properties.nodePools |= map(\n'
            '    (if (.instance | type) == "object" then .instance = .instance.name\n'
            '     else . end)\n'
            '    | (if (.dataCenter | type) == "object"\n'
            '       then .dataCenter = .dataCenter.name else . end)\n'
            '  )\n'
            'else . end\n')},
        note=("Kaas reads return a different shape than its writes accept: "
              "nodePools[].instance and nodePools[].dataCenter are strings in "
              "NodePoolsDto and objects in NodePoolsResponseDto. RDC compares the "
              "CR's keys against the response, so those two are permanent drift, and "
              "no compareScope avoids them because KaasUpdatePropertiesDto declares "
              "only nodePools. A responseTransform on the get verb folds each object "
              "back to its .name before the comparison runs. nodeCidr (returned as "
              "`nodecidr`) and securityGroup.name (returned as `uri`) need no "
              "handling: those keys are absent from the response and are skipped.")),
    "container/registries": dict(
        # Same defect as database/dbaas, found the same way: the CR reported Ready
        # while Aruba still had the registry at status.state = InCreation, and every
        # PUT in that window is refused with 400 {"fieldName": "Size"... no --
        # {"fieldName": "Status", "errorMessage": "Invalid status"}. Measured live
        # 2026-10-20: Ready at the CR, InCreation upstream more than ten minutes
        # later, and the DELETE refused for the same reason, which is why teardown
        # takes so long.
        #
        # Note the envelope: the container provider answers errors:[{fieldName,
        # errorMessage}] where network and storage answer errors:[{field, message}],
        # and it capitalises the message differently ("Invalid status" vs "invalid
        # status"). Anything matching on those strings must handle both.
        #
        # Polls its own item path; this provider publishes no monitor endpoint.
        # "InCreation" and "Failed" are both CONFIRMED by live observation, and the
        # guess that the platform-wide vocabulary applies here turned out right:
        # measured 2026-10-20, a registry sits InCreation for 1818s and then goes
        # straight to Failed. So failureValues is doing real work -- without it the
        # gate would poll a dead registry for its full 30 minutes.
        #
        # "Active" is NOT confirmed for this resource. No registry has reached it on
        # this account; see the coverage note. maxAttempts stays at 120 (30 minutes)
        # rather than being trimmed to the observed failure time, because a slower
        # account that does succeed must not be cut off by a bound derived from a
        # failure here.
        async_=dict(
            action="create",
            mode="requeue",
            operationRef=dict(In="body", path="metadata.id"),
            poll=dict(
                method="GET",
                path="/projects/{projectId}/providers/Aruba.Container/registries/{id}",
                handleParam="id",
                statusPath="status.state",
                successValues=["Active"],
                failureValues=["Failed"],
                intervalSeconds=15, maxAttempts=120),
            postGet=True),
        note=("Registry creation is asynchronous and the OAS does not say so: the "
              "create returns the object while status.state is still InCreation, and "
              "both update and delete are refused until it leaves that state with 400 "
              "{fieldName: Status, errorMessage: Invalid status}. Without the async "
              "block the CR reports Ready for a registry nothing can use yet. "
              "Measured: InCreation for 1818s, then Failed -- no registry has reached "
              "Active on this account, so the gate's value here is failing fast "
              "rather than gating Ready. "
              "Separately: properties.size takes Small, Medium or HighPerf, "
              "case-sensitive -- measured, because the OAS describes it as a "
              "connection count and its RegistrySize catalogue is referenced by no "
              "path.")),
    "database/backups": dict(
        metaIdent="metadata.id", compareScope="identifiersAndStatus",
        note=("Aruba names a backup itself: a create asking for 'ga-dbbackup' produces "
              "'mysql_<database>_<UTC timestamp>'. The requested name is accepted and "
              "discarded, and the OAS does not say so. With the default identifier "
              "metadata.name the spec can therefore never equal the response, so every "
              "reconcile reads as drift and the controller requests an update -- and "
              "there is no update verb to call, because Aruba exposes only DELETE on "
              "/backups/{id}. Observed live: one create, then 'API action update not "
              "found' once per reconcile while the CR sat at Ready=False/Creating, "
              "with the backup Active on Aruba twelve seconds after creation. "
              "metadata.id is the server-assigned key the delete verb already takes; "
              "findby matches it through isInResource's status fallback, exactly as it "
              "matched the renamed metadata.name before. compareScope must move with "
              "it: identifiersAndStatus restricts drift to identifiers plus "
              "additionalStatusFields, and every other spec field here is create-only "
              "(projectId, properties.dbaas, properties.database, billingPlan, "
              "dataCenter). 'updatable' is the closer fit in spirit but is refused by "
              "CEL without an update verb. Both keys are needed: identifiersAndStatus "
              "alone still compares metadata.name and still reports drift forever.")),
    "database/users": dict(
        metaWrap=False, idField="username", statusId="",
        secretFields=[dict(inBody="password", nameFrom="metadata.name",
                           keyFrom="spec.username")],
        note=("DatabaseUser is name-keyed (create {username, password}). 'password' is "
              "dropped from the spec and sourced from a Secret at create time via the "
              "secretRef resolver. The Secret is named after the CR and keyed by the "
              "username, both read from the CR itself - which is what makes this work "
              "without adding a *SecretRef field the source OAS does not declare, and "
              "the OAS is not ours to change. Its VALUE must be the base64 of the "
              "password: Aruba requires that and does not document it (their Terraform "
              "provider encodes client-side, dbaasuser_resource.go:130). A jq "
              "valueMapping of @base64 could not do that encoding for us on 0.22.2, "
              "which ran jq on the response direction only and silently skipped it on "
              "a request. oasgen#148 implements the request direction and shipped in "
              "0.26.0, so the 0.27.1 we now pin CAN do it and this Secret could hold "
              "the plaintext instead. It still does not: that change alters what every "
              "existing DatabaseUser Secret must contain, so it gets its own commit "
              "and its own live run rather than riding along with the version bump - "
              "see docs/authentication.md. No update verb (password change is a "
              "separate PUT .../password sub-endpoint).")),
    "database/grants": dict(
        metaWrap=False, idField="user.username", statusId="",
        note=("Grant is name-keyed. The create body nests the name as {user:{username}} "
              "and the item path segment is {username}, so the identifier must be the "
              "LEAF user.username, NOT the object `user`. Binding the object produced "
              "GET .../grants/map%5Busername:gauser%5D -- Go stringified the map into "
              "the path -- so observe never matched and the controller re-POSTed until "
              "the API answered 'A grant already exists for the specified Database "
              "User'. No update verb and no dedicated id.")),
    "security/keys": dict(
        metaWrap=False, idField="name",
        note=("Key create body is flat {name, algorithm}. The findby item carries the "
              "server id as **keyId**, not id -- mapping status.id here produced a CR "
              "that never went Ready while the key WAS created, and whose delete then "
              "had an unresolvable path parameter, so RDC released the finalizer "
              "without calling the API and the key was orphaned. An orphaned key in "
              "turn blocks deleting its Kms ('Some kms keys are not deleted').")),
    "security/kmip": dict(
        metaWrap=False, idField="name",
        # Kmip never reported Ready, and the cause is a NAME COLLISION between a path
        # parameter and a response field. Its path is
        # `/kms/{id}/kmip/{kmipId}`, so the spec carries `id` meaning THE KMS:
        #
        #   CR spec  id = 6ada80c7d94d05eaad355a3b   (the KMS that owns it)
        #   response id = 07090170-5e71-46a9-8751-... (the Kmip itself)
        #
        # One key, present on both sides, two unrelated identities. The comparison can
        # never be satisfied, so the controller PUT `{"name":"ga-kmip"}` every 180s
        # forever against a response that already said `name: ga-kmip` -- the same
        # unsatisfiable-comparison class as Kaas (types) and Job (normalisation),
        # reached by a third route (oasgen#213).
        #
        # A responseTransform cannot fix this one: the response's `id` is what
        # additionalStatusFields maps to status, so deleting it would break the status
        # mapping instead. Excluding `id` from the spec is worse -- it is a path
        # parameter, so the URL could not be built. Narrowing the comparison is the
        # honest fix, and it costs nothing here: KmipNameDto is `{name}` ONLY and
        # `name` is the identifier, so there is no non-identifying field left to
        # converge. Drift is INAPPLICABLE for exactly the reason it is for Key.
        #
        # It must be `updatable` and NOT `identifiersAndStatus`, which was tried first
        # and changed nothing -- four more no-op PUTs. identifiersAndStatus projects
        # the spec onto `identifiers + additionalStatusFields`, and `id` IS an
        # additionalStatusField here (that is how the Kmip's own id reaches status to
        # build {kmipId}). So that scope pulls the colliding key straight back into the
        # comparison. `updatable` derives its field set from the UPDATE body instead
        # (UpdatableBodyPaths -> KmipNameDto -> {name}), which is the only set that
        # excludes `id` without breaking the path or the status mapping.
        compareScope="updatable",
        # Kmip is a TOMBSTONE resource: after a successful DELETE it disappears from
        # its own list -- including with includeDeleted=true -- yet GET on its id
        # still answers 200, with `status: Deleted` and a `deletionDate` that a live
        # read does not carry:
        #
        #   live:        {"id","name","type","status","creationDate"}
        #   post-delete: {... ,"deletionDate"}  status: Deleted
        #
        # RDC's delete verification read that 200 as "still there" and held the
        # finalizer forever, which is what made Kmip the one resource whose teardown
        # wedged the whole security chain (oasgen#111). The fix shipped in 0.27.1 but
        # is NOT automatic: the controller cannot guess what a tombstone looks like,
        # so the RestDefinition has to say. Guarded on type, because `status` must not
        # be assumed a string -- a non-boolean result is a reconcile error, and this
        # API is already known to omit declared fields conditionally (oasgen#152).
        #
        # On every RELEASED version (0.23.0 through 0.32.0) it is the OBSERVE path that
        # honours this predicate and the DELETE path that cannot: RDC hands the delete
        # check the whole restclient.Response struct instead of the body, so the
        # program errors, the check degrades to "unverifiable", and the finalizer is
        # released by trusting the DELETE rather than by confirming the tombstone
        # (oasgen#218, one line). This repo's own chain verifies the tombstone against
        # the API directly, which is how the discrepancy surfaced at all.
        #
        # FIXED in oasgen f97b150 (PR #219) and verified live here on 2026-10-25: the
        # predicate matched a real tombstone and the finalizer was released BY it,
        # with the DELETE still erroring 400 --
        #   Get returned 200 but notFoundBody reports the resource absent
        #   Delete returned an error but the external resource is verifiably absent;
        #     releasing finalizer  deleteError: "unexpected status: 400"
        # That is the wedge oasgen#111 was filed to remove, which was still fully in
        # force on every release. On a released stack this predicate is dead code that
        # costs nothing; from the fix on it is load-bearing, so it must stay correct.
        # See docs/live-cluster-test.md.
        notFoundBody={"get": (
            'if (.status | type) == "string"\n'
            'then (.status | ascii_downcase) == "deleted"\n'
            'else false end\n')},
        note=("Kmip create body is flat {name}. Its findby item carries the server id as "
              "plain **id**, even though the path parameter is {kmipId} -- the opposite "
              "of security/keys, which uses {keyId} in BOTH. Assuming the path-parameter "
              "name is a field name orphans this resource exactly as assuming 'id' "
              "orphaned Key. Both mappings are now derived from the response schema.")),
}

# collections that are pure read-only (no create) but still worth exposing as a
# get/findby RestDefinition.
READONLY = {"network/loadBalancers"}


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
            props.update(p)
            req += r
        props.update(sch.get("properties") or {})
        req += sch.get("required", [])
    return props, req


def kind_for(seg):
    if seg in KIND_MAP:
        return KIND_MAP[seg]
    s = seg[:-1] if seg.endswith("s") else seg
    return s[0].upper() + s[1:]


def query_params(spec, path, method):
    ops = spec["paths"][path]
    out = []
    for prm in (ops.get("parameters", []) + ops[method].get("parameters", [])):
        prm = deref(spec, prm)
        if prm.get("in") == "query":
            out.append(prm["name"])
    return out


def find_collections(spec):
    """For each collection path, gather every one-segment-deeper item path and the
    HTTP methods available on it. Returns {coll: {"item_ops": {method:(path,idp)}}}.
    Handles APIs that use different path-param names per verb (e.g. GET .../{id}
    but DELETE .../{name} for the same logical item)."""
    paths = list(spec.get("paths", {}))
    colls = {}
    for coll in paths:
        item_ops = {}
        for it in paths:
            m = re.match(re.escape(coll) + r"/\{([^/}]+)\}$", it)
            if not m:
                continue
            idp = m.group(1)
            for method in ("get", "put", "delete"):
                if method in spec["paths"][it] and method not in item_ops:
                    item_ops[method] = (it, idp)
        if item_ops or "post" in spec["paths"][coll]:
            colls[coll] = item_ops
    return colls


def build(provider, spec):
    results = []
    seen_kinds = set()
    for coll, item_ops in find_collections(spec).items():
        collops = spec["paths"][coll]
        has_list = "get" in collops
        has_create = "post" in collops
        get_op = item_ops.get("get")
        put_op = item_ops.get("put")
        del_op = item_ops.get("delete")
        # A manageable resource must be observable: either it has an item GET, or
        # it can be created AND listed (findby). Pure POST action endpoints
        # (poweron, attach, download, automaticrenew, ...) are excluded.
        if not (get_op or (has_create and has_list)):
            continue
        seg = coll.rstrip("/").split("/")[-1]
        key = f"{provider}/{seg}"
        if key in SKIP:
            continue
        ov = OVERRIDES.get(key, {})
        kind = PROVIDER_KIND.get(key) or kind_for(seg)
        if kind in seen_kinds:
            continue
        seen_kinds.add(kind)

        meta = ov.get("metaWrap")
        if meta is None:
            meta = False
            if has_create:
                rb = deref(spec, collops["post"].get("requestBody", {}))
                for cv in (rb.get("content") or {}).values():
                    props, _ = merged_props(spec, cv.get("schema", {}))
                    meta = "metadata" in props
                    break

            # `meta` drives identifiers (metadata.name) and additionalStatusFields
            # (metadata.id) -- both of which are read from the RESPONSE, so the response
            # decides, not the create body. metering/AlertRule posts {metadata, properties}
            # but every read returns a FLAT AlertRulesDto with a root `id` and no metadata
            # at all. Declaring metadata.name there means findby can never match, so the
            # controller re-POSTs every reconcile (unbounded duplicate creates against a
            # live account), and metadata.id never populates, so delete has an
            # unresolvable path parameter and the resource is orphaned.
            if meta and has_list and not response_is_meta_wrapped(spec, coll):
                meta = False

        readonly = key in READONLY or not has_create
        results.append(dict(
            provider=provider, kind=kind, seg=seg, coll=coll, ov=ov, meta=meta,
            readonly=readonly, has_list=has_list, has_create=has_create,
            get_op=get_op, put_op=put_op, del_op=del_op,
            respId=response_id_field(spec, coll) if has_list else None,
            updatableScope=(needs_updatable_scope(spec, coll, put_op[0])
                            if (has_create and put_op) else False),
            respMeta=(response_is_meta_wrapped(spec, coll) if has_list else False),
            respFailure=(response_has_failure_reason(spec, coll)
                         if has_list else False),
            respError=(response_has_status_error(spec, coll)
                       if has_list else False),
            get_q=query_params(spec, get_op[0], "get") if get_op else [],
            list_q=query_params(spec, coll, "get") if has_list else [],
        ))
    return results


def _body_leaves(spec, path, method):
    op = (spec.get("paths", {}).get(path) or {}).get(method) or {}
    rb = deref(spec, op.get("requestBody", {}))
    for cv in (rb.get("content") or {}).values():
        return set(leaf_paths(spec, cv.get("schema", {})))
    return set()


def leaf_paths(spec, sch, prefix="", depth=0, out=None):
    out = out if out is not None else []
    sch = deref(spec, sch)
    for sub in sch.get("allOf", []) or []:
        leaf_paths(spec, sub, prefix, depth + 1, out)
    for k, v in (sch.get("properties") or {}).items():
        v2 = deref(spec, v)
        if v2.get("type") == "object" and v2.get("properties") and depth < 5:
            leaf_paths(spec, v, prefix + k + ".", depth + 1, out)
        else:
            out.append(prefix + k)
    return out


def needs_updatable_scope(spec, create_path, update_path):
    """True when the create body can express fields the update body cannot.

    Those fields are unfixable by definition: the controller sees a difference, calls
    UPDATE, the update cannot carry the field, and the difference is still there on the
    next reconcile -- forever, while the resource bills. compareScope: updatable
    (oasgen-provider#51, shipped in 0.22.1) narrows drift to what the update verb can
    actually send, which is the only set the controller can converge.

    Fixable drift is unaffected: metadata.tags lives in both bodies, which is why the
    live drift proofs for SecurityRule and VpcPeering still hold under this scope.
    """
    create = _body_leaves(spec, create_path, "post")
    update = _body_leaves(spec, update_path, "put")
    return bool(create and update and (create - update))


def response_is_meta_wrapped(spec, coll):
    """True when the findby response items carry a `metadata` object.

    Read separately from the create body because the two genuinely disagree: several
    resources accept {metadata, properties} on POST and return a flat DTO on GET.
    """
    op = (spec.get("paths", {}).get(coll) or {}).get("get")
    if not op:
        return True                      # unknown -> leave the create-body verdict alone
    for cv in ((op.get("responses", {}).get("200") or {}).get("content") or {}).values():
        sch = deref(spec, cv.get("schema", {}))
        props, _ = merged_props(spec, sch)
        item = deref(spec, (props.get("values") or props.get("value") or {}))
        item = deref(spec, item.get("items", {})) if item.get("type") == "array" else item
        iprops, _ = merged_props(spec, item)
        if not iprops:
            return True                  # could not resolve -> do not second-guess
        return "metadata" in iprops
    return True


def response_status_props(spec, coll):
    """Properties of the findby item's `status`, or {} when it is not an object.

    Shared by the two status detectors below so they cannot disagree about what the
    item schema is -- the envelope fallback took one quiet miss already (see
    response_has_failure_reason).
    """
    op = (spec.get("paths", {}).get(coll) or {}).get("get")
    if not op:
        return {}
    for cv in ((op.get("responses", {}).get("200") or {}).get("content") or {}).values():
        sch = deref(spec, cv.get("schema", {}))
        props, _ = merged_props(spec, sch)
        # Not every collection answers with a {total, values:[...]} envelope:
        # compute/keyPairs returns the {metadata, status, properties} object itself.
        # Falling through to an empty item there silently decided "no failureReason"
        # for a resource that has one, which is the quiet-miss this check exists to
        # avoid -- so with no envelope key, the response schema IS the item.
        item = deref(spec, (props.get("values") or props.get("value") or {})) or sch
        item = deref(spec, item.get("items", {})) if item.get("type") == "array" else item
        iprops, _ = merged_props(spec, item)
        st = deref(spec, iprops.get("status") or {})
        sprops, _ = merged_props(spec, st)
        return sprops
    return {}


def response_has_status_error(spec, coll):
    """True when the findby item's `status` declares the `error` object.

    `failureReason` is a bare string and it is not the only diagnostic: the same
    StatusResponseDto also carries `error` -> {reason, message}, declared in nine of
    the twelve published documents (all but auditing, metering and security). We
    surfaced `failureReason` and never looked at this one, which is how a Registry
    and a KaasBackup were reported upstream as failing with "failureReason: null"
    when the structured field beside it had never been read.

    Measured 2026-10-27 against live resources: the API OMITS both keys from `status`
    rather than sending them as null when it has nothing to say, so an absent `error`
    on a healthy resource is expected and costs nothing. Whether it is populated on a
    failure is NOT yet established -- no failed resource has been read with this field
    in hand. Surfacing it is what makes that observable at all.
    """
    return "error" in response_status_props(spec, coll)


def response_has_failure_reason(spec, coll):
    """True when the findby item's `status` is an OBJECT declaring `failureReason`.

    The API says why a resource failed and nothing surfaced it: StatusResponseDto
    declares `failureReason` (string, nullable) in all ten provider documents, it is
    null on a healthy resource and populated on a failed one, and no RestDefinition
    mapped it -- so every provisioning failure reached a CR as a resource that simply
    never converged, with the reason sitting unread in the body the controller had
    already fetched. Measured 2026-10-25: a Kms went Failed with
    `failureReason: "Ecommerce"` while the CR said only `Creating`.

    Guarded on the response rather than added everywhere, because `status` is NOT an
    object on every resource: security/keys and security/kmip return it as a bare
    string ("Deleted", "CertificateAvailable"). Declaring `status.failureReason`
    there would put a path in the CRD's status schema that cannot resolve against the
    OAS. RDC itself tolerates a missing field at runtime -- populateStatusFields
    skips a path it cannot find -- but the generated schema is built from these same
    paths, so the check belongs here.
    """
    return "failureReason" in response_status_props(spec, coll)


def response_id_field(spec, coll):
    """The name the findby response actually uses for the server-assigned id.

    Guessing this is how a resource gets orphaned. Two live cases from the same
    provider disagree: security/keys returns `keyId`, security/kmip returns plain
    `id` -- even though their path parameters are {keyId} and {kmipId} respectively.
    Assuming "id" broke Key (status never populated, so delete could not address the
    real key and it was left behind); assuming the path-parameter name then broke
    Kmip the same way in the other direction. The response schema is the only
    authority, so read it.
    """
    op = (spec.get("paths", {}).get(coll) or {}).get("get")
    if not op:
        return None
    for cv in ((op.get("responses", {}).get("200") or {}).get("content") or {}).values():
        sch = deref(spec, cv.get("schema", {}))
        props, _ = merged_props(spec, sch)
        item = deref(spec, (props.get("values") or props.get("value") or {}))
        item = deref(spec, item.get("items", {})) if item.get("type") == "array" else item
        iprops, _ = merged_props(spec, item)
        if "id" in iprops:
            return "id"
        for k in iprops:
            if k.lower().endswith("id") and not k.lower().startswith("private"):
                return k
        # None, not "id": a name-keyed resource (Database, Grant, DatabaseUser) has no
        # server id at all, and conflating that with "the id is called id" is what sends
        # the mapping to spec.<name> for resources that DO have one.
        return None
    return None


def id_target(r):
    """Where the item {id} path parameter should be sourced from in the CR."""
    ov = r["ov"]
    if r["meta"] or (r["readonly"] and r.get("respMeta")):
        return "status.metadata.id"
    if ov.get("statusId"):
        return ov["statusId"]
    if r.get("respId"):
        return f"status.{r['respId']}"
    if ov.get("idField"):
        return f"spec.{ov['idField']}"
    return "status.id"


def id_mapping_for(r, idp):
    return [{"inPath": idp, "inCustomResource": id_target(r)}]


def config_fields(r):
    fields = [{
        "fromOpenAPI": {"name": "api-version", "in": "query"},
        "fromRestDefinition": {"actions": ["*"]},
    }]
    for q in r["list_q"]:
        if q == "api-version":
            continue
        fields.append({
            "fromOpenAPI": {"name": q, "in": "query"},
            "fromRestDefinition": {"actions": ["findby"]},
        })
    for q in r["get_q"]:
        if q == "api-version":
            continue
        fields.append({
            "fromOpenAPI": {"name": q, "in": "query"},
            "fromRestDefinition": {"actions": ["get"]},
        })
    return fields


def render(r):
    ov = r["ov"]
    verbs = []
    excluded = set()  # item id path params sourced from status/spec, not user input

    if r["has_list"]:
        verbs.append({"action": "findby", "method": "GET", "path": r["coll"]})
    if r["get_op"]:
        path, idp = r["get_op"]
        v = {"action": "get", "method": "GET", "path": path,
             "requestFieldMapping": id_mapping_for(r, idp)}
        verbs.append(v)
        if not ov.get("idField"):
            excluded.add(idp)
        elif idp != ov["idField"]:
            excluded.add(idp)
    if r["has_create"]:
        create = {"action": "create", "method": "POST", "path": r["coll"]}
        # Body fields sourced from a Secret rather than from the spec. The field is
        # dropped from the CRD entirely, so there is no plaintext-in-spec form of it
        # left to use by mistake -- the resolver is the only way to supply it.
        for sf in ov.get("secretFields", []):
            create.setdefault("fieldMapping", []).append({
                "inBody": sf["inBody"],
                "resolver": {
                    "type": "secretRef",
                    "secretRef": {"nameFromCustomResource": sf["nameFrom"],
                                  "keyFromCustomResource": sf["keyFrom"]},
                },
            })
            excluded.add(sf["inBody"])
        verbs.append(create)
    if r["put_op"]:
        path, idp = r["put_op"]
        verbs.append({"action": "update", "method": "PUT", "path": path,
                      "requestFieldMapping": id_mapping_for(r, idp)})
        if not ov.get("idField") or idp != ov["idField"]:
            excluded.add(idp)
    if r["del_op"]:
        path, idp = r["del_op"]
        verbs.append({"action": "delete", "method": "DELETE", "path": path,
                      "requestFieldMapping": id_mapping_for(r, idp)})
        if not ov.get("idField") or idp != ov["idField"]:
            excluded.add(idp)

    # Async readiness: attach the async block to the matching mutating verb. This
    # turns an asynchronous API into a synchronous, level-based reconcile — the
    # controller-native form of a provisioning wait.
    a = ov.get("async_")
    if a:
        opref = a["operationRef"]
        async_cfg = {
            "mode": a.get("mode", "requeue"),
            "operationRef": {"in": opref["In"], "path": opref["path"]},
            "poll": a["poll"],
        }
        if opref.get("jq"):
            async_cfg["operationRef"]["jq"] = opref["jq"]
        if a.get("postGet"):
            async_cfg["postGet"] = True
        for v in verbs:
            if v["action"] == a["action"]:
                v["async"] = async_cfg

    # Whole-document gojq applied to a response BEFORE drift comparison. This is the
    # declarative answer to an API that returns a different shape than it accepts:
    # without it the comparison is unsatisfiable and the controller updates forever.
    for action, program in (ov.get("responseTransform") or {}).items():
        for v in verbs:
            if v["action"] == action:
                v["responseTransform"] = {"inline": program}

    # A gojq predicate that decides ABSENCE from a 2xx body. Needed for an API that
    # answers GET for a deleted resource with 200 and a tombstone instead of 404: the
    # status code alone then reads as "still present" and the CR wedges in Deleting
    # forever. RDC consults this on both observe and delete verification, so the two
    # share one definition of absence (oasgen#111).
    for action, program in (ov.get("notFoundBody") or {}).items():
        for v in verbs:
            if v["action"] == action:
                v["notFoundBody"] = {"inline": program}

    resource = {"kind": r["kind"]}
    if r["meta"]:
        resource["identifiers"] = [ov.get("metaIdent", "metadata.name")]
        # Also for read-only resources when the response carries metadata: their get
        # verb binds {id} from status.metadata.id, and without this the field is never
        # populated, so the mapping is dead config and get can never run.
        if not r["readonly"] or r.get("respMeta"):
            resource["additionalStatusFields"] = ["metadata.id"]
    elif not r["readonly"]:
        resource["identifiers"] = [ov.get("idField", "name")]
        # The status field must be the key the API's own response uses. Assuming "id"
        # for every flat resource silently breaks the ones that return keyId/kmipId:
        # status is never populated, so the CR cannot go Ready and its delete cannot
        # address the resource -- which orphans it. See security/keys.
        tgt = id_target(r)
        if tgt.startswith("status."):
            resource["additionalStatusFields"] = [tgt.split(".", 1)[1]]
    else:
        # Read-only resources have no create body, so `meta` was forced False upstream
        # and this branch used to assume a flat item. That is wrong whenever the
        # findby RESPONSE is metadata-wrapped: network/LoadBalancer returns
        # {metadata:{id,name}, status, properties}, so identifiers: [name] can never
        # match -- RDC looks for a top-level `name` that does not exist. The response
        # is the authority here exactly as it is for the id field.
        if r.get("respMeta"):
            resource["identifiers"] = ["metadata.name"]
            resource["additionalStatusFields"] = ["metadata.id"]
        else:
            resource["identifiers"] = ["name"]

    # Appended LAST, and append-only is not incidental: AdditionalStatusFields carries
    # a CEL rule allowing new entries at the end but no removal, rename or reorder of
    # existing ones once the CRD has been generated. Putting it anywhere but the end
    # would make this an immutable-field rejection on every existing cluster.
    if r.get("respFailure"):
        resource.setdefault("additionalStatusFields", []).append("status.failureReason")

    # Appended after failureReason, and last, because additionalStatusFields is
    # append-only under the CRD's CEL rule: an existing RestDefinition accepts the
    # new entry at the end, where inserting it earlier would be rejected.
    if r.get("respError"):
        resource.setdefault("additionalStatusFields", []).append("status.error")

    if ov.get("compareScope"):
        # An explicit scope is a statement about an API the derivation cannot see --
        # e.g. a server-assigned name that makes fullSpec diverge forever. It wins.
        resource["compareScope"] = ov["compareScope"]
    elif r.get("updatableScope"):
        resource["compareScope"] = "updatable"

    if excluded:
        resource["excludedSpecFields"] = sorted(excluded)

    # Delegate mutating verbs to Snowplow RESTActions (multi-call lifecycle) while
    # keeping observe native. createApiRef requires get/findby, which we retain.
    apirefs = ov.get("apiRefs")
    if apirefs:
        verbs = [v for v in verbs if v["action"] in ("findby", "get")]
        ns = apirefs["namespace"]
        extras = apirefs.get("extras")
        for verb_key, field in (("create", "createApiRef"),
                                ("update", "updateApiRef"),
                                ("delete", "deleteApiRef")):
            name = apirefs.get(verb_key)
            if not name:
                continue
            ref = {"name": name, "namespace": ns}
            # delete may need richer static extras: the CR spec is NOT forwarded on
            # delete-direction invocations (RDC buildExtras), so parent-scoping
            # values (e.g. projectId) must be static.
            verb_extras = apirefs.get(f"{verb_key}_extras", extras)
            if verb_extras:
                ref["extras"] = dict(verb_extras)  # independent copy -> no YAML anchors
            resource[field] = ref

    resource["verbsDescription"] = verbs
    cfg = config_fields(r)
    if cfg:
        resource["configurationFields"] = cfg

    rd = {
        "apiVersion": "ogen.krateo.io/v1alpha1",
        "kind": "RestDefinition",
        "metadata": {"name": f"arubacloud-{r['provider']}-{r['kind'].lower()}"},
        "spec": {
            "oasPath": f"configmap://{CM_NS}/arubacloud-{r['provider']}-openapi/{r['provider']}.json",
            "resourceGroup": GROUP,
            "resource": resource,
        },
    }
    return rd


def main():
    summary = []
    for provider, fn in PROVIDERS.items():
        spec = json.load(open(os.path.join(OAS, fn)))
        os.makedirs(os.path.join(OUT, provider), exist_ok=True)
        for r in build(provider, spec):
            rd = render(r)
            header = [f"# Aruba Cloud {r['kind']} ({provider} provider) - generated, no proxy."]
            if r["ov"].get("note"):
                header.append("# " + r["ov"]["note"].replace("\n", "\n# "))
            path = os.path.join(OUT, provider, f"{r['kind'].lower()}.yaml")
            with open(path, "w") as f:
                f.write("\n".join(header) + "\n")
                yaml.safe_dump(rd, f, sort_keys=False, default_flow_style=False, width=100)
            verbs = ",".join(v["action"] for v in rd["spec"]["resource"]["verbsDescription"])
            summary.append((provider, r["kind"], verbs, "meta" if r["meta"] else "flat",
                            "RO" if r["readonly"] else ""))
    summary.sort()
    print(f"{'PROVIDER':10} {'KIND':22} {'VERBS':34} {'BODY':6} RO")
    for row in summary:
        print(f"{row[0]:10} {row[1]:22} {row[2]:34} {row[3]:6} {row[4]}")
    print(f"\nTotal RestDefinitions generated: {len(summary)}")


if __name__ == "__main__":
    main()
