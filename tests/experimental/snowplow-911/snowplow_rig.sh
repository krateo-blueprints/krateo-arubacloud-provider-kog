#!/usr/bin/env bash
# A MINIMAL rig for asking snowplow what X-Snowplow-Stage-Outcomes it reports.
#
# Deliberately NO oasgen-provider and no generated controllers. snowplow's /call is
# answered by snowplow, so the provider is an unrelated moving part -- and on a
# single kind node 36 generated controllers saturate the node's systemd (pods stick
# in ContainerCreating with "Timeout waiting for systemd to create ... .slice"),
# which is how the full-stack attempt died. This rig isolates the thing under test.
#
# It installs: authn-crds, authn, snowplow-crds, snowplow, the Aruba endpoint Secret,
# both RESTActions, an identity for the caller, and a curl pod that authenticates the
# same way RDC does (audience-bound projected token -> authn /serviceaccount/login).
set -uo pipefail
CLUSTER="${CLUSTER:-sp-rig}"
CTX="kind-${CLUSTER}"
KC="${TMPDIR:-/tmp}/kind-${CLUSTER}.kubeconfig"
export KUBECONFIG="$KC"
NODE_IMAGE="${NODE_IMAGE:-kindest/node:v1.36.4}"
SNOWPLOW_VERSION="${SNOWPLOW_VERSION:-1.12.57}"
AUTHN_VERSION="${AUTHN_VERSION:-0.27.3}"
# Extra `--set` flags for the authn chart, space-separated, e.g. "replicaCount=3".
# Additive and empty by default, so the snowplow#911 fixture this rig was written
# for behaves identically whether or not a caller sets it.
AUTHN_EXTRA_SET="${AUTHN_EXTRA_SET:-}"
SNOWPLOW_DIGEST="${SNOWPLOW_DIGEST:-}"
SRC_CTX="${SRC_CONTEXT:-kind-aruba-110}"
# KUBECONFIG below is narrowed to this rig's own file so nothing here can touch
# another cluster by accident. That also hides the SOURCE context, so the credential
# read needs its own --kubeconfig: without it the step fails with
#   error: context "kind-aruba-110" does not exist
# which looks like the evidence cluster is gone when it is simply out of scope.
SRC_KUBECONFIG="${SRC_KUBECONFIG:-${HOME}/.kube/config}"
NS=krateo-system
ARUBA_API="${ARUBA_API:-https://api.arubacloud.com}"

say() { printf '\n=== %s ===\n' "$*"; }
die() { printf '\nerror: %s\n' "$*" >&2; exit 1; }
k()   { kubectl --context "$CTX" "$@"; }
h()   { helm --kube-context "$CTX" "$@"; }

say "creating ${CLUSTER} on ${NODE_IMAGE}"
if kind get clusters 2>/dev/null | grep -qx "$CLUSTER"; then
  echo "    already exists, reusing"
else
  kind create cluster --name "$CLUSTER" --image "$NODE_IMAGE" --kubeconfig "$KC" >/dev/null \
    || die "kind create failed"
fi

say "installing authn ${AUTHN_VERSION}"
h upgrade --install authn-crds oci://ghcr.io/krateo-platformops/charts/authn-crds \
  --version "$AUTHN_VERSION" -n "$NS" --create-namespace --wait --timeout 5m >/dev/null \
  || die "authn-crds failed"
# PEM RSA signing key, generated into a variable and piped: never disk, argv or log.
AUTHN_KEY="$(openssl genrsa 2048 2>/dev/null)"
[ -n "$AUTHN_KEY" ] || die "openssl produced no key"
k create secret generic authn-jwt-signing-key -n "$NS" \
  --from-literal=private.pem="$AUTHN_KEY" --dry-run=client -o yaml | k apply -f - >/dev/null \
  || die "signing-key Secret failed"
unset AUTHN_KEY
authn_sets=(--set service.type=ClusterIP)
for kv in $AUTHN_EXTRA_SET; do authn_sets+=(--set "$kv"); done
[ -n "$AUTHN_EXTRA_SET" ] && echo "    extra: ${AUTHN_EXTRA_SET}"
h upgrade --install authn oci://ghcr.io/krateo-platformops/charts/authn \
  --version "$AUTHN_VERSION" -n "$NS" --wait --timeout 10m \
  "${authn_sets[@]}" >/dev/null || die "authn failed"

say "installing snowplow ${SNOWPLOW_VERSION}"
h upgrade --install snowplow-crds oci://ghcr.io/krateo-platformops/charts/snowplow-crds \
  --version "$SNOWPLOW_VERSION" -n "$NS" --wait --timeout 5m >/dev/null \
  || die "snowplow-crds failed"
