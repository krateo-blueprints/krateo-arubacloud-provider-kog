#!/usr/bin/env python3
"""Generate per-provider reference documentation under docs/providers/ from the
patched OpenAPI specs and the generated RestDefinitions. Reproducible: rerun
after regenerating the RestDefinitions.

For each provider it emits, per resource: the wired verbs (action -> METHOD path),
identifiers/status/excluded fields, any *ApiRef delegation, the per-verb query
configuration, the generator's note, and a link to the sample. It also lists the
API operations that are intentionally NOT turned into a resource (action-only and
list-only endpoints) so coverage is auditable.
"""
import glob
import json
import os
import re

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
OAS = os.path.join(ROOT, "openapi")
RD = os.path.join(ROOT, "restdefinitions")
DOCS = os.path.join(ROOT, "docs", "providers")

# short provider name -> published filename (consumed UNMODIFIED)
PROVIDERS = {'network': 'network-provider.json',
    'compute': 'compute-provider.json',
    'container': 'container-provider.json',
    'database': 'database-provider.json',
    'storage': 'storage-provider.json',
    'security': 'security-provider.json',
    'schedule': 'schedule-provider.json',
    'baremetal': 'baremetal-provider.json',
    'project': 'project.json',
    'metering': 'metering.json'}
TITLES = {
    "network": "Network", "compute": "Compute", "container": "Container (Kubernetes)",
    "database": "Database (DBaaS)", "storage": "Storage", "security": "Security (KMS)",
    "schedule": "Schedule", "baremetal": "Bare Metal", "project": "Project", "metering": "Metering (Insight)",
}


def rd_note(path):
    """First contiguous comment block at the top of a generated RD file (minus the title line)."""
    lines = []
    for ln in open(path):
        if ln.startswith("#"):
            lines.append(ln.lstrip("#").strip())
        else:
            break
    return " ".join(lines[1:]) if len(lines) > 1 else ""


def covered_paths(rds):
    """Endpoints exercised by a resource: its native verbs, plus the create/
    update/delete endpoints delegated to RESTActions via *ApiRef (derived from the
    resource's collection/item paths, which are the findby/get verb paths)."""
    out = set()
    for _, doc in rds:
        r = doc["spec"]["resource"]
        verbs = {v["action"]: v for v in r["verbsDescription"]}
        for v in verbs.values():
            out.add((v["method"].upper(), v["path"]))
        coll = verbs.get("findby", {}).get("path")
        item = verbs.get("get", {}).get("path")
        if "createApiRef" in r and coll:
            out.add(("POST", coll))
        if "updateApiRef" in r and item:
            out.add(("PUT", item))
        if "deleteApiRef" in r and item:
            out.add(("DELETE", item))
    return out


def load_rds(provider):
    d = os.path.join(RD, provider)
    items = []
    for fn in sorted(os.listdir(d)):
        if fn.endswith(".yaml"):
            p = os.path.join(d, fn)
            items.append((rd_note(p), yaml.safe_load(open(p))))
    return items


def op_summary(spec, path, method):
    op = spec["paths"].get(path, {}).get(method.lower(), {})
    return op.get("summary", "")


