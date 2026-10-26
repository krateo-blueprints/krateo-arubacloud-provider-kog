#!/usr/bin/env bash
# Reproduce the GA evidence on the RELEASED stack, not the local builds.
#
# Every live verdict in docs/coverage.md was measured on `kind-aruba-110`, which runs
# chart 0.24.0 with two locally built images -- and whose provider Deployment has
# since been patched away from its own Helm release. The install docs tell users to
# install the CI pin instead. No tier depends on unreleased code (responseTransform is
# in 0.27.1), but "it works on the pin" was an inference, not a measurement.
#
# This builds a cluster that matches CI exactly, with NO image overrides, and runs the
# free network-security chain on it: four resources, four drift blocks, zero cost. If
# create/observe/drift/delete all hold there, the evidence transfers.
#
# The client credential is NOT prompted for. It already exists in the evidence
# cluster, so it is piped cluster-to-cluster and never touches disk, argv or a log.
#
# Usage:
#   scripts/pin_verify.sh            # build, verify, and LEAVE the cluster up
#   KEEP=0 scripts/pin_verify.sh     # delete the cluster afterwards
set -uo pipefail

PIN="${OASGEN_VERSION:-0.27.1}"
NODE_IMAGE="${NODE_IMAGE:-kindest/node:v1.33.12}"   # same pin as CI; see validate.yaml
ESO_VERSION="${ESO_VERSION:-2.11.0}"               # as installed on the evidence cluster
CLUSTER="${CLUSTER:-aruba-pin}"
CTX="kind-${CLUSTER}"
export CTX
SRC_CTX="${SRC_CONTEXT:-kind-aruba-110}"           # where the credential already lives
CHAIN="${CHAIN:-tests/ga/chains/network-security.yaml}"
# The directory of RestDefinitions to apply. Point it at a prepared copy to try an
# experimental definition without putting it in restdefinitions/, where CI and every
# other run would pick it up. See tests/experimental/.
#
# It is a whole DIRECTORY and not an "apply this one afterwards" overlay, which was
# the first attempt and does not work: configurationFields are IMMUTABLE once the CRD
# has been generated, so applying a definition that narrows them over one already
# admitted is rejected outright --
#   spec.resource.configurationFields: Invalid value: ConfigurationFields are
#   immutable, you cannot change them once the CRD has been generated
# The experimental definition has to be the only one the cluster ever sees.
RESTDEFS_DIR="${RESTDEFS_DIR:-restdefinitions/}"
# Install the charts from a LOCAL directory and run LOCALLY BUILT images instead of
# the released ones. Everything this script exists to prove depends on using neither,
# so all three are empty by default and the log shouts when they are not.
#
# They exist for one job: testing an unreleased controller fix end to end against the
# live API. oasgen#218 (notFoundBody never evaluated on the delete path) is only
# observable that way -- the unit tests cover the helper, the fix is in the wiring,
# and the predicate it switches on is ours.
#
# CHART_SRC is a directory holding oasgen-provider/ and oasgen-provider-crds/. The
# repo's own Chart.yaml carries CHART_VERSION/APP_VERSION placeholders that are
# substituted at release time, so a checkout cannot be installed as-is -- substitute
# them in your copy first (helm rejects them as non-semver).
#
# PROVIDER_IMAGE and RDC_IMAGE take a full registry/repository:tag reference and are
# loaded into the kind node with `kind load docker-image`, so pullPolicy is forced to
# Never: with IfNotPresent a typo in the tag reaches the registry and the failure
# looks like a network problem.
CHART_SRC="${CHART_SRC:-}"
PROVIDER_IMAGE="${PROVIDER_IMAGE:-}"
RDC_IMAGE="${RDC_IMAGE:-}"
# Turn on REST_CONTROLLER_DEBUG in every generated controller. The lines that prove
# which branch of RDC's delete verification ran are all log.Debug, so without this a
# clean teardown and a teardown that silently fell back to trusting the DELETE are
# indistinguishable from the outside. It renders into each controller's ConfigMap via
# rdc.env, so it reaches controllers oasgen generates later too.
#
# This dumps HTTP bodies. Safe from 0.26.1 on; below that the dump included the
# Authorization header (oasgen#214), so do not set it on an older stack.
RDC_DEBUG="${RDC_DEBUG:-0}"
CHAIN_ARGS="${CHAIN_ARGS:-}"                       # e.g. --with-billable
KEEP="${KEEP:-1}"

