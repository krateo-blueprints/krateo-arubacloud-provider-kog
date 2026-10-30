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
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
from providers import CONFIGMAP_PROVIDERS, PROVIDERS  # one list; see scripts/providers.py


# Verbatim from the RestDefinition CRD (ogen.krateo.io_restdefinitions.yaml).
OASPATH_PATTERN = r"^(configmap:\/\/([a-z0-9-]+)\/([a-z0-9-]+)\/([a-zA-Z0-9.-_]+)|https?:\/\/\S+)$"


def main():
    os.chdir(ROOT)
    errors = []
    verify_oas_unmodified(errors)
    # CONFIGMAP_PROVIDERS, not PROVIDERS: a verb may resolve against a document
    # that is not a provider of its own. compute11 is exactly that -- it ships as
    # a ConfigMap and carries CloudServer's create, but generates no resources.
    specs = {p: json.load(open(f"openapi/{fn}"))
             for p, fn in CONFIGMAP_PROVIDERS.items()}
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
            # A verb may override the document it resolves against (oasgen 0.34.0,
            # #227). Checking every verb against the RESOURCE's document would then
            # be checking the wrong file: CloudServer's create is a POST that exists
            # only in compute-provider_v1.1.json, and validating it against
            # compute-provider.json reports a missing method that is not missing.
            vspec, vlabel = spec, prov
            if v.get("oasPath"):
                if not re.match(OASPATH_PATTERN, v["oasPath"]):
                    errors.append(f"{f}: {v['action']} oasPath rejected by the CRD "
                                  f"pattern: {v['oasPath']}")
                    continue
                vcm = v["oasPath"].split("/")[3]
                if vcm not in cm_names:
                    errors.append(f"{f}: {v['action']} oasPath ConfigMap '{vcm}' "
                                  f"not found")
                    continue
                vprov = v["oasPath"].rsplit("/", 1)[-1].removesuffix(".json")
                if vprov not in specs:
                    errors.append(f"{f}: {v['action']} oasPath names an unknown "
                                  f"document: {vprov}")
                    continue
                vspec, vlabel = specs[vprov], vprov
            if p not in vspec["paths"]:
                errors.append(f"{f}: path missing in {vlabel} OAS: {p}")
            elif m not in vspec["paths"][p]:
                errors.append(f"{f}: method {m.upper()} not on {p} in {vlabel} OAS")
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
    verify_composition_apiversion(errors)
    verify_documented_pin(errors)
    verify_no_builtin_shadowing(errors)
    verify_restaction_filter_envelope(errors)
    verify_restaction_jq_compiles(errors)
    charts = validate_charts(errors)
    validate_composition_generated(errors)

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
        # safe_load_all, not safe_load: a sample may carry a companion document -- a
        # resource whose credential is sourced by the secretRef resolver ships the
        # Secret alongside the CR, since the field has no spec form to fill in.
        for doc in yaml.safe_load_all(open(f)):
            if not isinstance(doc, dict) or "apiVersion" not in doc:
                continue
            if not doc["apiVersion"].startswith(group + "/"):
                continue          # core/v1 companions (Secret) are not group-versioned
            v = (specs[prov].get("info") or {}).get("version") or ""
            want_res = f"{group}/v{v.replace('.', '-')}" if v else f"{group}/v1alpha1"
            want = f"{group}/v1alpha1" if doc.get("kind", "").endswith("Configuration") else want_res
            if doc["apiVersion"] != want:
                errors.append(f"{f}: apiVersion {doc['apiVersion']} but the cluster serves {want}")


