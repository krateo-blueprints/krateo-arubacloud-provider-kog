#!/usr/bin/env bash
# Drive the dependent GA-core network chain through its full lifecycle.
#
# SecurityGroup, SecurityRule, VpcPeering and VpcPeeringRoute cannot be tested the
# way ga-lifecycle-test.sh tests a standalone resource: each needs an id that only
# exists once its parent has been created. That is the same runtime-reference
# problem the Composition hits (docs/lifecycle-beyond-crud.md) -- Helm cannot know
# a VPC's id at render time either. Here it is solved the blunt way, by reading each
# id out of status and substituting it into the next manifest.
#
#   vpc-a ──┬── securityGroup ── securityRule
#           └── vpcPeering (→ vpc-b) ── vpcPeeringRoute   [--with-billable]
#   vpc-b ──┘
#
# Teardown runs in reverse dependency order, and the script verifies the account is
# clean afterwards rather than assuming it. Anything it created is deleted even if a
# later step fails -- a half-built chain left behind is the expensive outcome.
#
# Usage:
#   scripts/ga-chain-test.sh [--with-billable]
#
#   --with-billable  also exercise VpcPeeringRoute, which carries a
#                    properties.billingPlan.billingPeriod and therefore COSTS MONEY.
#                    Omitted by default: everything else in this chain is free.
#
# Every interpolated id is QUOTED in the emitted YAML. Aruba ids are 24-character
# hex, so one consisting only of digits is perfectly legal and would otherwise be
# parsed as an integer -- the CRD then rejects it with "must be of type string".
# Rare, non-deterministic, and maddening to debug at 3am; quoting costs nothing.
#
# Requires a live token at /tmp/aruba.token and the GA Configurations applied.

set -uo pipefail

CONTEXT="${KUBE_CONTEXT:-kind-aruba-ga}"
TOKEN_FILE="${ARUBA_TOKEN_FILE:-/tmp/aruba.token}"
API="${ARUBA_API:-https://api.arubacloud.com}"
PROJECT="${ARUBA_PROJECT:-69a55c8d5e6f0e14f14093ff}"
LOCATION="${ARUBA_LOCATION:-ITBG-Bergamo}"
NS="${NS:-default}"
GROUP="arubacloud.ogen.krateo.io"
TIMEOUT="${TIMEOUT:-420}"
WITH_BILLABLE=0
[ "${1:-}" = "--with-billable" ] && WITH_BILLABLE=1

k() { kubectl --context "$CONTEXT" "$@"; }
CREATED=()   # "kind/name", newest last; torn down in reverse

[ -f "$TOKEN_FILE" ] || { echo "no token at $TOKEN_FILE -- run scripts/get-aruba-token.sh" >&2; exit 2; }
code=$(curl -s -o /dev/null -w '%{http_code}' -H "Authorization: Bearer $(cat "$TOKEN_FILE")" \
  "${API}/projects?api-version=1.0")
[ "$code" = "200" ] || { echo "token is not usable (HTTP ${code}) -- refresh it first" >&2; exit 2; }
echo "token OK, project ${PROJECT}"