say() { printf '\n=== %s ===\n' "$*"; }
# Split registry/repository:tag into --set arguments for one of the chart's image
# blocks ("image" or "rdc.image"). Appends to the HELM_SETS array.
img_sets() {
  local prefix="$1" ref="$2" reg repo tag
  tag="${ref##*:}"
  ref="${ref%:*}"
  reg="${ref%%/*}"
  repo="${ref#*/}"
  HELM_SETS+=(--set "${prefix}.registry=${reg}"
              --set "${prefix}.repository=${repo}"
              --set "${prefix}.tag=${tag}"
              --set "${prefix}.pullPolicy=Never")
}
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

cleanup() {
  if [ "$KEEP" = "0" ]; then
    say "deleting cluster ${CLUSTER}"
    kind delete cluster --name "$CLUSTER" >/dev/null 2>&1
  else
    printf '\ncluster %s left up (KEEP=0 to delete it)\n' "$CLUSTER"
  fi
}
trap cleanup EXIT

for bin in kind helm kubectl python3; do
  command -v "$bin" >/dev/null || die "$bin is not on PATH"
done
kubectl --context "$SRC_CTX" get secret aruba-client-credentials -n krateo-system \
  >/dev/null 2>&1 || die "no aruba-client-credentials in ${SRC_CTX}/krateo-system to copy"

say "creating ${CLUSTER} on ${NODE_IMAGE}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "    already exists, reusing"
else
  kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" || die "kind create failed"
fi

say "installing external-secrets ${ESO_VERSION}"
helm --kube-context "$CTX" repo add external-secrets \
  https://charts.external-secrets.io >/dev/null 2>&1
helm --kube-context "$CTX" repo update external-secrets >/dev/null 2>&1
helm --kube-context "$CTX" upgrade --install external-secrets \
  external-secrets/external-secrets --version "$ESO_VERSION" \
  --namespace external-secrets --create-namespace --wait --timeout 5m \
  || die "external-secrets install failed"

say "copying the client credential from ${SRC_CTX} (never touches disk)"
kubectl --context "$CTX" create namespace krateo-system >/dev/null 2>&1
# -o yaml straight into apply. The pipe is the point: the secret is never written to a
# file, never passed as an argument, and never echoed.
kubectl --context "$SRC_CTX" get secret aruba-client-credentials -n krateo-system -o yaml \
  | python3 -c '
import sys, yaml
d = yaml.safe_load(sys.stdin)
m = d["metadata"]
for k in ("resourceVersion", "uid", "creationTimestamp", "managedFields",
          "ownerReferences", "selfLink", "generation"):
    m.pop(k, None)
# The labels are load-bearing: ESO caches only labelled Secrets, and the webhook
# provider needs its own. Copying without them yields a bare "not found".
labels = m.setdefault("labels", {})
labels["reconcile.external-secrets.io/managed"] = "true"
labels["external-secrets.io/type"] = "webhook"
yaml.safe_dump(d, sys.stdout)
' | kubectl --context "$CTX" apply -f - >/dev/null || die "credential copy failed"
echo "    copied"

say "applying the ESO store and the token ExternalSecret"
kubectl --context "$CTX" apply -f eso/clustersecretstore.yaml -f eso/externalsecret.yaml \
  || die "eso manifests failed"
echo "    waiting for ESO to mint a token"
for i in $(seq 1 40); do
  if kubectl --context "$CTX" get secret arubacloud-token -n default \
       -o jsonpath='{.data.token}' 2>/dev/null | grep -q .; then
    echo "    arubacloud-token present after ${i} checks"; break
  fi
  sleep 15
  [ "$i" = 40 ] && die "ESO never produced arubacloud-token -- check the credential"
