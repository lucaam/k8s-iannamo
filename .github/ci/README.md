# CI

Everything Flux applies is rebuilt and validated before it reaches `main`,
without access to the cluster.

| Workflow | When | What |
|---|---|---|
| `ci.yaml` | every pull request | the gate: lint, Flux build, render of the changed HelmReleases, validation, data guard, diff comment |
| `ci-main.yaml` | after every merge, every night, on demand | build and render of **every** HelmRelease (external chart sources included), validation, Trivy scan to code scanning; a failure opens or updates the issue labelled `ci-main`, a success closes it. It also opens an issue labelled `data-retention` when retained data passes its date (`retention.yaml`, see `docs/retiring-an-app.en.md`) |
| `docs.yml` | merge that touches the docs | builds and publishes the MkDocs site |

The only required check besides GitGuardian is **`ci-gate`**, which fails if
any of `changes`, `lint`, `manifests` or `render` fails or is cancelled.
Renovate automerges only when `ci-gate` is green.

## Jobs of the pull request gate

- **lint**: `yamllint --strict` on the whole repository (`.yamllint.yml`),
  `actionlint`, `zizmor` on every workflow, GitHub's dependency review (new
  actions with known vulnerabilities), the fluxci unit tests, the Renovate
  config validator when `.github/renovate.json` changes, `mkdocs build
  --strict` when the docs change.
- **manifests**: `fluxci build` of every Flux Kustomization reachable from
  `clusters/production/flux-system/gotk-sync.yaml`, then `fluxci check`, then
  kubeconform and conftest on the result.
- **render**: `helm template` of the HelmReleases the pull request changes,
  for both the base and the head revision, then kubeconform and conftest on
  the head render, the data guard, and the diff that the `diff comment` job
  posts on the pull request (it is updated on every push).

Every HelmRelease is rendered (instead of only the changed ones) when the pull
request touches the CI itself, `policy/`, `mise.toml`, `clusters/` or
`infrastructure/crds/`, or carries the `ci-full-render` label.

## What fluxci checks

`fluxci.py` rebuilds what Flux would apply and renders what helm-controller
would install:

- **Substitution in strict mode.** postBuild variables are resolved as
  kustomize-controller does and substituted with `flux envsubst --strict`.
  Without strict mode, Flux silently replaces an undefined `${VAR}` with an
  empty string; here it fails. Escape with `$${VAR}` what the application
  expands at runtime.
- **Variables from encrypted Secrets.** CI cannot decrypt the SOPS Secrets
  used by `substituteFrom`, so `substitutions.yaml` holds one placeholder per
  key. The key names are readable in the encrypted files, so a key missing
  from `substitutions.yaml`, or a stale placeholder, fails CI.
- **Static checks** (`fluxci check`):
  - every `*.sops.yaml` is encrypted, matches a `.sops.yaml` rule and stores
    nothing in plaintext;
  - no plaintext Secret anywhere in the repository;
  - `dependsOn`, chart sources and `valuesFrom` point at objects that exist;
  - SOPS objects are only in Kustomizations with `decryption`;
  - no object is applied by two Kustomizations;
  - YAML files that no Kustomization applies are reported as warnings;
  - PVCs and CNPG clusters without `kustomize.toolkit.fluxcd.io/prune: disabled`
    are reported (`pruneProtection` in `config.yaml`).
- **Render like helm-controller.** Release name and namespace, `valuesFrom`
  ConfigMaps, CRD policy, post-renderers and origin labels are applied as
  helm-controller does. `--kube-version` and `--api-versions` come from
  `config.yaml` and `api-versions.txt`, so charts take the same branches as in
  the cluster.
- **Schemas.** kubeconform runs with `-strict` against JSON schemas generated
  from the CRDs found in the build, in the renders and in
  `clusters/production/flux-system/gotk-components.yaml`. Fields a CRD does
  not define are rejected (the API server would drop them). A pinned
  datreeio/CRDs-catalog commit is the fallback. A kind without any schema
  fails unless it is listed in `kubeconform.allowMissingSchemas`.
- **Policies** in `policy/`, evaluated by conftest on each object:
  - `deny`: images without a tag or digest, HelmRelease charts without an
    exact version;
  - `warn`: privileged containers, hostPath volumes, missing memory requests,
    namespaces without a Pod Security level, GitRepositories that follow a
    branch.

  Known violations go in `policy/exceptions.rego`, with a reason and the change
  that removes them.

## Data guard

The guard compares base and head, both for the objects Flux applies and for
the rendered charts. It fails when a pull request:

- removes a PVC, PV, Namespace, CNPG Cluster, StorageClass, HelmRelease, Flux
  Kustomization or StatefulSet with volume claim templates;
- changes the storage class, access modes or volume of a PVC, the volume claim
  templates of a StatefulSet, or the storage or bootstrap of a CNPG Cluster;
- enables `prune` on a Kustomization, changes its `path` or target namespace,
  or changes a release name or target namespace;
- moves a protected object to another Kustomization;
- removes `kustomize.toolkit.fluxcd.io/prune: disabled`;
- introduces a `Delete` reclaim policy.

If the change is intended, add the **`allow-destructive`** label to the pull
request: the guard then reports the findings as warnings.

## Configuration

| File | Purpose |
|---|---|
| `config.yaml` | bootstrap file, cluster version, kubeconform and render settings, prune protection level |
| `substitutions.yaml` | placeholders for the postBuild variables that come from SOPS Secrets |
| `api-versions.txt` | API versions and kinds served by the cluster, for `helm template --api-versions` |
| `../../mise.toml` | versions of every tool, for CI and local runs |
| `../../policy/` | conftest policies and their exceptions |

Refresh `api-versions.txt` after a Kubernetes upgrade or after adding CRDs,
with `.github/ci/refresh-api-versions.sh` (read-only: it queries the
discovery API of the current kubeconfig context), and commit the result.

## Run it locally

```bash
mise install                        # the tools at the versions CI uses
fx=.github/ci/fluxci                # wrapper around fluxci.py with its Python dependencies
out=$(mktemp -d)
$fx build --repo . --out "$out/build"
$fx check --build "$out/build"
$fx plan --head "$out/build" --full --out "$out/plan.json"
$fx render --build "$out/build" --plan "$out/plan.json" --out "$out/render"
$fx schemas --repo . --out "$out/schemas" "$out/build" "$out/render"
$fx bundle --build "$out/build" --render "$out/render" --out "$out/bundle"
$fx validate --repo . --bundle "$out/bundle" --schemas "$out/schemas"
```

To compare with `main`, build a worktree of `origin/main` with
`--config-repo .`, then run `plan --base`, `render --side base`, `guard` and
`diff` as in `.github/workflows/ci.yaml`.
