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

# short provider name -> published filename (consumed UNMODIFIED)
PROVIDERS = {'network': 'network-provider.json',
             'compute': 'compute-provider.json',
             # Aruba splits CloudServer across two API versions: everything except
             # create is in compute-provider.json (1.0.0), and POST .../cloudServers
             # exists ONLY in compute-provider_v1.1.json (1.1.0). The 1.1 document
             # contains that single path and nothing else. It is vendored and was never
             # shipped as a ConfigMap, so oasgen could not see the create endpoint at
             # all -- which is why this repo concluded CloudServer had none.
             'compute11': 'compute-provider_v1.1.json',
             'container': 'container-provider.json',
             'database': 'database-provider.json',
             'storage': 'storage-provider.json',
             'security': 'security-provider.json',
             'schedule': 'schedule-provider.json',
             'baremetal': 'baremetal-provider.json',
             'project': 'project.json',
             'metering': 'metering.json'}


def main():
    os.makedirs(OUT, exist_ok=True)
    for p, fn in PROVIDERS.items():
        spec = json.load(open(os.path.join(OAS, fn)))
        # compact JSON keeps the ConfigMap well under the 1 MiB object limit
        content = json.dumps(spec, separators=(",", ":"))
        cm = {
            "apiVersion": "v1",
            "kind": "ConfigMap",
            "metadata": {"name": f"arubacloud-{p}-openapi", "namespace": NS},
            # The ConfigMap KEY is ours to choose and must be hyphen-free: the RestDefinition
        # CRD validates oasPath with [a-zA-Z0-9.-_] for this segment, which is a RANGE
        # ('.' to '_') and therefore excludes '-'. Aruba's filename (network-provider.json)
        # would be rejected. The file on disk keeps its published name; only the key differs.
        "data": {f"{p}.json": content},
        }
        with open(os.path.join(OUT, f"{p}-openapi.yaml"), "w") as f:
            yaml.safe_dump(cm, f, sort_keys=False, width=10**9)
        print(f"  wrote configmaps/{p}-openapi.yaml ({len(content)//1024} KiB)")


if __name__ == "__main__":
    main()