done

HELM_SETS=()
if [ -n "$PROVIDER_IMAGE" ] || [ -n "$RDC_IMAGE" ]; then
  say "loading locally built images into the kind node"
  for ref in "$PROVIDER_IMAGE" "$RDC_IMAGE"; do
    [ -n "$ref" ] || continue
    kind load docker-image "$ref" --name "$CLUSTER" >/dev/null \
      || die "kind load failed for ${ref} -- is it built?"
    echo "    loaded ${ref}"
  done
  [ -n "$PROVIDER_IMAGE" ] && img_sets image "$PROVIDER_IMAGE"
  [ -n "$RDC_IMAGE" ] && img_sets rdc.image "$RDC_IMAGE"
fi
if [ "$RDC_DEBUG" != "0" ]; then
  # --set-string, not --set: the values schema types rdc.env as string=>string, and
  # plain --set coerces `true` to a boolean, which the chart then rejects --
  #   at '/rdc/env/REST_CONTROLLER_DEBUG': got boolean, want string
  HELM_SETS+=(--set-string rdc.env.REST_CONTROLLER_DEBUG=true)
fi

if [ -n "$CHART_SRC" ]; then
  say "installing oasgen-provider from ${CHART_SRC} -- NOT A RELEASED STACK"
  echo "    overrides: ${HELM_SETS[*]:-none}"
  CRDS_CHART="${CHART_SRC}/oasgen-provider-crds"
  PROV_CHART="${CHART_SRC}/oasgen-provider"
  VERSION_ARGS=()
else
  say "installing oasgen-provider ${PIN} (RELEASED images, no overrides)"
  CRDS_CHART="oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds"
  PROV_CHART="oci://ghcr.io/krateo-platformops/charts/oasgen-provider"
  VERSION_ARGS=(--version "$PIN")
fi
# upgrade --install, not install: this script reuses an existing cluster when one is
# already there, and a plain install then fails with "cannot re-use a name that is
# still in use". The whole point is that it can be run again.
helm --kube-context "$CTX" upgrade --install oasgen-provider-crds \
  "$CRDS_CHART" ${VERSION_ARGS[@]+"${VERSION_ARGS[@]}"} \
  --namespace krateo-system --create-namespace --wait \
  || die "crds chart failed"
helm --kube-context "$CTX" upgrade --install oasgen-provider \
  "$PROV_CHART" ${VERSION_ARGS[@]+"${VERSION_ARGS[@]}"} ${HELM_SETS[@]+"${HELM_SETS[@]}"} \
  --namespace krateo-system --wait --timeout 5m \
  || die "provider chart failed"

say "images actually running (this is the whole point of the exercise)"
kubectl --context "$CTX" get deploy -n krateo-system \
  -o jsonpath='{range .items[*]}    {.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'

say "applying OAS ConfigMaps and RestDefinitions"
# -n krateo-system is not decoration: the manifests declare no namespace, and oasgen
# deploys each generated controller into its RestDefinition's namespace.
kubectl --context "$CTX" apply -n krateo-system -f configmaps/ >/dev/null \
  || die "configmaps failed"
# -R, as CI does: restdefinitions/ is one directory per provider, and without it
# kubectl refuses the directory outright ("recognized file extensions are ...").
[ "$RESTDEFS_DIR" = "restdefinitions/" ] || say "RestDefinitions from ${RESTDEFS_DIR}"
kubectl --context "$CTX" apply -n krateo-system -R -f "$RESTDEFS_DIR" >/dev/null \
  || die "restdefinitions failed"