def verify_composition_apiversion(errors):
    """No file may hardcode a `composition.krateo.io/<version>` apiVersion.

    Sibling rule to verify_sample_versions, and never checked until now.
    core-provider derives the Composition CRD's version from the chart version with
    `"v" + strings.ReplaceAll(chartVersion, ".", "-")` -- including the PATCH. So
    chart 0.3.8 is served at `v0-3-8`, and a literal is wrong the moment the chart
    gets a patch bump. This repo makes it unconditionally wrong: the chart carries
    `version: CHART_VERSION`, substituted at release, so at no commit does a correct
    literal exist to write.

    Writing one costs more than a stale doc. `no matches for kind` is what the API
    server says both when the apiVersion is wrong and when the CRD was never
    created, so the reader debugs the wrong layer. The addressable form is to read
    what core-provider actually created:

        kubectl wait --for=condition=Ready compositiondefinition/<name> -n <ns> --timeout=5m
        kubectl get compositiondefinition <name> -n <ns> -o jsonpath='{.status.apiVersion}'
        kubectl get compositiondefinition <name> -n <ns> -o jsonpath='{.status.kind}'
        kubectl get compositiondefinition <name> -n <ns> -o jsonpath='{.status.resource}'

    All three are served on the object; confirmed against core-provider's committed
    CRD schema. The plural especially must be READ -- core-provider resolves it
    through plumbing/kubeutil/plurals, not a string rule, and they measured
    `strings.ToLower(kind)+"s"` dropping 32 of 83 rows in a fleet-wide join.

    Reported by the openstack-nova-operator-kog session, which found it shipping in
    its own examples; `examples/network-environment/README.md` had it here.
    """
    pat = re.compile(r"composition\.krateo\.io/v[0-9A-Za-z]")
    # A screen that reports "all clear" without proving it can detect a known-bad
    # case is a function that returns false. Assert on both a real miss and a real
    # hit before trusting the sweep.
    assert pat.search("apiVersion: composition.krateo.io/v1alpha1")
    assert pat.search("composition.krateo.io/v0-3-8")
    assert not pat.search("apiVersion: core.krateo.io/v1alpha1")
    assert not pat.search("kubectl get compositiondefinition x -o json")
    for f in sorted(glob.glob("**/*.md", recursive=True) +
                    glob.glob("**/*.yaml", recursive=True)):
        if f.startswith(".git" + os.sep) or os.sep + "charts" + os.sep in f:
            continue
        for n, line in enumerate(open(f, encoding="utf-8", errors="replace"), 1):
            if pat.search(line):
                errors.append(
                    f"{f}:{n}: hardcoded Composition apiVersion -- core-provider "
                    f"derives it from the FULL chart version, and this chart's "
                    f"version is a release-time placeholder. Read it instead: "
                    f"kubectl wait --for=condition=Ready "
                    f"compositiondefinition/<name> -n <ns> --timeout=5m, then read "
                    f"{{.status.apiVersion}}, {{.status.kind}} and {{.status.resource}} "
                    f"-- all three are served; the plural is NOT derivable from the kind")


# Docs that are DATED EVIDENCE or a changelog: they record what was true at a past
# version on purpose, and rewriting them would destroy the record. Everything else
# is an instruction a reader will follow today.
HISTORICAL_DOCS = ("docs/live-cluster-test.md", "docs/log.md",
                   "docs/oasgen-provider-evolution.md", "docs/release.md")


