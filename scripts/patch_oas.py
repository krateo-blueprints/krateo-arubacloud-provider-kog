#!/usr/bin/env python3
"""Patch the vendored Aruba Cloud OpenAPI specs so they can be consumed by the
Krateo oasgen-provider (krateo fork) WITHOUT any wrapper/proxy web service.

The original specs live under ``openapi/_source/`` exactly as published at
``https://api.arubacloud.com/openapi/<provider>.json``. This script rewrites the
subset of OAS constructs that oasgen-provider cannot consume today and emits the
result under ``openapi/``.

Every transformation performed here is a *symptom* of an oasgen-provider gap:
each one is counted and reported so ``docs/oasgen-provider-evolution.md`` can be
regenerated from ground truth. If oasgen-provider evolves to support a construct,
the corresponding transformation can simply be dropped.

Transformations
---------------
1. securityScheme: ``apiKey``-in-header Bearer  ->  ``http``/``bearer``.
   oasgen-provider only wires Bearer/Basic from an ``http`` security scheme;
   the Aruba specs declare the token as a raw ``apiKey`` header, which would
   never be treated as an auth credential.
2. strip ``nullable: true`` (unsupported; OAS 3.0 construct removed in 3.1).
3. strip ``readOnly`` / ``writeOnly`` (unsupported).
4. merge the ``compute-provider_v1.1.json`` ``POST /cloudServers`` (create) into
   ``compute-provider.json`` so the CloudServer resource has a create verb in a
   single document (oasgen references one OAS document per RestDefinition).
   (Verified safe: the two documents share 67 schema names with ZERO differing
   definitions, so first-wins merging is lossless.)

Retired transformations (the upstream gap was fixed; requires oasgen >= 0.18.0)
-------------------------------------------------------------------------------
* ``additionalProperties: {<schema>}`` -> ``additionalProperties: true``.
  Dropped: oasgen 0.18.0 carries the object form through to crdgen as a typed
  map (``libopenapi_adapter.go`` recurses the value schema; ``helpers.go``
  emits it), so Aruba's 42 typed maps — ``metadata.annotations``,
  ``metadata.labels``, ``AlertModel.labels``, ``AlertAction.parameters``,
  ``CloudServerNetworkInterfaceDto.properties`` — keep their value type and
  validation instead of degrading to untyped objects. (oasgen-provider#45)
* rename the baremetal async-monitor path parameter ``{id}`` -> ``{operationId}``.
  Dropped: ``async.poll.handleParam`` (oasgen 0.18.0 + RDC 0.18.0) declares
  WHICH path parameter receives the operation handle, so Aruba's
  ``.../monitor/{id}`` is used exactly as published. (oasgen-provider#46)

Constructs left untouched on purpose (documented, not silently "fixed"):
* ``format`` (int32/int64/double/date-time/uuid/uri) - appended to the field
  description by oasgen, harmless.
* ``number`` / ``format: double`` - coerced to integer by oasgen; flagged.
* ``allOf`` - merged by oasgen.
"""
import json
import os
import sys
from collections import Counter

SRC = os.path.join(os.path.dirname(__file__), "..", "openapi", "_source")
OUT = os.path.join(os.path.dirname(__file__), "..", "openapi")

stats = Counter()


def strip_constructs(node):
    """Recursively rewrite unsupported schema constructs in place."""
    if isinstance(node, dict):
        if node.pop("nullable", None) is not None:
            stats["nullable"] += 1
        for k in ("readOnly", "writeOnly"):
            if node.pop(k, None) is not None:
                stats[k] += 1
        # NOTE: object-form `additionalProperties` is deliberately left alone —
        # oasgen >= 0.18.0 turns it into a typed map in the CRD (see the module
        # docstring's "Retired transformations").
        for v in node.values():
            strip_constructs(v)
    elif isinstance(node, list):
        for v in node:
            strip_constructs(v)


def fix_security(spec):
    comps = spec.setdefault("components", {})
    schemes = comps.get("securitySchemes", {})
    new = {}
    for name, sch in schemes.items():
        if sch.get("type") == "apiKey" and sch.get("in") == "header":
            new["accessToken"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
            stats["securityScheme-apiKey->http"] += 1
        elif sch.get("type") == "http" and sch.get("scheme") == "bearer":
            # already usable; normalise the scheme name to accessToken
            new["accessToken"] = {"type": "http", "scheme": "bearer", "bearerFormat": "JWT"}
        else:
            new[name] = sch
    if new:
        comps["securitySchemes"] = new
        spec["security"] = [{"accessToken": []}]


def merge_compute_v11(compute, v11):
    """Splice the v1.1 'Create CloudServer' operation into the base compute doc."""
    for path, ops in v11.get("paths", {}).items():
        base = compute["paths"].setdefault(path, {})
        for method, op in ops.items():
            base[method] = op
    # bring across any schemas the create operation references
    v11_schemas = v11.get("components", {}).get("schemas", {})
    dst = compute.setdefault("components", {}).setdefault("schemas", {})
    for name, sch in v11_schemas.items():
        dst.setdefault(name, sch)
    stats["compute-v1.1-create-merged"] += 1


def main():
    os.makedirs(OUT, exist_ok=True)
    compute = None
    computev11 = None
    specs = {}
    for fn in sorted(os.listdir(SRC)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(SRC, fn)) as f:
            specs[fn] = json.load(f)

    # merge compute v1.1 create into base compute doc, then drop the v1.1 file
    if "compute-provider.json" in specs and "compute-provider_v1.1.json" in specs:
        merge_compute_v11(specs["compute-provider.json"], specs["compute-provider_v1.1.json"])
    specs.pop("compute-provider_v1.1.json", None)

    for fn, spec in specs.items():
        fix_security(spec)
        strip_constructs(spec)
        out = fn.replace("-provider", "").replace("_v1.1", "")
        with open(os.path.join(OUT, out), "w") as f:
            json.dump(spec, f, indent=2)
        print(f"  wrote openapi/{out}")

    print("\nTransformations applied (each is an oasgen-provider gap):")
    for k, v in sorted(stats.items(), key=lambda x: -x[1]):
        print(f"  {v:6}  {k}")


if __name__ == "__main__":
    main()
