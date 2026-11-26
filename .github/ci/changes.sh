#!/usr/bin/env bash
# Decide which CI jobs a pull request needs, from the files it changes.
# Writes manifests/full/docs/renovate=true|false to $GITHUB_OUTPUT.
#   manifests: something Flux applies, or the CI that validates it, changed
#   full:      render every HelmRelease, not only the changed ones
set -euo pipefail

out=${GITHUB_OUTPUT:-/dev/stdout}
emit() { printf '%s\n' "manifests=$1" "full=$2" "docs=$3" "renovate=$4" | tee -a "$out"; }

if [ "${EVENT_NAME:-}" != "pull_request" ]; then
  emit true true true true
  exit 0
fi

# actions/checkout checks out the merge commit: its first parent is the base.
mapfile -t files < <(git diff --name-only HEAD^1 HEAD)
printf 'changed: %s\n' "${files[@]}"

has() {
  local f
  for f in "${files[@]}"; do
    [[ $f =~ $1 ]] && return 0
  done
  return 1
}

manifests=false full=false docs=false renovate=false
if has '^(apps|infrastructure|clusters|charts)/|^\.sops\.yaml$'; then manifests=true; fi
# A change to the CI itself, the cluster entry point or the CRDs can affect
# every HelmRelease.
if has '^\.github/ci/|^\.github/workflows/ci\.yaml$|^mise\.toml$|^policy/|^clusters/|^infrastructure/crds/'; then
  manifests=true full=true
fi
if has '^docs/|^mkdocs\.yml$|^\.github/ci/docs-requirements\.txt$'; then docs=true; fi
if has '^\.github/renovate\.json5?$'; then renovate=true; fi
if jq -e 'index("ci-full-render") != null' <<<"${PR_LABELS:-[]}" >/dev/null; then
  manifests=true full=true
fi
emit "$manifests" "$full" "$docs" "$renovate"