def verify_documented_pin(errors):
    """Every documented install must name the version CI actually pins.

    The pin lives in one place -- OASGEN_VERSION in .github/workflows/validate.yaml,
    which scripts/pin_verify.sh defaults to -- but the docs spell it out in prose, so
    nothing stopped them drifting. They had: 0.9.20 in the README quickstart, 0.21.1
    in the README's own requirements table nine lines later, 0.21.1 in
    getting-started, and a pre-monorepo chart at 0.9.19 in usage -- against a pin of
    0.36.0, which is a parse floor. Following any of them lands you below it, where
    no RestDefinition reaches Ready and nothing in the failure names the parser.

    Also screens the install SOURCE. Three resolve and only one is right:
    `krateo-platformops` (live), `krateoplatformops` (abandoned, disjoint tags), and
    charts.krateo.io (serves appVersion 0.10.0 -- below apiKey support AND below the
    0.26.1 credential-header redaction). Reported by the openstack-nova-operator-kog
    session, which had all five of its install docs pointing at the last one;
    oasgen#278.
    """
    wf = open(os.path.join(".github", "workflows", "validate.yaml")).read()
    m = re.search(r'^\s*OASGEN_VERSION:\s*"([^"]+)"', wf, flags=re.M)
    if not m:
        errors.append(".github/workflows/validate.yaml: no OASGEN_VERSION to check "
                      "the docs against")
        return
    pin = m.group(1)

    chart = re.compile(r"charts/oasgen-provider(-crds)?\b")
    version = re.compile(r'--version\s+"?\$?\{?([0-9]+\.[0-9]+\.[0-9]+)')
    # A screen that cannot detect a known-bad case is a function that returns false.
    assert version.search("  --version 0.9.20 --namespace krateo-system").group(1) == "0.9.20"
    assert version.search('--version "0.36.0" -n x').group(1) == "0.36.0"
    assert version.search('--version "${OASGEN_VERSION}"') is None   # indirection is fine
    assert chart.search("oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds")
    assert not chart.search("oci://ghcr.io/krateo-blueprints/charts/aruba-network-environment")

    bad_source = re.compile(r"ghcr\.io/krateoplatformops/|"
                            r"https?://charts\.krateo\.io|"
                            r"helm\s+repo\s+add\s+\S*krateo")
    assert bad_source.search("helm install x oci://ghcr.io/krateoplatformops/charts/y")
    assert bad_source.search("helm repo add krateo https://charts.krateo.io")
    assert not bad_source.search("`charts.krateo.io` serves a build below the floor")

    for f in sorted(glob.glob("**/*.md", recursive=True)):
        if f.startswith(".git" + os.sep) or f.replace(os.sep, "/") in HISTORICAL_DOCS:
            continue
        lines = open(f, encoding="utf-8", errors="replace").read().splitlines()
        for n, line in enumerate(lines):
            if bad_source.search(line):
                errors.append(f"{f}:{n+1}: install source is not "
                              f"ghcr.io/krateo-platformops -- that registry serves a "
                              f"version below the {pin} floor (oasgen#278)")
            if not chart.search(line):
                continue
            # The --version may sit on the install line or its continuation. The
            # 3-line window is not a guess: every occurrence in this repo was checked
            # against it. But if a reformat ever moves one further away, a screen that
            # just `continue`s here reports ALL CLEAR on an unchecked install -- the
            # same way an INCOMPLETE pattern fails like a dead one. So an install
            # command whose version cannot be located is itself an error.
            found = False
            for cand in lines[n:n + 3]:
                v = version.search(cand)
                if v:
                    found = True
                    if v.group(1) != pin:
                        errors.append(f"{f}:{n+1}: documents oasgen-provider chart "
                                      f"{v.group(1)} but CI pins {pin} "
                                      f"(OASGEN_VERSION in validate.yaml)")
                    break
            if not found and re.search(r"\bhelm\s+(install|upgrade)\b", line):
                errors.append(f"{f}:{n+1}: installs the oasgen-provider chart but no "
                              f"--version within 3 lines -- this screen could not "
                              f"verify it against the {pin} pin, so treat it as "
                              f"unchecked rather than clean")


