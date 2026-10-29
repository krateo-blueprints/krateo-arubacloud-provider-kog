#!/usr/bin/env bash
# Call snowplow's /call directly, as RDC's own identity, and capture the RAW
# X-Snowplow-Stage-Outcomes header.
#
# Why not read it out of RDC's log: RDC decides what it logs. This asks snowplow
# itself, over the same route RDC uses, authenticating as the same identity --
# nothing here is a privileged shortcut.
set -uo pipefail
CTX="kind-aruba-pin"
export KUBECONFIG="${TMPDIR:-/tmp}/kind-aruba-pin.kubeconfig"
NS=krateo-system
SA="cloudservers-arubacloud.ogen.krateo.io-v1alpha1"
RA="${1:?usage: call_probe.sh <restaction-name> <cr-name>}"
CRNAME="${2:?}"
PROJECT=69a55c8d5e6f0e14f14093ff

k() { kubectl --context "$CTX" "$@"; }

# A probe pod carrying RDC's ServiceAccount and the SAME audience-bound projected
# token, so authn maps it to the same identity (rdc-cloudserver) that RDC gets.
if ! k get pod call-probe -n "$NS" >/dev/null 2>&1; then
  cat <<EOF | k apply -f - >/dev/null
apiVersion: v1
kind: Pod
metadata: {name: call-probe, namespace: ${NS}}
spec:
  serviceAccountName: ${SA}
  restartPolicy: Never
  containers:
  - name: c
    image: curlimages/curl:8.11.1
    command: ["sleep", "3600"]
    volumeMounts:
    - {name: authn-token, mountPath: /var/run/secrets/krateo.io/serviceaccount, readOnly: true}
  volumes:
  - name: authn-token
    projected:
      sources:
      - serviceAccountToken: {audience: authn, expirationSeconds: 3600, path: token}
EOF
  k wait --for=condition=Ready pod/call-probe -n "$NS" --timeout=120s >/dev/null || {
    echo "probe pod never became Ready"; k describe pod call-probe -n "$NS" | tail -20; exit 1; }
fi

AUTHN_SVC=$(k get svc -n "$NS" -l app.kubernetes.io/name=authn -o jsonpath='{.items[0].metadata.name}')
AUTHN_PORT=$(k get svc -n "$NS" "$AUTHN_SVC" -o jsonpath='{.spec.ports[0].port}')
SP_SVC=$(k get svc -n "$NS" -l app.kubernetes.io/name=snowplow -o jsonpath='{.items[0].metadata.name}')
SP_PORT=$(k get svc -n "$NS" "$SP_SVC" -o jsonpath='{.spec.ports[0].port}')

echo "=== authn: ${AUTHN_SVC}:${AUTHN_PORT}   snowplow: ${SP_SVC}:${SP_PORT} ==="

# extras = the CR fields this RESTAction's paths and filters read: .spec.projectId
# and .spec.metadata.name. Same shape RDC forwards on a delete.
EXTRAS="{\"spec\":{\"projectId\":\"${PROJECT}\",\"metadata\":{\"name\":\"${CRNAME}\"}}}"

echo "=== STEP 1: exchange the projected SA token at authn /serviceaccount/login ==="
k exec -n "$NS" call-probe -- sh -c "
  set -e
  T=\$(cat /var/run/secrets/krateo.io/serviceaccount/token)
  curl -sS -i -X POST \
    -H \"Authorization: Bearer \$T\" \
    'http://${AUTHN_SVC}.${NS}.svc.cluster.local:${AUTHN_PORT}/serviceaccount/login' \
  | head -40
" 2>&1

echo
echo "=== STEP 2: GET /call with the JWT, full response headers ==="
k exec -n "$NS" call-probe -- sh -c "
  T=\$(cat /var/run/secrets/krateo.io/serviceaccount/token)
  JWT=\$(curl -sS -X POST -H \"Authorization: Bearer \$T\" \
    'http://${AUTHN_SVC}.${NS}.svc.cluster.local:${AUTHN_PORT}/serviceaccount/login' \
    | tr ',{}' '\n\n\n' | grep -i -E '\"(token|jwt|access_token|id_token)\"' \
    | head -1 | sed 's/.*:\"//; s/\"\$//')
  if [ -z \"\$JWT\" ]; then echo 'NO JWT PARSED -- step 1 body above shows why'; exit 1; fi
  echo \"jwt length: \${#JWT}\"
  curl -sS -D - -o /tmp/body.json -G \
    --data-urlencode 'apiVersion=templates.krateo.io/v1' \
    --data-urlencode 'resource=restactions' \
    --data-urlencode 'namespace=${NS}' \
    --data-urlencode 'name=${RA}' \
    --data-urlencode 'extras=${EXTRAS}' \
    -H \"Authorization: Bearer \$JWT\" \
    'http://${SP_SVC}.${NS}.svc.cluster.local:${SP_PORT}/call'
  echo '--- body (first 1500 chars) ---'
  head -c 1500 /tmp/body.json
" 2>&1
