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
# See docs/ga-readiness.md for what each tier claims about production use.
TIERS = {
    ("network", "Subnet"): (
        "ga",
        "full lifecycle live — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "Vpc"): (
        "ga",
        "full lifecycle live incl. drift correction — [live-cluster-test](live-cluster-test.md)",
    ),
    ("compute", "KeyPair"): (
        "ga",
        "full lifecycle live — [live-cluster-test](live-cluster-test.md)",
    ),
    ("project", "Project"): (
        "ga",
        "full lifecycle live incl. drift correction — [live-cluster-test](live-cluster-test.md)",
    ),
    ("project", "Folder"): (
        "beta",
        "create/observe/delete proven live; drift **not** exercised — its only mutable "
        "spec fields are the identifier itself and an account-wide `default` flag, "
        "neither safe to perturb — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "SecurityGroup"): (
        "ga",
        "full lifecycle live incl. drift correction — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "SecurityRule"): (
        "ga",
        "full lifecycle live incl. drift correction — [live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "VpcPeering"): (
        "ga",
        "full lifecycle live incl. drift correction, free (no `billingPlan`) — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("network", "VpcPeeringRoute"): (
        "beta",
        "create/observe/delete proven live in a 6-resource chain (two VPCs, two "
        "**Advanced** subnets whose CIDRs the route references, a peering); drift not "
        "yet exercised — **billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("schedule", "BackupPolicy"): (
        "ga",
        "full lifecycle live incl. drift correction — [live-cluster-test](live-cluster-test.md)",
    ),
    ("schedule", "Job"): (
        "blocked",
        "a step's only supported actions are **`poweron` / `poweroff` via POST** "
        "(GET is rejected: *All steps must have a correct HttpVerb defined*), so a Job "
        "requires a `CloudServer` to target — none exists and CloudServer creation is "
        "itself blocked on snowplow (P0-3)",
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
        "as the resources with no update verb — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("security", "Kms"): (
        "ga",
        "full lifecycle in one clean run incl. drift correction (`6a9ae569`) — "
        "**billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("security", "Key"): (
        "ga",
        "create → observe → delete proven live (`b6ae8eee`). Drift is **not "
        "applicable**: its update body is `{name}` only, and `name` is the identifier, "
        "so there is no non-identifying field to converge — the same reasoning as "
        "`Restore` — **billable parent** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("security", "Kmip"): (
        "experimental",
        "same `kmipId` status mapping correction as Key; not yet run",
    ),
    ("container", "Kaas"): (
        "beta",
        "cluster created and reached **Active** upstream (`6a9aa956`, K2A4 / 1.33.2) "
        "and deleted cleanly, but the CR never reported `Ready` — "
        "**billable** ~EUR 0.076/hr — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "Dbaas"): (
        "beta",
        "create/observe/delete proven live (`6a9a846d`, mysql-8.0 / DBO1A2 / 20 GB); "
        "drift injection rejected with 400 — its update body will not accept a full "
        "re-PUT — **billable** — [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "Database"): (
        "beta",
        "create/observe proven live (name-keyed, `status.name = gadb`); deleted with "
        "its parent rather than individually — **billable parent** — "
        "[live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "DatabaseUser"): (
        "blocked",
        "**password policy is undeclared and unguessable** — the OAS gives `password` "
        "no `minLength` or `pattern`, and the API rejects even Aruba's own SDK example "
        "value; see [live-cluster-test](live-cluster-test.md)",
    ),
    ("database", "Grant"): (
        "blocked",
        "depends on `DatabaseUser`, which cannot be created",
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
        "metadata-wrapped), but the generated selector is a **flat** key while RDC "
        "reads it nested — [oasgen-provider#106]"
        "(https://github.com/krateo-platformops/oasgen-provider/issues/106) — so "
        "`findby` cannot match. Separately, no load balancer can be created: the API "
        "has no create verb, and one exists only as a side effect of a Service of type "
        "LoadBalancer inside a KaaS cluster",
    ),
    ("compute", "CloudServer"): (
        "experimental",
        "gained a `metadata.name` selector on 0.22.1; RESTActions still never "
        "executed — [P0-3](ga-readiness.md#blockers)",
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
            if ident in excluded or ident in spec_body:
                continue
            # A dotted identifier becomes a FLAT key containing the dot -- oasgen emits
            # `metadata.name` as one property, not a nested metadata object. Nesting it
            # is rejected by strict decoding as `unknown field "spec.metadata"`.
            #
            # This mirrors a defect, not a design: RDC reads the identifier NESTED
            # (spec.metadata.name), so the flat key it is given can never match. Filed
            # as oasgen-provider#106. When that lands, this must emit a nested object
            # instead -- and the samples will need regenerating with it.
            spec_body[ident] = "REPLACE_selects_the_existing_object_by_this_value"
    return cr


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
        dump_yaml(make_cr(specs[prov], prov, doc, apiver), os.path.join(d, f"{kind.lower()}.yaml"))
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
            "timestamp: 2026-08-19T00:00:00Z\n"
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
        f.write("Only **GA** claims fitness for production use. See "
                "[ga-readiness](ga-readiness.md).\n\n")

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
