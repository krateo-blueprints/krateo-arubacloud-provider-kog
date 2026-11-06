#!/usr/bin/env python3
"""Type-check every sample CR against the CRD it must satisfy.

`scripts/validate.py` deliberately needs no cluster, so it can parse a sample and
confirm every RestDefinition path exists -- but it cannot know what the GENERATED CRD
contains. That comparison exists in exactly one place today: the kind smoke job's
`kubectl apply --dry-run=server`. Which means the only way to learn that a sample no
longer fits its CRD is to push and wait ~4 minutes.

This closes that loop locally. It is not a replacement for the dry-run -- the
apiserver is the authority -- it is the same question asked sooner.

It earns its place: the dry-run caught two samples sending `REPLACE` into a field the
CRD types as an object, because Aruba ships two schemas with no `type` at all (both
literally `{"nullable": true}`: `KaasPropertiesDto.autoscalerProfile` and
`AlertInfo.theshold`) and a structural CRD must give every node a type, so they become
`type: object`. This script reproduces both failures from the pre-fix samples, which is
the only reason to trust it when it reports none.

Usage:
    # against a running cluster that has the CRDs (no Go toolchain needed)
    scripts/check_samples.py --from-cluster
    scripts/check_samples.py --from-cluster --context kind-aruba-pin

    # against CRDs you rendered yourself, one JSON or YAML document per file
    scripts/check_samples.py path/to/crds/

    # prove the comparison can disagree, without any cluster or CRD at all
    scripts/check_samples.py --prove

Exits non-zero if any sample mismatches its CRD, if no sample could be checked at
all, or if a sample in the provider's own API group has no CRD to check against.
"""
import glob
import json
import os
import subprocess
import sys

import yaml

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

# The group the provider generates into. A sample in THIS group with no CRD is a
# failure (the CRD should exist and does not); a sample in any other group -- the
# `v1` Secrets the samples ship as prerequisites -- is simply not ours to check.
OWN_GROUP = "arubacloud.ogen.krateo.io"

JSON_TYPE = {
    "object": dict,
    "array": list,
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": (int, float),
}


def check(node, schema, path, out):
    """Compare one sample subtree against one CRD subschema, appending complaints."""
    if schema is None:
        return
    t = schema.get("type")

    if node is None:
        # A null on a non-nullable field is PRUNED by the apiserver, which turns the
        # field unspecified, which lets any `default` apply. That is how a sample can
        # ask for nothing and get `default: true` -- see docs/oasgen-provider-evolution.md.
        if not schema.get("nullable"):
            out.append(f"{path}: sample sends null but the CRD field is not nullable, "
                       f"so the apiserver prunes it and any default applies")
        return

    if t and t in JSON_TYPE:
        # bool is a subclass of int in Python, so the two numeric cases need an
        # explicit guard in both directions or `true` passes as an integer.
        if t == "integer" and isinstance(node, bool):
            out.append(f"{path}: bool where the CRD wants integer")
            return
        if t == "boolean" and not isinstance(node, bool):
            out.append(f"{path}: {type(node).__name__} where the CRD wants boolean")
            return
        if not isinstance(node, JSON_TYPE[t]):
            out.append(f"{path}: {type(node).__name__} ({node!r}) "
                       f"where the CRD wants {t}")
            return

    if isinstance(node, dict):
        props = schema.get("properties") or {}
        addl = schema.get("additionalProperties")
        preserve = schema.get("x-kubernetes-preserve-unknown-fields") is True
        for k, v in node.items():
            if k in props:
                check(v, props[k], f"{path}.{k}", out)
            elif isinstance(addl, dict):
                check(v, addl, f"{path}.{k}", out)
            elif preserve or addl is True:
                continue  # the CRD accepts anything here; nothing to compare against
            else:
                out.append(f"{path}.{k}: unknown field "
                           f"(the CRD declares no such property)")
    elif isinstance(node, list) and schema.get("items"):
        for i, v in enumerate(node):
            check(v, schema["items"], f"{path}[{i}]", out)

    for req in schema.get("required") or []:
        if isinstance(node, dict) and req not in node:
            out.append(f"{path}: required property {req!r} missing from the sample")


def index_crds(crds):
    """Key every (group, version, kind) a CRD serves to its spec subschema.

    Indexing on versions[0] would be wrong here: `metering.json` and `auditing.json`
    declare `info.version: 1.0` while the other eleven declare `1.0.0`, so the fleet
    genuinely contains both `v1-0` and `v1-0-0` CRDs. A sample must be checked against
    the version its own apiVersion names.
    """
    index = {}
    for crd in crds:
        spec = crd.get("spec") or {}
        group = spec.get("group")
        kind = ((spec.get("names") or {}).get("kind"))
        if not group or not kind:
            continue
        for ver in spec.get("versions") or []:
            root = ((ver.get("schema") or {}).get("openAPIV3Schema")) or {}
            index[(group, ver.get("name"), kind)] = \
                (root.get("properties") or {}).get("spec")
    return index


