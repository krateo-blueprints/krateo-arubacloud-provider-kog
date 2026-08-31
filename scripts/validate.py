#!/usr/bin/env python3
"""Static validation of the generated artifacts (no cluster needed):
  * the vendored Aruba OAS documents are UNMODIFIED (sha256 vs openapi/CHECKSUMS.txt);
  * every RestDefinition verb path+method exists in its referenced OAS;
  * every requestFieldMapping.inPath is a real path parameter of that verb's path;
  * every oasPath configmap:// reference resolves to a generated ConfigMap;
  * all RestDefinition / ConfigMap / sample / restaction YAML is parseable;
  * every Composition chart passes `helm lint` and renders valid YAML with
    `helm template` (skipped with a warning if the helm binary is absent).
Exits non-zero if anything fails.
"""
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys

import yaml

ROOT = os.path.join(os.path.dirname(__file__), "..")
# short provider name -> published filename (consumed UNMODIFIED)
PROVIDERS = {
    "network": "network-provider.json", "compute": "compute-provider.json",
    "container": "container-provider.json", "database": "database-provider.json",
    "storage": "storage-provider.json", "security": "security-provider.json",
    "schedule": "schedule-provider.json", "baremetal": "baremetal-provider.json",
    "project": "project.json", "metering": "metering.json",
}


# Verbatim from the RestDefinition CRD (ogen.krateo.io_restdefinitions.yaml).
OASPATH_PATTERN = r"^(configmap:\/\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-zA-Z0-9.-_]+)|https?:\/\/\S+)$"


def main():
    os.chdir(ROOT)
    errors = []
    verify_oas_unmodified(errors)
    specs = {p: json.load(open(f"openapi/{fn}")) for p, fn in PROVIDERS.items()}
    cm_names = {yaml.safe_load(open(f))["metadata"]["name"]
                for f in glob.glob("configmaps/*.yaml")}

    rds = glob.glob("restdefinitions/**/*.yaml", recursive=True)
    for f in rds:
        doc = yaml.safe_load(open(f))
        prov = f.split(os.sep)[1]
        spec = specs[prov]
        oas_path = doc["spec"]["oasPath"]
        # Mirror the RestDefinition CRD's own oasPath pattern. Note the key segment's
        # class is [a-zA-Z0-9.-_] -- a RANGE '.'-'_' that excludes '-', so a ConfigMap
        # key containing a hyphen is rejected by the API server. Checking it here turns
        # an apply-time failure into a validation-time one.
        if not re.match(OASPATH_PATTERN, oas_path):
            errors.append(f"{f}: oasPath rejected by the CRD pattern: {oas_path}")
        cm = oas_path.split("/")[3]
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
            # Async poll path contract, mirroring oasgen >= 0.18.0's own admission
            # check (restdefinition/helper.go validateAsyncPollPaths):
            #   1. the poll path must be an EXACT key of the OAS paths object --
            #      paths are resolved by exact string lookup (restclient.go);
            #   2. it must contain the {handleParam} token, i.e. the parameter the
            #      extracted operation handle binds to. handleParam defaults to
            #      "operationId" when not declared.
            poll = v.get("async", {}).get("poll", {})
            if poll.get("path"):
                handle = poll.get("handleParam") or "operationId"
                if poll["path"] not in spec["paths"]:
                    errors.append(f"{f}: async poll path not in OAS verbatim: {poll['path']}")
                if "{" + handle + "}" not in poll["path"]:
                    errors.append(f"{f}: async poll path lacks the {{{handle}}} token "
                                  f"(handleParam={poll.get('handleParam', '<default>')})")
                if not poll.get("statusPath") or not poll.get("successValues"):
                    errors.append(f"{f}: async poll missing statusPath/successValues")

    for f in (glob.glob("samples/**/*.yaml", recursive=True)
              + glob.glob("configmaps/*.yaml")
              + glob.glob("restactions/**/*.yaml", recursive=True)):
        try:
            list(yaml.safe_load_all(open(f)))
        except Exception as e:
            errors.append(f"{f}: {e}")

    verify_sample_versions(errors, specs)
    charts = validate_charts(errors)

    print(f"Checked {len(rds)} RestDefinitions, {len(cm_names)} ConfigMaps, {charts} chart(s).")
    if errors:
        print(f"{len(errors)} ERROR(S):")
        for e in errors:
            print("  -", e)
        sys.exit(1)
    print("OK: verb paths/methods exist in their OAS; oasPaths resolve; YAML valid; charts render.")