def main():
    os.makedirs(DOCS, exist_ok=True)
    index_rows = []
    for prov, fn in PROVIDERS.items():
        spec = json.load(open(os.path.join(OAS, fn)))
        rds = load_rds(prov)
        info = spec.get("info", {})
        ss = list((spec.get("components", {}).get("securitySchemes") or {}).keys())
        out = [f"# Aruba Cloud {TITLES[prov]} provider\n"]
        out.append(f"- **OpenAPI**: `{info.get('title')}` v{info.get('version')} "
                   f"(`openapi/_source/{fn}` → patched `openapi/{fn}`)\n"
                   f"- **Security scheme (patched)**: `{', '.join(ss)}` (HTTP Bearer)\n"
                   f"- **ConfigMap**: `arubacloud-{prov}-openapi` in `krateo-system`\n"
                   f"- **Resources**: {len(rds)}\n")
        out.append("\n| Kind | Verbs | Identifier(s) | Delegation |\n|------|-------|---------------|------------|")
        for note, doc in rds:
            r = doc["spec"]["resource"]
            verbs = ", ".join(v["action"] for v in r["verbsDescription"])
            refs = [k.replace("ApiRef", "") for k in ("createApiRef", "updateApiRef", "deleteApiRef") if k in r]
            deleg = ", ".join(refs) + " → RESTAction" if refs else "—"
            out.append(f"| `{r['kind']}` | {verbs} | `{', '.join(r.get('identifiers', []))}` | {deleg} |")
        out.append("")

        for note, doc in rds:
            r = doc["spec"]["resource"]
            kind = r["kind"]
            out.append(f"\n## {kind}\n")
            if note:
                out.append(f"> {note}\n")
            out.append("| Verb | Method | Path |\n|------|--------|------|")
            for v in r["verbsDescription"]:
                out.append(f"| {v['action']} | {v['method']} | `{v['path']}` |")
            for verb_key, field in (("create", "createApiRef"), ("update", "updateApiRef"), ("delete", "deleteApiRef")):
                if field in r:
                    ref = r[field]
                    out.append(f"| {verb_key} | *(delegated)* | RESTAction `{ref['name']}` |")
            meta = []
            if r.get("additionalStatusFields"):
                meta.append(f"status fields: `{', '.join(r['additionalStatusFields'])}`")
            if r.get("excludedSpecFields"):
                meta.append(f"excluded from spec: `{', '.join(r['excludedSpecFields'])}`")
            if meta:
                out.append("\n" + " · ".join(meta))
            cfg = r.get("configurationFields", [])
            if cfg:
                names = sorted({c["fromOpenAPI"]["name"] for c in cfg})
                out.append(f"\nConfiguration query params: `{', '.join(names)}`")
            out.append(f"\nSample: [`samples/{prov}/{kind.lower()}.yaml`](../../samples/{prov}/{kind.lower()}.yaml) · "
                       f"[`{kind.lower()}-configuration.yaml`](../../samples/{prov}/{kind.lower()}-configuration.yaml)")

        # uncovered endpoints (audit trail)
        cov = covered_paths(rds)
        # add delegated action paths as "covered by RESTAction" so they aren't flagged
        uncovered = []
        for path, ops in sorted(spec.get("paths", {}).items()):
            for method in ("get", "post", "put", "patch", "delete"):
                if method in ops and (method.upper(), path) not in cov:
                    uncovered.append((method.upper(), path, ops[method].get("summary", "")))
        if uncovered:
            out.append("\n## Endpoints not exposed as a resource\n")
            out.append("Action-only, list-only or delegated endpoints (see "
                       "[lifecycle-beyond-crud](../lifecycle-beyond-crud.md) and "
                       "[coverage](../coverage.md)):\n")
            out.append("| Method | Path | Summary |\n|--------|------|---------|")
            for m, p, s in uncovered:
                out.append(f"| {m} | `{p}` | {s} |")

        out.append("")
        with open(os.path.join(DOCS, f"{prov}.md"), "w") as f:
            f.write("\n".join(out))
        index_rows.append((prov, len(rds), len(uncovered)))
        print(f"  wrote docs/providers/{prov}.md ({len(rds)} resources)")

    # index
    idx = ["# Provider reference\n",
           "Per-provider reference for the Aruba Cloud KOG RestDefinitions. "
           "Generated by `scripts/gen_provider_docs.py`.\n",
           "| Provider | Resources | Uncovered endpoints |",
           "|----------|-----------|---------------------|"]
    for prov, n, unc in index_rows:
        idx.append(f"| [{TITLES[prov]}]({prov}.md) | {n} | {unc} |")
    idx.append("")
    with open(os.path.join(DOCS, "README.md"), "w") as f:
        f.write("\n".join(idx))
    print(f"wrote docs/providers/README.md ({len(index_rows)} providers)")


if __name__ == "__main__":
    main()
