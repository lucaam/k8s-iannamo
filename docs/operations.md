# Operazioni

## Comandi quotidiani

### Stato del cluster

```bash
# Stato Flux (tutte le risorse)
flux get all -A

# HelmRelease fallite
flux get helmreleases -A --status-selector ready=false

# Kustomization
flux get kustomizations

# Pod non healthy
kubectl get pods -A --field-selector status.phase!=Running,status.phase!=Succeeded
```

### Forzare riconciliazione

```bash
# Riconcilia sorgente git + tutte le kustomization
flux reconcile source git flux-system
flux reconcile kustomization infrastructure
flux reconcile kustomization apps

# Singola HelmRelease
flux reconcile helmrelease <nome> -n <namespace>
```

### Gestione secret

```bash
# Cifrare un nuovo file
sops --encrypt --in-place apps/<app>/secret-<nome>.sops.yaml

# Editare un secret esistente (decifra in-place, apre editor)
sops apps/<app>/secret-<nome>.sops.yaml

# Chiave age necessaria
export SOPS_AGE_KEY_FILE=~/age.agekey
```

### Logs e debug

```bash
# Log di un pod
kubectl logs -n <namespace> <pod> -f

# Describe risorsa
kubectl describe helmrelease -n <namespace> <nome>

# Eventi recenti
kubectl get events -A --sort-by='.lastTimestamp' | tail -30

# Log Flux controllers
flux logs --level=error
```

## Deploy di una nuova app

1. Creare la cartella `apps/<nome-app>/`
2. Aggiungere i file standard (namespace, helm-repo, helm-release, httproute, kustomization)
3. Se servono secret: creare `secret-*.sops.yaml` e cifrare con `sops --encrypt --in-place`
4. Aggiungere la cartella in `apps/kustomization.yaml`
5. Commit e push → Flux applica automaticamente

## Aggiornamento chart

1. Modificare `spec.chart.spec.version` nel `helm-release.yaml`
2. Commit e push
3. Flux aggiorna automaticamente (con remediation in caso di errore)

## Rollback

```bash
# Sospendere riconciliazione
flux suspend helmrelease <nome> -n <namespace>

# Rollback Helm manuale
helm rollback <release> <revision> -n <namespace>

# Ripristinare riconciliazione (dopo fix in git)
flux resume helmrelease <nome> -n <namespace>
```

## Manutenzione nodi

### Drain di un nodo

```bash
kubectl drain <nodo> --ignore-daemonsets --delete-emptydir-data
# ... manutenzione ...
kubectl uncordon <nodo>
```

### Upgrade Talos

```bash
talosctl upgrade --nodes <ip> --image ghcr.io/siderolabs/installer:<version>
```

## Troubleshooting

| Sintomo | Cosa controllare |
|---------|-----------------|
| HelmRelease stuck | `kubectl describe hr -n <ns> <nome>` → Events |
| Pod CrashLoop | `kubectl logs -n <ns> <pod> --previous` |
| PVC Pending | `kubectl describe pvc -n <ns> <nome>` → Events (NFS raggiungibile?) |
| Cert non rinnovato | `kubectl describe certificate -n cert-manager` + cert-manager logs |
| Alert spam | Verificare regole in Alertmanager config, soglie PrometheusRule |
| Flux non sincronizza | `flux logs --level=error` + check git credentials |
