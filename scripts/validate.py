#!/usr/bin/env python3
"""Static validation of the generated artifacts (no cluster needed):
  * every RestDefinition verb path+method exists in its referenced patched OAS;
  * every requestFieldMapping.inPath is a real path parameter of that verb's path;
  * every oasPath configmap:// reference resolves to a generated ConfigMap;
  * all RestDefinition / ConfigMap / sample / restaction YAML is parseable;
  * every Composition chart passes `helm lint` and renders valid YAML with
    `helm template` (skipped with a warning if the helm binary is absent).
Exits non-zero if anything fails.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
PROVIDERS = ["network", "compute", "container", "database", "storage",
             "security", "schedule", "baremetal", "project", "metering"]


def main():
    os.chdir(ROOT)
    errors = []
    specs = {p: json.load(open(f"openapi/{p}.json")) for p in PROVIDERS}
    cm_names = {yaml.safe_load(open(f))["metadata"]["name"]
                for f in glob.glob("configmaps/*.yaml")}

    rds = glob.glob("restdefinitions/**/*.yaml", recursive=True)
    for f in rds:
        doc = yaml.safe_load(open(f))
        prov = f.split(os.sep)[1]
        spec = specs[prov]
        cm = doc["spec"]["oasPath"].split("/")[3]
        if cm not in cm_names:
            errors.append(f"{f}: oasPath ConfigMap '{cm}' not found")
        for v in doc["spec"]["resource"]["verbsDescription"]:
            p, m = v["path"], v["method"].lower()
            if p not in spec["paths"]:
                errors.append(f"{f}: path missing in OAS: {p}")
            elif m not in spec["paths"][p]:
                errors.append(f"{f}: method {m.upper()} not on {p}")
            pps = re.findall(r"\{([^}]+)\}", p)
            for rfm in v.get("requestFieldMapping", []):
                if "inPath" in rfm and rfm["inPath"] not in pps:
                    errors.append(f"{f}: {v['action']} inPath '{rfm['inPath']}' not in {pps}")
            # async poll endpoint must exist in the OAS (param-name agnostic: the
            # {operationId} token maps onto whatever the OAS calls that segment)
            poll = v.get("async", {}).get("poll", {})
            if poll.get("path"):
                norm = lambda s: re.sub(r"\{[^}]+\}", "{}", s)
                oas_norm = {norm(k) for k in spec["paths"]}
                if norm(poll["path"]) not in oas_norm:
                    errors.append(f"{f}: async poll path not in OAS: {poll['path']}")
                if not poll.get("statusPath") or not poll.get("successValues"):
                    errors.append(f"{f}: async poll missing statusPath/successValues")

    for f in (glob.glob("samples/**/*.yaml", recursive=True)
              + glob.glob("configmaps/*.yaml")
              + glob.glob("restactions/**/*.yaml", recursive=True)):
        try:
            list(yaml.safe_load_all(open(f)))
        except Exception as e:
            errors.append(f"{f}: {e}")

    charts = validate_charts(errors)

    print(f"Checked {len(rds)} RestDefinitions, {len(cm_names)} ConfigMaps, {charts} chart(s).")
    if errors:
        print(f"{len(errors)} ERROR(S):")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("OK: verb paths/methods exist in their OAS; oasPaths resolve; YAML valid; charts render.")


def find_helm():
    return shutil.which("helm") or next(
        (p for p in ("/root/go/bin/helm", os.path.expanduser("~/go/bin/helm"))
         if os.path.isfile(p)), None)


def validate_charts(errors):
    """helm lint + helm template each Composition chart; render must be valid YAML."""
    chart_dirs = sorted(os.path.dirname(f) for f in glob.glob("compositions/**/Chart.yaml", recursive=True))
    if not chart_dirs:
        return 0
    helm = find_helm()
    if not helm:
        print("WARNING: helm not found (PATH or ~/go/bin) - skipping chart lint/template.")
        print("         install with: go install helm.sh/helm/v3/cmd/helm@latest")
        return 0
    for d in chart_dirs:
        lint = subprocess.run([helm, "lint", d], capture_output=True, text=True)
        if lint.returncode != 0:
            errors.append(f"helm lint failed for {d}:\n{lint.stdout}{lint.stderr}")
            continue
        tmpl = subprocess.run([helm, "template", "validate", d], capture_output=True, text=True)
        if tmpl.returncode != 0:
            errors.append(f"helm template failed for {d}:\n{tmpl.stderr}")
            continue
        try:
            docs = [x for x in yaml.safe_load_all(tmpl.stdout) if x]
        except Exception as e:
            errors.append(f"{d}: rendered output is not valid YAML: {e}")
            continue
        if not docs:
            errors.append(f"{d}: helm template rendered no resources")
    return len(chart_dirs)


if __name__ == "__main__":
    main()
