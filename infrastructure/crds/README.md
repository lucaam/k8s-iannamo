**CRDs & operator namespaces**

- **Path layout**: each operator/CRD group lives under `infrastructure/crds/<operator>/` (e.g. `cnpg/`, `gateway-api/`).
- The root `infrastructure/crds/kustomization.yaml` aggregates per-operator `namespace.yaml` and `crds.yaml`/remote manifests.

- **Namespace convention**: operator/controller namespaces follow the `<name>-system` convention (e.g. `cnpg-system`). This repo uses per-operator namespaces (not a single generic `crd-ns`).

- **Why**: CRDs themselves are cluster-scoped; operator deployments and webhook services are namespace-scoped and are best isolated per-operator. Using `<name>-system` matches common upstream conventions and avoids accidental collisions.

- **Tests & repository rules**:
  - `tests/test_pr_manifests.py` expects every local YAML resource to be referenced by a `kustomization.yaml`. Keep per-operator `namespace.yaml` under the operator folder and list it from the root `infrastructure/crds/kustomization.yaml`.
  - SOPS secrets must follow rules defined in `.sops.yaml` (creation_rules) and use the `encrypted_regex` patterns.

- **How to run repo checks**:

```bash
# run manifest tests
pytest -q --disable-warnings --maxfail=1

# quick YAML parse sweep (PyYAML)
python3 - <<'PY'
import yaml
from pathlib import Path
errs=[]
for fp in Path('.').rglob('**/*.yaml'):
    try:
        with open(fp) as fh:
            list(yaml.safe_load_all(fh))
    except Exception as e:
        print('PARSE ERROR',fp,e)
        errs.append(fp)
print('Done, errors:', len(errs))
PY
```

If you prefer a different namespace naming scheme (for example `cnpg` instead of `cnpg-system`), update the per-operator `namespace.yaml` and any code that references the namespace FQDNs. Prefer keeping per-operator namespace files inside their folder so tests keep passing.