# Kinds and plurals a stock Kubernetes API server already serves. Hand-written on
# purpose: this check must run on a PR with no cluster, which is the only place the
# collision is still cheap. It is not exhaustive and does not need to be -- it holds
# every name a cloud-resource kind might plausibly land on. The AUTHORITATIVE screen
# is `kubectl api-resources` minus CRD-backed groups on a FRESH cluster, which is
# what the smoke job has; this one just moves the common cases earlier.
BUILTIN_KINDS = {
    "Binding", "ComponentStatus", "ConfigMap", "Endpoints", "Event", "LimitRange",
    "Namespace", "Node", "PersistentVolume", "PersistentVolumeClaim", "Pod",
    "PodTemplate", "ReplicationController", "ResourceQuota", "Secret", "Service",
    "ServiceAccount", "ControllerRevision", "DaemonSet", "Deployment", "ReplicaSet",
    "StatefulSet", "CronJob", "Job", "Ingress", "IngressClass", "NetworkPolicy",
    "ClusterRole", "ClusterRoleBinding", "Role", "RoleBinding", "Lease",
    "CustomResourceDefinition", "APIService", "StorageClass", "VolumeAttachment",
    "CSIDriver", "CSINode", "PriorityClass", "RuntimeClass", "PodDisruptionBudget",
    "HorizontalPodAutoscaler", "MutatingWebhookConfiguration",
    "ValidatingWebhookConfiguration", "CertificateSigningRequest",
}
BUILTIN_LOWER = {k.lower() for k in BUILTIN_KINDS} | {
    "bindings", "componentstatuses", "configmaps", "endpoints", "events",
    "limitranges", "namespaces", "nodes", "persistentvolumes",
    "persistentvolumeclaims", "pods", "podtemplates", "replicationcontrollers",
    "resourcequotas", "secrets", "services", "serviceaccounts",
    "controllerrevisions", "daemonsets", "deployments", "replicasets",
    "statefulsets", "cronjobs", "jobs", "ingresses", "ingressclasses",
    "networkpolicies", "clusterroles", "clusterrolebindings", "roles",
    "rolebindings", "leases", "customresourcedefinitions", "apiservices",
    "storageclasses", "volumeattachments", "csidrivers", "csinodes",
    "priorityclasses", "runtimeclasses", "poddisruptionbudgets",
    "horizontalpodautoscalers", "certificatesigningrequests",
}


def _collides(kind):
    """The names this kind would occupy, intersected with the built-ins.

    The plural is DERIVED here, which is the weak point: oasgen derives the real one
    and irregular stems do not survive a naive +s (`Kaas` -> `kaas`, not `kaass`).
    So this screens the forms a collision could take and accepts a false positive
    over a miss -- a false positive is one comment to resolve, a miss is a chart that
    installs on an upgraded cluster and refuses on a fresh one.
    """
    low = kind.lower()
    return ({kind} & BUILTIN_KINDS) | ({low, low + "s", low + "es"} & BUILTIN_LOWER)


def verify_no_builtin_shadowing(errors):
    """No generated CRD may shadow a Kubernetes built-in, on EITHER of its two CRDs.

    oasgen refuses such a kind from 0.32.0 (#200) -- but only on a FRESH cluster. Where
    the CRD already exists it downgrades to a BuiltInNameCollisionExisting warning
    event, so a developer who has been upgrading one cluster for weeks sees nothing and
    the chart fails for the first person who installs it clean. `github-provider-kog`
    lost a chart to exactly this with a `Deployment` kind; this repo renamed Aruba's
    `jobs` to `ScheduledJob` for the same reason (see generate_restdefinitions.py).

    Screens BOTH CRDs per RestDefinition: `<Kind>` and the companion
    `<Kind>Configuration`. Reported by the openstack-nova-operator-kog session.
    """
    # A screen that reports "all clear" without proving it can detect a known-bad
    # case is a function that returns false. These must all be caught.
    for probe in ("Node", "Service", "Secret", "Deployment", "Event", "Job"):
        assert _collides(probe), f"screen cannot detect the built-in {probe}"
    for ok in ("Vpc", "CloudServer", "ScheduledJob", "AiKey", "Kaas"):
        assert not _collides(ok), f"screen false-positives on {ok}"

    for f in sorted(glob.glob("restdefinitions/**/*.yaml", recursive=True)):
        kind = ((yaml.safe_load(open(f)) or {}).get("spec", {})
                .get("resource", {}).get("kind"))
        if not kind:
            continue
        for k in (kind, kind + "Configuration"):
            hit = _collides(k)
            if hit:
                errors.append(
                    f"{f}: kind '{k}' shadows a Kubernetes built-in ({', '.join(sorted(hit))}). "
                    f"oasgen refuses it on a fresh cluster (#200) and only warns where the "
                    f"CRD already exists, so this is invisible until a clean install. Rename "
                    f"the kind in scripts/generate_restdefinitions.py's plural->kind map.")


