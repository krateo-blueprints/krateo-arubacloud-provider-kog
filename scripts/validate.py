#!/usr/bin/env python3
"""Static validation of the generated artifacts (no cluster needed):
  * every RestDefinition verb path+method exists in its referenced patched OAS;
  * every requestFieldMapping.inPath is a real path parameter of that verb's path;
  * every oasPath configmap:// reference resolves to a generated ConfigMap;
  * all RestDefinition / ConfigMap / sample YAML is parseable.
Exits non-zero if anything fails.
"""
import glob
import json
import os
import re
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

    for f in glob.glob("samples/**/*.yaml", recursive=True) + glob.glob("configmaps/*.yaml"):
        try:
            yaml.safe_load(open(f))
        except Exception as e:
            errors.append(f"{f}: {e}")

    print(f"Checked {len(rds)} RestDefinitions, {len(cm_names)} ConfigMaps.")
    if errors:
        print(f"{len(errors)} ERROR(S):")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("OK: all verb paths/methods exist in their OAS; all oasPaths resolve; all YAML valid.")


if __name__ == "__main__":
    main()