teardown() {
  echo
  echo "=== teardown (reverse dependency order) ==="
  for (( i=${#CREATED[@]}-1 ; i>=0 ; i-- )); do
    local ref="${CREATED[$i]}" kind name
    kind="${ref%%/*}"; name="${ref##*/}"
    echo "--- deleting ${kind}/${name}"
    k delete "${kind}.${GROUP}" "$name" -n "$NS" --wait=true --timeout="${TIMEOUT}s" 2>&1 | sed 's/^/    /'
  done

  # Deletion is asynchronous at Aruba: the CR disappearing proves the finalizer ran,
  # not that anything was removed. Poll ground truth instead of trusting kubectl --
  # an early check races the delete and reports a false orphan, which cost a
  # debugging round the first time.
  echo
  echo "=== residue check (ground truth, allowing for async deletion) ==="
  local tok; tok=$(cat "$TOKEN_FILE" 2>/dev/null)
  for attempt in 1 2 3 4 5 6; do
    local left
    left=$(curl -s -H "Authorization: Bearer ${tok}" \
      "${API}/projects/${PROJECT}/providers/Aruba.Network/vpcs?api-version=1.0" 2>/dev/null \
      | python3 -c "
import json,sys
try: d=json.load(sys.stdin)
except Exception: print('?'); raise SystemExit
vals=d.get('values') or d.get('value') or []
print(len([v for v in vals if str(v.get('metadata',{}).get('name','')).startswith('ga-chain-')]))" 2>/dev/null)
    echo "    attempt ${attempt}: ga-chain-* VPCs still present: ${left}"
    [ "$left" = "0" ] && { echo "    clean"; return; }
    sleep 20
  done
  echo "    WARNING: ga-chain-* resources may remain -- check the Aruba console." >&2
}
# Any exit path tears down. A chain abandoned half-built is the costly failure.
trap teardown EXIT

apply_wait() { # apply_wait <kind> <name> <manifest-file>
  local kind="$1" name="$2" file="$3"
  echo "--- creating ${kind}/${name}"
  k apply -f "$file" >/dev/null || return 1
  CREATED+=("${kind}/${name}")

  local deadline=$(( $(date +%s) + TIMEOUT ))
  while :; do
    local ready
    ready=$(k get "${kind}.${GROUP}" "$name" -n "$NS" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    [ "$ready" = "True" ] && break
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "    TIMEOUT: ${kind}/${name} never became Ready" >&2
      k get "${kind}.${GROUP}" "$name" -n "$NS" \
        -o jsonpath='{.status.conditions[*].message}' 2>/dev/null | head -c 400; echo
      return 1
    fi
    sleep 10
  done

  # Two status shapes exist: metadata-wrapped resources report status.metadata.id,
  # Folder-style resources report a flat status.id. Try both rather than assuming.
  local id
  id=$(k get "${kind}.${GROUP}" "$name" -n "$NS" -o jsonpath='{.status.metadata.id}' 2>/dev/null)
  [ -n "$id" ] || id=$(k get "${kind}.${GROUP}" "$name" -n "$NS" -o jsonpath='{.status.id}' 2>/dev/null)
  [ -n "$id" ] || { echo "    no id in status for ${kind}/${name}" >&2; return 1; }
  echo "    Ready, id=${id}"
  printf '%s' "$id"
}

hdr() { printf '%s\n\n' "=== $* ==="; }

# ---------------------------------------------------------------- vpc-a, vpc-b
hdr "1. parent VPCs"
mkvpc() {
  cat <<EOF | tee /tmp/ga-$1.yaml >/dev/null
apiVersion: ${GROUP}/v1-0-0
kind: Vpc
metadata: {name: $1, namespace: ${NS}, annotations: {krateo.io/connector-verbose: 'true'}}
spec:
  configurationRef: {name: vpc-config, namespace: ${NS}}
  projectId: "${PROJECT}"
  metadata: {name: $1, location: {value: "${LOCATION}"}, tags: []}
  properties: {default: false, preset: false}
EOF
}
mkvpc ga-chain-vpc-a; VPC_A=$(apply_wait Vpc ga-chain-vpc-a /tmp/ga-ga-chain-vpc-a.yaml) || exit 1
mkvpc ga-chain-vpc-b; VPC_B=$(apply_wait Vpc ga-chain-vpc-b /tmp/ga-ga-chain-vpc-b.yaml) || exit 1

# ------------------------------------------------------------- security group
hdr "2. SecurityGroup in vpc-a"
cat > /tmp/ga-sg.yaml <<EOF
apiVersion: ${GROUP}/v1-0-0
kind: SecurityGroup
metadata: {name: ga-chain-sg, namespace: ${NS}, annotations: {krateo.io/connector-verbose: 'true'}}
spec:
  configurationRef: {name: securitygroup-config, namespace: ${NS}}
  projectId: "${PROJECT}"
  vpcId: "${VPC_A}"
  metadata: {name: ga-chain-sg, location: {value: "${LOCATION}"}, tags: []}
  properties: {default: false, preset: false}
EOF
SG=$(apply_wait SecurityGroup ga-chain-sg /tmp/ga-sg.yaml) || exit 1

# -------------------------------------------------------------- security rule
hdr "3. SecurityRule in the SecurityGroup"
cat > /tmp/ga-sr.yaml <<EOF
apiVersion: ${GROUP}/v1-0-0
kind: SecurityRule
metadata: {name: ga-chain-sr, namespace: ${NS}, annotations: {krateo.io/connector-verbose: 'true'}}
spec:
  configurationRef: {name: securityrule-config, namespace: ${NS}}
  projectId: "${PROJECT}"
  vpcId: "${VPC_A}"
  securityGroupId: "${SG}"
  metadata: {name: ga-chain-sr, location: {value: "${LOCATION}"}, tags: []}
  properties:
    direction: Ingress
    protocol: TCP
    port: "443"
    target: {kind: Ip, value: "0.0.0.0/0"}
EOF
apply_wait SecurityRule ga-chain-sr /tmp/ga-sr.yaml >/dev/null || exit 1

# ---------------------------------------------------------------- vpc peering
hdr "4. VpcPeering vpc-a -> vpc-b"
# remoteVpc is a URI, not a bare id -- the runtime-reference shape the Composition
# cannot express at template time.
cat > /tmp/ga-peer.yaml <<EOF
apiVersion: ${GROUP}/v1-0-0
kind: VpcPeering
metadata: {name: ga-chain-peering, namespace: ${NS}, annotations: {krateo.io/connector-verbose: 'true'}}
spec:
  configurationRef: {name: vpcpeering-config, namespace: ${NS}}
  projectId: "${PROJECT}"
  vpcId: "${VPC_A}"
  metadata: {name: ga-chain-peering, location: {value: "${LOCATION}"}, tags: []}
  properties:
    remoteVpc: {uri: "/projects/${PROJECT}/providers/Aruba.Network/vpcs/${VPC_B}"}
EOF
PEER=$(apply_wait VpcPeering ga-chain-peering /tmp/ga-peer.yaml) || exit 1

# ---------------------------------------------------------- peering route ($$)
if [ "$WITH_BILLABLE" = "1" ]; then
  hdr "5. VpcPeeringRoute (BILLABLE)"
  cat > /tmp/ga-route.yaml <<EOF
apiVersion: ${GROUP}/v1-0-0
kind: VpcPeeringRoute
metadata: {name: ga-chain-route, namespace: ${NS}, annotations: {krateo.io/connector-verbose: 'true'}}
spec:
  configurationRef: {name: vpcpeeringroute-config, namespace: ${NS}}
  projectId: "${PROJECT}"
  vpcId: "${VPC_A}"
  vpcPeeringId: "${PEER}"
  metadata: {name: ga-chain-route, location: {value: "${LOCATION}"}, tags: []}
  properties:
    localNetworkAddress: "10.10.0.0/16"
    remoteNetworkAddress: "10.20.0.0/16"
    billingPlan: {billingPeriod: Hour}
EOF
  apply_wait VpcPeeringRoute ga-chain-route /tmp/ga-route.yaml >/dev/null || exit 1
else
  echo "5. VpcPeeringRoute SKIPPED -- it carries a billingPlan and costs money."
  echo "   Re-run with --with-billable to include it."
fi

echo
echo "RESULT: chain created and observed."
echo "  vpc-a=${VPC_A} vpc-b=${VPC_B} sg=${SG} rule=ok peering=${PEER}"
echo "Teardown follows; residue is verified after it."
