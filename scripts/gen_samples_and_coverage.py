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
APIVER = f"{GROUP}/v1alpha1"


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


def make_configuration(kind, verbs):
    q = {}
    for v in verbs:
        entry = {"api-version": "1.0"}
        if v == "get":
            entry["ignoreDeletedStatus"] = False
        q[v] = entry
    return {
        "apiVersion": APIVER,
        "kind": f"{kind}Configuration",
        "metadata": {"name": f"{kind.lower()}-config", "namespace": "default"},
        "spec": {
            "authentication": {"bearer": {"tokenRef": {
                "name": "arubacloud-token", "namespace": "default", "key": "token"}}},
            "configuration": {"query": q},
        },
    }


def make_cr(spec, prov, doc):
    res = doc["spec"]["resource"]
    kind = res["kind"]
    create = next((v for v in res["verbsDescription"] if v["action"] == "create"), None)
    meta = any(i == "metadata.name" for i in res.get("identifiers", []))
    cr = {
        "apiVersion": APIVER,
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
        dump_yaml(make_configuration(kind, verbs),
                  os.path.join(d, f"{kind.lower()}-configuration.yaml"))
        dump_yaml(make_cr(specs[prov], prov, doc), os.path.join(d, f"{kind.lower()}.yaml"))
        ids = ",".join(res.get("identifiers", []))
        rows.append((prov, kind, ",".join(verbs), ids))

    rows.sort()
    with open(os.path.join(DOCS, "coverage.md"), "w") as f:
        f.write("# Aruba Cloud KOG — resource coverage matrix\n\n")
        f.write("Generated by `scripts/gen_samples_and_coverage.py` from the "
                "RestDefinitions under `restdefinitions/`. No proxy/plugin is used "
                "for any resource.\n\n")
        f.write(f"**{len(rows)} manageable resources** across 10 providers.\n\n")
        f.write("| Provider | Kind | Verbs | Identifier(s) |\n")
        f.write("|----------|------|-------|---------------|\n")
        for prov, kind, verbs, ids in rows:
            f.write(f"| {prov} | `{kind}` | {verbs} | `{ids}` |\n")
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