def crds_from_cluster(context):
    cmd = ["kubectl"]
    if context:
        cmd += ["--context", context]
    cmd += ["get", "crd", "-o", "json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"kubectl could not list CRDs:\n{proc.stderr.strip()}")
    return json.loads(proc.stdout).get("items") or []


def crds_from_dir(path):
    if not os.path.isdir(path):
        raise SystemExit(f"{path} is not a directory. Pass a directory of rendered "
                         f"CRDs, or use --from-cluster.")
    out = []
    for f in sorted(glob.glob(os.path.join(path, "**", "*"), recursive=True)):
        if not os.path.isfile(f) or not f.endswith((".json", ".yaml", ".yml")):
            continue
        with open(f) as fh:
            try:
                docs = list(yaml.safe_load_all(fh))  # a superset of JSON
            except yaml.YAMLError as e:
                raise SystemExit(f"{f}: not parseable as YAML or JSON: {e}")
        for d in docs:
            if isinstance(d, dict) and d.get("kind") == "CustomResourceDefinition":
                out.append(d)
    if not out:
        raise SystemExit(f"{path} holds no CustomResourceDefinition documents. "
                         f"An empty index would check zero samples and report clean.")
    return out


def prove():
    """Show the comparison can disagree, using the two shapes that really broke.

    A checker that cannot fail is indistinguishable from one that found nothing. This
    runs the real `check` against a hand-written schema, so it needs no cluster.
    """
    cases = [
        ("a string where the CRD says object (the `theshold` break)",
         {"type": "object", "properties": {
             "alertInfo": {"type": "object", "properties": {
                 "theshold": {"type": "object"}}}}},
         {"alertInfo": {"theshold": "REPLACE"}}),
        ("a string where the CRD says object, at the top (the `autoscalerProfile` break)",
         {"type": "object", "properties": {"autoscalerProfile": {"type": "object"}}},
         {"autoscalerProfile": "REPLACE"}),
        ("a field the CRD does not declare",
         {"type": "object", "properties": {"known": {"type": "string"}}},
         {"typo": "x"}),
        ("a null on a non-nullable field, which the apiserver prunes",
         {"type": "object", "properties": {"properties": {"type": "object"}}},
         {"properties": None}),
        ("true where the CRD says integer",
         {"type": "object", "properties": {"sizeGb": {"type": "integer"}}},
         {"sizeGb": True}),
    ]
    ok = True
    for label, schema, sample in cases:
        out = []
        check(sample, schema, "spec", out)
        if out:
            print(f"  caught: {label}\n          -> {out[0]}")
        else:
            print(f"  MISSED: {label}")
            ok = False

    # And the converse: a sample that fits must NOT be complained about, or every
    # run is noise and the "caught" lines above prove nothing.
    out = []
    check({"properties": {"default": False, "preset": False}},
          {"type": "object", "properties": {"properties": {
              "type": "object", "properties": {
                  "default": {"type": "boolean"},
                  "preset": {"type": "boolean"}}}}},
          "spec", out)
    if out:
        print(f"  FALSE POSITIVE on a correct sample: {out}")
        ok = False
    else:
        print("  quiet on a sample that fits")

    print("\nthe comparison can disagree" if ok
          else "\nthe comparison is BROKEN -- a clean run would mean nothing")
    return 0 if ok else 1


def main(argv):
    if "--prove" in argv:
        return prove()

    if "--from-cluster" in argv:
        context = None
        if "--context" in argv:
            i = argv.index("--context")
            if i + 1 >= len(argv):
                raise SystemExit("--context needs a value")
            context = argv[i + 1]
        crds = crds_from_cluster(context)
        source = f"cluster {context or '(current context)'}"
    else:
        positional = [a for a in argv if not a.startswith("-")]
        if len(positional) != 1:
            raise SystemExit(__doc__)
        crds = crds_from_dir(positional[0])
        source = positional[0]

    index = index_crds(crds)
    problems, unchecked, checked = [], [], 0

    for f in sorted(glob.glob(os.path.join(ROOT, "samples", "**", "*.yaml"),
                              recursive=True)):
        rel = os.path.relpath(f, ROOT)
        with open(f) as fh:
            docs = list(yaml.safe_load_all(fh))
        for doc in docs:
            if not isinstance(doc, dict) or "kind" not in doc:
                continue
            api = doc.get("apiVersion") or ""
            group, _, version = api.rpartition("/")
            key = (group, version, doc["kind"])
            if key not in index:
                # Never skip silently. A mis-pointed CRD source checks nothing and,
                # without this, exits 0 -- a green that means only that it looked
                # in the wrong place.
                unchecked.append((rel, api, doc["kind"], group == OWN_GROUP))
                continue
            out = []
            check(doc.get("spec"), index[key], f"{rel}:spec", out)
            checked += 1
            problems += out

    print(f"indexed {len(index)} (group, version, kind) from {source}")
    print(f"checked {checked} sample CR(s)")

    ours = [u for u in unchecked if u[3]]
    theirs = [u for u in unchecked if not u[3]]
    if theirs:
        print(f"\nnot ours to check ({len(theirs)}): "
              + ", ".join(sorted({f'{u[2]} ({u[1]})' for u in theirs})))
    if ours:
        print(f"\n{len(ours)} sample(s) in {OWN_GROUP} have NO matching CRD:")
        for rel, api, kind, _ in ours:
            print(f"  {rel}: {kind} {api}")
        print("  Either the CRD source is incomplete, or the sample's apiVersion is "
              "wrong.\n  Both are failures; neither is a skip.")

    if problems:
        print(f"\n{len(problems)} PROBLEM(S):")
        for p in problems:
            print("  " + p)

    if checked == 0:
        print("\nNOTHING WAS CHECKED. A clean result here would mean nothing.")
        return 1
    if problems or ours:
        return 1
    print("\nno type, unknown-field or missing-required mismatches")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
