#!/usr/bin/env bash
# Regenerate api-versions.txt from the cluster of the current kubeconfig
# context. Read-only: it only queries the discovery API.
set -euo pipefail
out="$(dirname "${BASH_SOURCE[0]}")/api-versions.txt"
{
  echo v1
  kubectl get --raw /api/v1 | jq -r '.resources[] | select(.name | contains("/") | not) | "v1/" + .kind'
  for gv in $(kubectl get --raw /apis | jq -r '.groups[].versions[].groupVersion'); do
    echo "$gv"
    kubectl get --raw "/apis/$gv" | jq -r --arg gv "$gv" '.resources[] | select(.name | contains("/") | not) | $gv + "/" + .kind'
  done
} | sort -u > "$out"
echo "wrote $(wc -l < "$out") entries to $out"
