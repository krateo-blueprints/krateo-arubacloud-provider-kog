#!/usr/bin/env bash
# Call snowplow's /call directly, as RDC's own identity, and capture the RAW
# X-Snowplow-Stage-Outcomes header.
#
# Why not read it out of RDC's log: RDC decides what it logs. This asks snowplow
# itself, over the same route RDC uses, authenticating as the same identity --
# nothing here is a privileged shortcut.
set -uo pipefail
# The rig's cluster, not a full pin_verify one: this probe talks to snowplow, which
# needs no oasgen-provider. See snowplow_rig.sh and the README.
CLUSTER="${CLUSTER:-sp-rig}"
CTX="kind-${CLUSTER}"
export KUBECONFIG="${TMPDIR:-/tmp}/kind-${CLUSTER}.kubeconfig"
NS=krateo-system
# The rig's own caller identity. On a real cluster this would be the per-resource SA
# oasgen generates (e.g. cloudservers-arubacloud.ogen.krateo.io-v1alpha1) -- which
# must be READ off the generated Deployment, never constructed, because oasgen names
# it <plural>-<group>-v1alpha1 even where the CRD's served version differs.
SA="${SA:-sp-caller}"
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
# NEVER print this response body. authn's login returns a whole kubeconfig for the
# issued identity, including client-key-data -- a PRIVATE KEY. An earlier version of
# this script piped it through `head -40` and put it in a terminal log. Only the
# status line and non-secret identity fields are shown.
k exec -n "$NS" call-probe -- sh -c "
  T=\$(cat /var/run/secrets/krateo.io/serviceaccount/token)
  curl -sS -o /tmp/login.json -w 'http_status=%{http_code}\n' -X POST \
    -H \"Authorization: Bearer \$T\" \
    'http://${AUTHN_SVC}.${NS}.svc.cluster.local:${AUTHN_PORT}/serviceaccount/login'
  # which fields came back, and the identity -- not the credentials in them
  echo \"top-level keys: \$(tr ',{}' '\n\n\n' < /tmp/login.json | grep -o '\"[a-zA-Z]*\":' | tr -d '\":' | sort -u | tr '\n' ' ')\"
  echo \"username      : \$(tr ',{}' '\n\n\n' < /tmp/login.json | grep -m1 '\"username\"' | sed 's/.*:\"//; s/\"\$//')\"
  echo \"groups        : \$(tr '{}' '\n\n' < /tmp/login.json | grep -m1 '\"groups\"' | sed 's/.*\"groups\"://; s/,\".*//')\"
" 2>&1

echo
echo "=== STEP 2: GET /call with the JWT, full response headers ==="
k exec -n "$NS" call-probe -- sh -c "
  T=\$(cat /var/run/secrets/krateo.io/serviceaccount/token)
  # The field is accessToken -- camelCase. An earlier pattern looked for
  # token|jwt|access_token|id_token and matched none of them, which presented as
  # "NO JWT PARSED" on a login that had in fact returned 200.
  JWT=\$(curl -sS -X POST -H \"Authorization: Bearer \$T\" \
    'http://${AUTHN_SVC}.${NS}.svc.cluster.local:${AUTHN_PORT}/serviceaccount/login' \
    | tr ',{}' '\n\n\n' | grep -o '\"accessToken\":\"[^\"]*\"' \
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