def verify_restaction_filter_envelope(errors):
    """A stage `filter` must read only `.extras` and its own stage's key.

    snowplow evaluates a filter on an ENVELOPE, not on the response body:

        { "extras": <the request's extras>, "<stage name>": <the response body> }

    (plus `slice` when paginated). Any other root key resolves to null, and jq does
    that SILENTLY -- so the filter returns a well-formed result that is simply always
    empty, the stage reports success, and `X-Snowplow-Stage-Outcomes` comes back `[]`
    because that header lists only stages that did NOT succeed. There is no error
    anywhere.

    This cost a real billable CloudServer. All three of this repo's filters read
    `.spec.metadata.name` and `.values` -- the shapes that `path` and
    `dependsOn.iterator` see, since those run against the resolver dict. In a filter
    both are absent, so `$n` was null on every call, nothing matched, both downstream
    iterators yielded `[]`, and no request was ever sent. The server stayed Active
    through 23 delegated delete attempts while every layer reported success.
    snowplow#997; confirmed live with a probe filter of `{ k: keys, ... }` returning
    `k = ["extras","server"]`.

    The repo-wide shape, worth stating because confusing them is how this happened:
        path                -> resolver dict (a copy of extras):  .spec.projectId
        filter              -> the envelope:                      .extras.* / .<stage>.*
        dependsOn.iterator  -> resolver dict:                     .server.self
    """
    root = re.compile(r"(?<![\w.$])\.([A-Za-z_][A-Za-z0-9_]*)")
    # A screen that cannot detect a known-bad case is a function that returns false.
    def roots(expr):
        """Root-level lookups only, with `.`-rebinding contexts removed first.

        Inside map/select/any/all/with_entries, `.` is the ITEM, not the envelope, so
        `select(.metadata.name == $n)` is correct and must not be flagged. Stripping
        those bodies is the honest fix; allow-listing `metadata` would silence this
        case and hide a genuine root `.metadata` later.
        """
        out, i = [], 0
        opener = re.compile(r"\b(map|map_values|select|any|all|with_entries)\s*\(")
        while i < len(expr):
            m = opener.search(expr, i)
            if not m:
                out.append(expr[i:])
                break
            out.append(expr[i:m.start()])
            depth, j = 0, m.end() - 1          # sitting on the '('
            while j < len(expr):               # skip the BALANCED body
                if expr[j] == "(":
                    depth += 1
                elif expr[j] == ")":
                    depth -= 1
                    if depth == 0:
                        j += 1
                        break
                j += 1
            i = j
        return sorted(set(root.findall("".join(out))))

    # A screen that cannot detect a known-bad case is a function that returns false.
    assert roots(".spec.metadata.name as $n | (.values // [])") == ["spec", "values"]
    assert roots(".extras.spec.x | .server.values") == ["extras", "server"]
    assert roots("$s.metadata.id") == []
    # The FIXED filter must come back clean -- .metadata lives inside select().
    assert roots('.extras.spec.metadata.name as $n | { self: ((.server.values // []) '
                 '| map(select(.metadata.name == $n)) | .[0]) }') == ["extras", "server"]
    # ...and a genuine ROOT .metadata must still be caught.
    assert "metadata" in roots(".metadata.name as $n | (.server.values // [])")
    for f in sorted(glob.glob("restactions/**/*.yaml", recursive=True)):
        try:
            doc = yaml.safe_load(open(f))
        except Exception:
            continue
        if not isinstance(doc, dict) or doc.get("kind") != "RESTAction":
            continue
        for st in (doc.get("spec") or {}).get("api") or []:
            filt, name = st.get("filter"), st.get("name")
            if not filt or not name:
                continue
            allowed = {"extras", "slice", name}
            bad = sorted(set(roots(filt)) - allowed)
            if bad:
                errors.append(
                    f"{f}: stage '{name}' filter reads root key(s) {bad}, which are "
                    f"NOT in the filter's input. A filter sees "
                    f"{{extras, {name}}} -- use .extras.* for request data and "
                    f".{name}.* for the response body. jq resolves the absent keys to "
                    f"null silently, so the stage SUCCEEDS over an empty result and "
                    f"nothing is ever sent (snowplow#997).")


