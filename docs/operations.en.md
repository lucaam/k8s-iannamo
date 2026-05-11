# Operations

## Daily commands

### Cluster status

```bash
# Flux status (all resources)
flux get all -A

# Failed HelmReleases
flux get helmreleases -A --status-selector ready=false

# Kustomization
flux get kustomizations

# Unhealthy pods
kubectl get pods -A --field-selector status.phase!=Running,status.phase!=Succeeded
```

### Force reconciliation

```bash
# Reconcile git source + all kustomizations
flux reconcile source git flux-system
flux reconcile kustomization infrastructure
flux reconcile kustomization apps

# Single HelmRelease
flux reconcile helmrelease <name> -n <namespace>
```

### Secret management

```bash
# Encrypt a new file
sops --encrypt --in-place apps/<app>/secret-<name>.sops.yaml

# Edit an existing secret (decrypts in-place, opens editor)
sops apps/<app>/secret-<name>.sops.yaml

# age key required
export SOPS_AGE_KEY_FILE=~/age.agekey
```

### Logs and debug

```bash
# Pod logs
kubectl logs -n <namespace> <pod> -f

# Describe resource
kubectl describe helmrelease -n <namespace> <name>

# Recent events
kubectl get events -A --sort-by='.lastTimestamp' | tail -30

# Flux controllers logs
flux logs --level=error
```
