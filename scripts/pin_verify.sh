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
SRC_CTX="${SRC_CONTEXT:-kind-aruba-110}"           # where the credential already lives
CHAIN="${CHAIN:-tests/ga/chains/network-security.yaml}"
KEEP="${KEEP:-1}"

say() { printf '\n=== %s ===\n' "$*"; }
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

say "installing oasgen-provider ${PIN} (RELEASED images, no overrides)"
helm --kube-context "$CTX" install oasgen-provider-crds \
  oci://ghcr.io/krateo-platformops/charts/oasgen-provider-crds \
  --version "$PIN" --namespace krateo-system --create-namespace --wait \
  || die "crds chart failed"
helm --kube-context "$CTX" install oasgen-provider \
  oci://ghcr.io/krateo-platformops/charts/oasgen-provider \
  --version "$PIN" --namespace krateo-system --wait --timeout 5m \
  || die "provider chart failed"

say "images actually running (this is the whole point of the exercise)"
kubectl --context "$CTX" get deploy -n krateo-system \
  -o jsonpath='{range .items[*]}    {.metadata.name}{"\t"}{.spec.template.spec.containers[0].image}{"\n"}{end}'

say "applying OAS ConfigMaps and RestDefinitions"
# -n krateo-system is not decoration: the manifests declare no namespace, and oasgen
# deploys each generated controller into its RestDefinition's namespace.
kubectl --context "$CTX" apply -n krateo-system -f configmaps/ >/dev/null \
  || die "configmaps failed"
kubectl --context "$CTX" apply -n krateo-system -f restdefinitions/ >/dev/null \
  || die "restdefinitions failed"

say "waiting for the controllers the chain needs"
for res in vpcs subnets securitygroups securityrules; do
  for i in $(seq 1 40); do
    d=$(kubectl --context "$CTX" get deploy -n krateo-system -o name 2>/dev/null \
        | grep -i -- "-${res%s}" | head -1)
    if [ -n "$d" ] && kubectl --context "$CTX" rollout status "$d" -n krateo-system \
         --timeout=20s >/dev/null 2>&1; then
      echo "    ${res}: $(basename "$d") ready"; break
    fi
    sleep 15
    [ "$i" = 40 ] && die "no ready controller for ${res} after 10m"
  done
done

say "applying the generated Configurations (and ONLY those)"
# Deliberately not `apply -f samples/`. That directory also holds a CR sample per
# resource -- applying them would create live, billable resources on the account --
# and samples/arubacloud-token-secret.yaml, which would clobber the Secret ESO owns.
# Only the 34 *-configuration.yaml objects are wanted here.
find samples -name '*-configuration.yaml' -print0 \
  | xargs -0 -n1 -I{} kubectl --context "$CTX" apply -n default -f {} >/dev/null \
  || die "configurations failed"
echo "    applied $(find samples -name '*-configuration.yaml' | wc -l | tr -d ' ')"

say "running ${CHAIN} on the released stack"
KUBE_CONTEXT="$CTX" python3 -u scripts/ga_chain.py "$CHAIN"
rc=$?
say "chain exited ${rc}"
exit $rc