h upgrade --install snowplow oci://ghcr.io/krateo-platformops/charts/snowplow \
  --version "$SNOWPLOW_VERSION" -n "$NS" --wait --timeout 15m \
  --set service.type=ClusterIP >/dev/null || die "snowplow failed"

SP_RUNNING="$(k get pods -n "$NS" -l app.kubernetes.io/name=snowplow \
  -o jsonpath='{.items[0].status.containerStatuses[0].imageID}')"
echo "    running image: ${SP_RUNNING}"
if [ -n "$SNOWPLOW_DIGEST" ]; then
  m=""; for w in $SNOWPLOW_DIGEST; do case "$SP_RUNNING" in *"$w"*) m="$w"; break;; esac; done
  [ -n "$m" ] && echo "    digest MATCHES ${m}" \
    || die "snowplow is not running an accepted digest
    accepted: ${SNOWPLOW_DIGEST}
    running : ${SP_RUNNING}"
fi

say "the Aruba endpoint Secret (token piped from ${SRC_CTX}, never to disk)"
TOK="$(kubectl --kubeconfig "$SRC_KUBECONFIG" --context "$SRC_CTX" \
  get secret arubacloud-token -n default \
  -o jsonpath='{.data.token}' | base64 --decode)"
[ -n "$TOK" ] || die "no Aruba token in ${SRC_CTX}"
k create secret generic arubacloud-endpoint -n "$NS" \
  --from-literal=server-url="$ARUBA_API" --from-literal=token="$TOK" \
  --dry-run=client -o yaml | k apply -f - >/dev/null || die "endpoint Secret failed"
unset TOK

say "the caller's identity, and the RBAC authn does not author"
k create serviceaccount sp-caller -n "$NS" --dry-run=client -o yaml | k apply -f - >/dev/null
k apply -f - >/dev/null <<EOF || die "identity failed"
apiVersion: serviceaccount.authn.krateo.io/v1alpha1
kind: ServiceAccount
metadata: {name: sp-caller, namespace: ${NS}}
spec:
  displayName: snowplow /call probe
  serviceAccountRef: {namespace: ${NS}, name: sp-caller}
  groups: [krateo-rdc]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: Role
metadata: {name: krateo-rdc-restactions, namespace: ${NS}}
rules:
  - apiGroups: ["templates.krateo.io"]
    resources: ["restactions"]
    verbs: ["get", "list", "watch"]
  - apiGroups: ["templates.krateo.io"]
    resources: ["restactions/status"]
    verbs: ["get", "update", "patch"]
  - apiGroups: [""]
    resources: ["secrets"]
    resourceNames: ["arubacloud-endpoint"]
    verbs: ["get"]
---
apiVersion: rbac.authorization.k8s.io/v1
kind: RoleBinding
metadata: {name: krateo-rdc-restactions, namespace: ${NS}}
roleRef: {apiGroup: rbac.authorization.k8s.io, kind: Role, name: krateo-rdc-restactions}
subjects:
  - {apiGroup: rbac.authorization.k8s.io, kind: Group, name: krateo-rdc}
EOF

say "applying both RESTActions"
k apply -n "$NS" -f restactions/compute/cloudserver-delete.restaction.yaml >/dev/null \
  || die "shipped RESTAction failed to apply"
k apply -n "$NS" -f tests/experimental/snowplow-911/restaction.yaml >/dev/null \
  || die "probe RESTAction failed to apply"
k get restactions.templates.krateo.io -n "$NS" 2>&1 | sed 's/^/    /'

say "the curl pod, with the same audience-bound projected token RDC uses"
k apply -f - >/dev/null <<EOF || die "probe pod failed"
apiVersion: v1
kind: Pod
metadata: {name: call-probe, namespace: ${NS}}
spec:
  serviceAccountName: sp-caller
  restartPolicy: Never
  containers:
  - name: c
    image: curlimages/curl:8.11.1
    command: ["sleep", "7200"]
    volumeMounts:
    - {name: authn-token, mountPath: /var/run/secrets/krateo.io/serviceaccount, readOnly: true}
  volumes:
  - name: authn-token
    projected:
      sources:
      - serviceAccountToken: {audience: authn, expirationSeconds: 3600, path: token}
EOF
k wait --for=condition=Ready pod/call-probe -n "$NS" --timeout=180s >/dev/null \
  || die "probe pod never became Ready"

say "rig up"
printf 'reach it with: KUBECONFIG=%s kubectl --context %s get pods -n %s\n' "$KC" "$CTX" "$NS"