say "waiting for the controllers ${CHAIN} needs"
# Derived from the chain itself rather than hardcoded, so this works for any chain
# passed in CHAIN. A hardcoded list silently waits for the wrong controllers the
# moment the chain changes, and then times out for a reason that looks like a
# provider fault.
KINDS=$(python3 -c "
import sys, yaml
c = yaml.safe_load(open('$CHAIN'))
print(' '.join(sorted({r['kind'].lower() for r in (c.get('resources') or [])})))
") || die "could not read kinds from $CHAIN"
echo "    kinds: ${KINDS}"
for kind in $KINDS; do
  for i in $(seq 1 40); do
    # Anchored on `-<kind>-controller$`: a bare `-vpc` also matches
    # `-vpcpeering-controller`, and `head -1` would then wait on the wrong one.
    d=$(kubectl --context "$CTX" get deploy -n krateo-system -o name 2>/dev/null \
        | grep -i -- "-${kind}-controller$" | head -1)
    if [ -n "$d" ] && kubectl --context "$CTX" rollout status "$d" -n krateo-system \
         --timeout=20s >/dev/null 2>&1; then
      echo "    ${kind}: $(basename "$d") ready"; break
    fi
    sleep 15
    [ "$i" = 40 ] && die "no ready controller for ${kind} after 10m"
  done
done

say "images actually running, including the RDCs"
# Printed again here on purpose. The earlier print runs before any RestDefinition
# exists, so there are no generated controllers yet and it can only show the provider
# -- while the RDC image is the more interesting half of the question.
kubectl --context "$CTX" get deploy -n krateo-system \
  -o jsonpath='{range .items[*]}{.spec.template.spec.containers[0].image}{"\n"}{end}' \
  | sort | uniq -c | sed 's/^/    /'

say "applying the generated Configurations (and ONLY those)"
# Deliberately not `apply -f samples/`. That directory also holds a CR sample per
# resource -- applying them would create live, billable resources on the account --
# and samples/arubacloud-token-secret.yaml, which would clobber the Secret ESO owns.
# Only the 34 *-configuration.yaml objects are wanted here.
# A missing CRD is NOT fatal here, and insisting it was cost a whole run: oasgen 0.32.0
# refuses to generate `Job`, whose plural collides with batch/jobs (oasgen#200), so
# job-configuration.yaml can never apply on that stack and `|| die` aborted before the
# chain -- for a resource the chain does not use. Each failure is named instead, and the
# run only dies if a configuration THIS CHAIN references is among them.
applied=0 missing=""
while IFS= read -r f; do
  if kubectl --context "$CTX" apply -n default -f "$f" >/dev/null 2>&1; then
    applied=$((applied + 1))
  else
    missing="${missing} $(basename "$f")"
  fi
done < <(find samples -name '*-configuration.yaml' | sort)
echo "    applied ${applied}"
[ -n "$missing" ] && echo "    NOT applied (no CRD -- fatal only if the chain needs it):${missing}"
python3 - "$CHAIN" <<'EOF' || die "a configuration this chain references did not apply"
import sys, yaml
chain = yaml.safe_load(open(sys.argv[1]))
need = set()
for r in chain.get("resources") or []:
    ref = (r.get("spec") or {}).get("configurationRef") or {}
    if ref.get("name"):
        need.add((r["kind"], ref["name"]))
import subprocess, os
ctx = os.environ["CTX"]
bad = []
for kind, name in sorted(need):
    rc = subprocess.run(["kubectl", "--context", ctx, "get",
                         f"{kind.lower()}configurations.arubacloud.ogen.krateo.io",
                         name, "-n", "default"],
                        capture_output=True).returncode
    if rc != 0:
        bad.append(f"{kind}Configuration/{name}")
if bad:
    print("    MISSING, and the chain needs them: " + ", ".join(bad))
    sys.exit(1)
print(f"    all {len(need)} configurations this chain references are present")
EOF

say "running ${CHAIN} on the released stack"
# CHAIN_ARGS carries flags through to the runner, e.g. --with-billable. Unquoted on
# purpose so it word-splits into separate arguments.
# shellcheck disable=SC2086
KUBE_CONTEXT="$CTX" python3 -u scripts/ga_chain.py "$CHAIN" ${CHAIN_ARGS:-}
rc=$?
say "chain exited ${rc}"
exit $rc
