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
SKIP = {"schedule/executions"}

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
    "jobs": "Job", "alertRules": "AlertRule",
    "blockStorages": "BlockStorage", "snapshots": "Snapshot", "restores": "Restore",
    "backups": "Backup", "hpcs": "Hpc", "folders": "Folder", "projects": "Project",
}

# Which spec file each provider resource set comes from and the resource kind
# prefix used to disambiguate collections that repeat a name (e.g. "backups"
# exists in database, storage and container).
PROVIDERS = {
    "network": "network.json",
    "compute": "compute.json",
    "container": "container.json",
    "database": "database.json",
    "storage": "storage.json",
    "security": "security.json",
    "schedule": "schedule.json",
    "baremetal": "baremetal.json",
    "project": "project.json",
    "metering": "metering.json",
}

# Explicit overrides for resources that do not follow the metadata-wrapper CRUD
# pattern. Keyed by "<provider>/<seg>".
#   idField      - natural key field in the CR spec (identifier)
#   statusId     - JSONPath in status holding the server id ("" = none)
#   metaWrap     - True if the create body wraps name in a metadata object
#   secretFields - body fields to source from a Kubernetes Secret via secretRef
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
    "database/users": dict(
        metaWrap=False, idField="username", statusId="",
        note=("DatabaseUser is name-keyed (create {username, password}). 'password' is a "
              "plaintext spec field in the source OAS; sourcing it from a Kubernetes "
              "Secret needs the secretRef resolver plus an OAS-declared *SecretRef field "
              "- see docs/oasgen-provider-evolution.md. No update verb (password change "
              "is a separate PUT .../password sub-endpoint).")),
    "database/grants": dict(
        metaWrap=False, idField="user", statusId="",
        note=("Grant is name-keyed (create {user, role}); item path segment is "
              "{username}. No update/dedicated id.")),
    "security/keys": dict(
        metaWrap=False, idField="name", statusId="status.id",
        note="Key create body is flat {name, algorithm}; item keyed by server id {keyId}."),
    "security/kmip": dict(
        metaWrap=False, idField="name", statusId="status.id",
        note="Kmip create body is flat {name}; item keyed by server id {kmipId}."),
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

        readonly = key in READONLY or not has_create
        results.append(dict(
            provider=provider, kind=kind, seg=seg, coll=coll, ov=ov, meta=meta,
            readonly=readonly, has_list=has_list, has_create=has_create,
            get_op=get_op, put_op=put_op, del_op=del_op,
            get_q=query_params(spec, get_op[0], "get") if get_op else [],
            list_q=query_params(spec, coll, "get") if has_list else [],
        ))
    return results


def id_target(r):
    """Where the item {id} path parameter should be sourced from in the CR."""
    ov = r["ov"]
    if r["meta"]:
        return "status.metadata.id"
    if ov.get("statusId"):
        return ov["statusId"]
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
        verbs.append({"action": "create", "method": "POST", "path": r["coll"]})
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

    resource = {"kind": r["kind"]}
    if r["meta"]:
        resource["identifiers"] = ["metadata.name"]
        if not r["readonly"]:
            resource["additionalStatusFields"] = ["metadata.id"]
    elif not r["readonly"]:
        resource["identifiers"] = [ov.get("idField", "name")]
        if id_target(r) == "status.id":
            resource["additionalStatusFields"] = ["id"]
    else:  # read-only, flat
        resource["identifiers"] = ["name"]

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
