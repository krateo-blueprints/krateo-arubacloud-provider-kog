#!/usr/bin/env python3
"""Emit one ConfigMap per provider embedding its patched OpenAPI document, so the
``oasPath: configmap://krateo-system/arubacloud-<provider>-openapi/<provider>.json``
reference in every RestDefinition resolves inside the cluster.

Only providers that actually have generated RestDefinitions get a ConfigMap.
"""
import json
import os

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
OAS = os.path.join(ROOT, "openapi")
OUT = os.path.join(ROOT, "configmaps")
NS = "krateo-system"

PROVIDERS = ["network", "compute", "container", "database", "storage",
             "security", "schedule", "baremetal", "project", "metering"]


def main():
    os.makedirs(OUT, exist_ok=True)
    for p in PROVIDERS:
        spec = json.load(open(os.path.join(OAS, f"{p}.json")))
        # compact JSON keeps the ConfigMap well under the 1 MiB object limit
        content = json.dumps(spec, separators=(",", ":"))
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"arubacloud-{p}-openapi", "namespace": NS},
            "data": {f"{p}.json": content},
        }
        with open(os.path.join(OUT, f"{p}-openapi.yaml"), "w") as f:
            yaml.safe_dump(cm, f, sort_keys=False, width=10**9)
        print(f"  wrote configmaps/{p}-openapi.yaml ({len(content)//1024} KiB)")


if __name__ == "__main__":
    main()