def verify_restaction_jq_compiles(errors):
    """Every jq expression in every RESTAction must COMPILE.

    Stronger than any pattern match, and it exists because a pattern match is what I
    reached for first. `$.spec.projectId` appeared in six path templates: `$` in jq
    must be followed by a variable NAME, so the whole expression is a syntax error.
    snowplow rejected it and logged `skipping api call: path jq expression errored` at
    DEBUG -- invisible at its default LOG_LEVEL=warn -- so every delegated write stage
    was silently disabled, delete and update alike. Nothing was ever sent.

    jq separates the two failures by exit code, which is what makes this usable:
        exit 3  syntax error  -- the expression can NEVER work, for any input
        otherwise             -- fine, or a runtime type error that depends on the
                                 input, which is NOT checked here since these run
                                 against live API responses
    Only a syntax error is an error here. snowplow#1001 proposes rejecting
    uncompilable templates at load time for the same reason: no input can save them.
    """
    jq = shutil.which("jq")
    if not jq:
        # Loud, not silent. A skipped check that prints nothing is indistinguishable
        # from one that passed -- the failure mode this whole file exists to prevent.
        print("WARNING: jq not found - RESTAction jq expressions NOT compile-checked.")
        print("         install jq to enable this gate (brew install jq / apt-get install jq)")
        return
    checked = 0
    for f in sorted(glob.glob("restactions/**/*.yaml", recursive=True)):
        try:
            doc = yaml.safe_load(open(f))
        except Exception:
            continue
        if not isinstance(doc, dict) or doc.get("kind") != "RESTAction":
            continue
        for st in (doc.get("spec") or {}).get("api") or []:
            name = st.get("name", "?")
            exprs = []
            m = re.match(r"^\s*\$\{(.*)\}\s*$", st.get("path") or "", flags=re.S)
            if m:
                exprs.append(("path", m.group(1).strip()))
            if st.get("filter"):
                exprs.append(("filter", st["filter"]))
            it = (st.get("dependsOn") or {}).get("iterator")
            if it:
                exprs.append(("dependsOn.iterator", it))
            for kind, expr in exprs:
                checked += 1
                r = subprocess.run([jq, "-n", expr], capture_output=True, text=True)
                if r.returncode == 3 or "syntax error" in (r.stderr or ""):
                    detail = (r.stderr or "").strip().splitlines()
                    errors.append(
                        f"{f}: stage '{name}' {kind} is not valid jq -- it can never "
                        f"work, for any input: "
                        f"{detail[0][:170] if detail else '(no detail)'}")
    if checked:
        print(f"Compile-checked {checked} RESTAction jq expression(s).")


def find_helm():
    return shutil.which("helm") or next(
        (p for p in ("/root/go/bin/helm", os.path.expanduser("~/go/bin/helm"))
         if os.path.isfile(p)), None)


def validate_composition_generated(errors):
    """The chart's _generated.tpl must match what gen_composition.py would write.

    Everything else generated in this repo is checked for staleness by CI
    regenerating and diffing; the chart's partial was not, which is half of why a
    chart that no cluster would accept shipped twice. The other half -- that the
    chart's own templates may still be wrong -- is only catchable against real CRDs,
    and that gate lives in the smoke job.
    """
    r = subprocess.run([sys.executable, os.path.join("scripts", "gen_composition.py"),
                        "--check"], capture_output=True, text=True)
    if r.returncode != 0:
        errors.append((r.stderr or r.stdout).strip() or
                      "gen_composition.py --check failed")


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
