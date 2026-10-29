#!/usr/bin/env bash
# Restore aruba-110's generated controllers to the replica counts they had before
# they were scaled to 0 to free CPU on a saturated Docker VM (2026-10-29).
#
# Scaling them down did NOT touch the credential: external-secrets, the
# ClusterSecretStore, the ExternalSecret and the arubacloud-token Secret are all in
# other namespaces and were left running. The CRDs and RestDefinitions are untouched,
# so a scale back up is the whole restore.
set -euo pipefail
export KUBECONFIG="${KUBECONFIG:-$HOME/.kube/config}"
LIST="$(dirname "$0")/aruba110-replicas.txt"
[ -r "$LIST" ] || { echo "missing $LIST"; exit 1; }
while read -r name replicas; do
  [ -n "$name" ] || continue
  kubectl --context kind-aruba-110 -n krateo-system scale deploy "$name" \
    --replicas="${replicas:-1}" >/dev/null
  echo "  restored $name -> ${replicas:-1}"
done < "$LIST"