def verify_oas_unmodified(errors):
    """The vendored specs are Aruba's published documents, byte-for-byte. This repo
    deliberately performs NO OAS rewriting -- KOG adapts to the published contract,
    not the other way round -- so any drift here is a bug, not a patch."""
    manifest = os.path.join("openapi", "CHECKSUMS.txt")
    if not os.path.isfile(manifest):
        errors.append("openapi/CHECKSUMS.txt missing: cannot prove the specs are unmodified")
        return
    expected = {}
    for line in open(manifest):
        digest, _, name = line.strip().partition("  ")
        if name:
            expected[name] = digest
    for fn in sorted(glob.glob("openapi/*.json")):
        name = os.path.basename(fn)
        actual = hashlib.sha256(open(fn, "rb").read()).hexdigest()
        if name not in expected:
            errors.append(f"openapi/{name}: not listed in CHECKSUMS.txt")
        elif actual != expected[name]:
            errors.append(f"openapi/{name}: MODIFIED (sha256 differs from CHECKSUMS.txt)")
    for name in expected:
        if not os.path.isfile(os.path.join("openapi", name)):
            errors.append(f"openapi/{name}: listed in CHECKSUMS.txt but missing")


def verify_sample_versions(errors, specs):
    """Sample apiVersions must match what oasgen actually generates. Learned on a
    live cluster: the RESOURCE CRD's version is derived from the OAS info.version
    (crdgen.NormalizeVersionName: 1.0.0 -> v1-0-0), while the companion
    <Kind>Configuration CRD is always v1alpha1. Samples that hardcoded v1alpha1 for
    the resource were rejected with "no matches for kind ... in version"."""
    group = "arubacloud.ogen.krateo.io"
    for f in sorted(glob.glob("samples/*/*.yaml")):
        prov = f.split(os.sep)[1]
        if prov not in specs:
            continue
        doc = yaml.safe_load(open(f))
        if not isinstance(doc, dict) or "apiVersion" not in doc:
            continue
        v = (specs[prov].get("info") or {}).get("version") or ""
        want_res = f"{group}/v{v.replace('.', '-')}" if v else f"{group}/v1alpha1"
        want = f"{group}/v1alpha1" if doc.get("kind", "").endswith("Configuration") else want_res
        if doc["apiVersion"] != want:
            errors.append(f"{f}: apiVersion {doc['apiVersion']} but the cluster serves {want}")


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
        # The chart carries `version: CHART_VERSION`, a placeholder the release workflow
        # substitutes with the git tag. helm rejects it as invalid SemVer, so lint/template a
        # COPY with a stand-in version -- exactly what the release does -- rather than skipping
        # validation or forcing the chart to carry a throwaway version in git.
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, os.path.basename(d))
            shutil.copytree(d, work)
            cf = os.path.join(work, "Chart.yaml")
            txt = open(cf).read()
            open(cf, "w").write(re.sub(r"^version:\s*CHART_VERSION\s*$", "version: 0.0.0", txt, flags=re.M))

            lint = subprocess.run([helm, "lint", work], capture_output=True, text=True)
            if lint.returncode != 0:
                errors.append(f"helm lint failed for {d}:\n{lint.stdout}{lint.stderr}")
                continue
            tmpl = subprocess.run([helm, "template", "validate", work], capture_output=True, text=True)
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
