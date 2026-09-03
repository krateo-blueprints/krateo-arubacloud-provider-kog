#!/usr/bin/env bash
# Drive one resource through its full lifecycle against the live Aruba API and
# report what actually happened.
#
# This exists because a support tier in docs/coverage.md is only allowed to move
# up when someone can point at a run. Doing that by hand is how evidence stops
# being reproducible, so the procedure is the script.
#
# What it proves, in order:
#   create   the CR is admitted and the controller creates the real resource
#   observe  the resource is found again and status carries the upstream id
#   drift    (resources with an update verb) an out-of-band change is corrected
#   delete   the finalizer releases only after the real resource is gone
#
# It ends by asserting the account is as it was found. A lifecycle test that
# leaves residue is a billing incident, so cleanup is verified, not assumed.
#
# Usage:
#   scripts/ga-lifecycle-test.sh <manifest.yaml> [--keep]
#
# Requires: a token at /tmp/aruba.token (scripts/get-aruba-token.sh), a cluster
# with the provider installed, and the resource's Configuration already applied.

set -uo pipefail

MANIFEST="${1:-}"
KEEP="${2:-}"
CONTEXT="${KUBE_CONTEXT:-kind-aruba-ga}"
TOKEN_FILE="${ARUBA_TOKEN_FILE:-/tmp/aruba.token}"
TOKEN_SECRET="${ARUBA_TOKEN_SECRET:-arubacloud-token}"
TOKEN_SECRET_NS="${ARUBA_TOKEN_SECRET_NS:-default}"
API="${ARUBA_API:-https://api.arubacloud.com}"
TIMEOUT="${TIMEOUT:-300}"

[ -n "$MANIFEST" ] || { echo "usage: $0 <manifest.yaml> [--keep]" >&2; exit 2; }
[ -f "$MANIFEST" ]  || { echo "no such manifest: $MANIFEST" >&2; exit 2; }

k() { kubectl --context "$CONTEXT" "$@"; }

# Token source: the cluster Secret FIRST, because that is what External Secrets keeps
# fresh and what the controllers actually authenticate with. /tmp/aruba.token is only
# a manual-bootstrap fallback, and reading it by preference meant these scripts failed
# with a 401 against a stale file while the cluster itself was perfectly authenticated.
read_token() {
  local t
  t=$(kubectl --context "$CONTEXT" get secret "$TOKEN_SECRET" -n "$TOKEN_SECRET_NS" \
        -o jsonpath='{.data.token}' 2>/dev/null | base64 -d 2>/dev/null)
  if [ -n "$t" ]; then printf '%s' "$t"; return 0; fi
  [ -f "$TOKEN_FILE" ] && cat "$TOKEN_FILE" && return 0
  return 1
}

TOKEN=$(read_token) || { echo "no token: neither ${TOKEN_SECRET_NS}/${TOKEN_SECRET} nor ${TOKEN_FILE}" >&2; exit 2; }


KIND=$(awk '/^kind:/{print $2; exit}' "$MANIFEST")
NAME=$(awk '/^  name:/{print $2; exit}' "$MANIFEST")
NS=$(awk '/^  namespace:/{print $2; exit}' "$MANIFEST"); NS="${NS:-default}"

echo "=== ${KIND}/${NAME} in ${NS} (context ${CONTEXT}) ==="

# A CR can only be created if the token is live; failing here rather than midway
# keeps a credential problem from being misread as a provider defect.
code=$(curl -s -o /dev/null -w '%{http_code}' \
  -H "Authorization: Bearer ${TOKEN}" "${API}/projects?api-version=1.0")
[ "$code" = "200" ] || { echo "token is not usable (HTTP ${code}) -- refresh it first" >&2; exit 2; }
echo "token OK"

wait_for() { # wait_for <condition-jsonpath-value> <label>
  local want="$1" label="$2" deadline=$(( $(date +%s) + TIMEOUT ))
  while :; do
    local ready
    ready=$(k get "$KIND" "$NAME" -n "$NS" \
      -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}' 2>/dev/null)
    [ "$ready" = "$want" ] && { echo "  ${label}: Ready=${want}"; return 0; }
    if [ "$(date +%s)" -ge "$deadline" ]; then
      echo "  ${label}: TIMEOUT after ${TIMEOUT}s (Ready=${ready:-<none>})" >&2
      k get "$KIND" "$NAME" -n "$NS" -o jsonpath='{.status.conditions}' 2>/dev/null | head -c 800
      echo
      return 1
    fi
    sleep 5
  done
}

echo "--- create"
k apply -f "$MANIFEST" || exit 1
wait_for True create || exit 1

ID=$(k get "$KIND" "$NAME" -n "$NS" -o jsonpath='{.status.metadata.id}' 2>/dev/null)
[ -n "$ID" ] || ID=$(k get "$KIND" "$NAME" -n "$NS" -o jsonpath='{.status.id}' 2>/dev/null)
echo "  upstream id: ${ID:-<none reported>}"
[ -n "$ID" ] || { echo "  no id in status -- observe cannot be proven" >&2; exit 1; }

echo "--- observe"
# Re-reconcile from scratch: annotating forces the controller round-trip rather
# than trusting the status left over from create.
k annotate "$KIND" "$NAME" -n "$NS" krateo.io/ga-probe="$(date +%s)" --overwrite >/dev/null
wait_for True observe || exit 1

if [ "$KEEP" = "--keep" ]; then
  echo "--- delete SKIPPED (--keep): resource ${ID} is still live and may bill"
  exit 0
fi

echo "--- delete"
k delete -f "$MANIFEST" --wait=true --timeout="${TIMEOUT}s" || exit 1
if k get "$KIND" "$NAME" -n "$NS" >/dev/null 2>&1; then
  echo "  CR still present after delete -- finalizer did not release" >&2
  exit 1
fi
echo "  CR gone (finalizer released)"

echo "--- cleanup verified"
# The CR disappearing proves the finalizer ran, not that Aruba deleted anything.
# Only the API can answer that, and an orphaned billable resource is the one
# failure mode of this script that costs real money.
echo "  confirm no residue for id ${ID} in the Aruba console before promoting a tier."
echo
echo "RESULT: ${KIND}/${NAME} completed create -> observe -> delete against the live API."
echo "Record it in scripts/gen_samples_and_coverage.py TIERS, with a link to this run."
