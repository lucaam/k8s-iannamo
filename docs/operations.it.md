# Operazioni quotidiane

Le modifiche durature passano da una pull request
([Flusso delle modifiche](workflow.md)). Questa pagina raccoglie i comandi per
osservare il cluster, le poche azioni dirette che fanno parte del lavoro
normale e i problemi più comuni.

## Accesso e strumenti

- Un kubeconfig e un talosconfig per il cluster; nessuno dei due è in questo
  repository.
- La chiave privata age, per leggere e modificare i file SOPS (vedi
  [Sicurezza e segreti](security.md#sops-and-age)).
- `mise install` installa kustomize, helm, flux, kubeconform, conftest, yq e i
  linter alle versioni usate dalla CI (`mise.toml`). kubectl, talosctl e sops
  si installano a parte, a versioni compatibili con il cluster.

## Stato del cluster

Questi comandi leggono soltanto:

```bash
flux get kustomizations                                  # secrets, infrastructure, apps
flux get helmreleases -A --status-selector ready=false   # release non pronte
flux get sources all -A                                  # sorgenti Git, Helm e OCI
flux events -A                                           # eventi recenti degli oggetti Flux
flux logs -A --level=error --since=1h                    # errori dei controller
kubectl get pods -A --field-selector=status.phase!=Running,status.phase!=Succeeded
kubectl get events -A --sort-by=.lastTimestamp | tail -30
```

Le pagine di stato sono Gatus su `gatus.${DOMAIN}` e Grafana su
`grafana.${DOMAIN}`; gli alert di Prometheus sono su
`prometheus.${DOMAIN}/alerts`.

## Riconciliare

Riconciliare chiede a Flux di agire subito invece che al prossimo intervallo.
Modifica il cluster solo applicando ciò che è già su `main`.

```bash
flux reconcile source git flux-system                     # legge main subito
flux reconcile kustomization infrastructure               # applica subito una Kustomization
flux reconcile kustomization apps --with-source           # legge, poi applica
flux reconcile helmrelease <nome> -n <namespace>          # aggiorna subito una release
flux reconcile helmrelease <nome> -n <namespace> --reset  # azzera i tentativi di una release in stallo
```

Una HelmRelease il cui upgrade è fallito più volte di quante ne preveda la sua
remediation è `Stalled` e aspetta una modifica. Dopo il merge della
correzione, `--reset` permette a helm-controller di riprovare; `--force`
esegue una sola installazione o un solo upgrade forzati.

## Risoluzione dei problemi

| Sintomo | Dove guardare |
|---|---|
| Una Kustomization non è pronta | `kubectl -n flux-system describe kustomization <nome>`: il messaggio indica l'oggetto che ha fallito. Un solo oggetto non valido blocca l'intera Kustomization. |
| `apps` segnala che una dipendenza non è pronta | `infrastructure` non ha ancora applicato l'ultima revisione: riconciliala, oppure correggila prima. |
| Una HelmRelease non è pronta | `kubectl -n <namespace> describe helmrelease <nome>`, `helm -n <namespace> history <release>`, poi i pod della release. |
| Un upgrade va in timeout anche se il pod parte | L'app si avvia lentamente: aumenta `spec.timeout` nella HelmRelease, come per Home Assistant. |
| Un pod è in `CrashLoopBackOff` | `kubectl -n <namespace> logs <pod> --previous` e `kubectl -n <namespace> describe pod <pod>`. |
| Un PVC è `Pending` | Normale finché nessun pod lo usa (`WaitForFirstConsumer`). Altrimenti `kubectl -n <namespace> describe pvc <nome>`: nome della classe, server NFS raggiungibile. |
| Un hostname risponde 404 | Nessuna route corrisponde: `kubectl -n <namespace> describe httproute <nome>`, controlla l'hostname e le condizioni `Accepted` e `ResolvedRefs`. |
| Un'app in forward auth va in loop sul login o risponde 500 | Il middleware deve esistere nel namespace della route, e deve esistere la route `authentik-outpost`; poi `kubectl -n authentik logs deploy/authentik-server`, che ospita l'outpost integrato. |
| Il certificato non si rinnova | `kubectl -n traefik describe certificate wildcard-apps`, `kubectl -n cert-manager logs deploy/cert-manager`, il token di Cloudflare. |
| kube-controller-manager o kube-scheduler si riavviano spesso | Scritture lente di etcd sul pool `flash`: cerca chi scrive molto sui volumi `nfs-flash` e sui dischi delle VM. |
| Flux non vede i nuovi commit | `flux get sources git -A`, `flux logs --kind GitRepository --level error`, le credenziali in `flux-system/flux-system`. |
| Non arriva nessun messaggio su Telegram | `kubectl -n gatus logs deploy/gatus`, `kubectl -n prometheus logs statefulset/alertmanager-kube-prometheus-stack-alertmanager`, `flux events --for Alert/flux-failures -n flux-system`. |

## Nodi

### Svuotare un nodo

```bash
kubectl drain <nodo> --ignore-daemonsets --delete-emptydir-data
# manutenzione
kubectl uncordon <nodo>
```

- Alcuni pod sono vincolati e non possono spostarsi: il server di Immich su
  `talos-homelab-worker-1` (iGPU), Zigbee2MQTT su `talos-homelab-worker-2`
  (adattatore USB). Quelle app restano ferme finché il nodo non torna.
- I PodDisruptionBudget dei primary di CloudNativePG non ammettono
  interruzioni, quindi il drain del nodo che ospita un primary si blocca.
  Prima pianifica la manutenzione con CloudNativePG (uno switchover, oppure la
  sua finestra di manutenzione dei nodi).
- C'è un solo nodo di control plane: svuotarlo o riavviarlo ferma l'API di
  Kubernetes finché non torna.

### Upgrade di Talos e Kubernetes

Talos, Kubernetes e le VM si gestiscono dal repository Terraform, non da
questo.

- Un upgrade di Talos usa l'installer dell'Image Factory con lo schematic del
  nodo: `talos-homelab-worker-1` ha le estensioni `i915` e `intel-ucode`, che
  l'immagine semplice `ghcr.io/siderolabs/installer` perderebbe.
- Prima fai uno snapshot di etcd (`talosctl etcd snapshot <file>`), poi
  aggiorna i worker prima del control plane, un nodo alla volta.
- Kubernetes sale di una versione minor alla volta. Dopo un upgrade, aggiorna
  `.github/ci/api-versions.txt` e `kubernetesVersion` in
  `.github/ci/config.yaml` (vedi il
  [README della CI](https://github.com/lucaam/k8s-iannamo/blob/main/.github/ci/README.md#configuration)).

## Dati

- Non si cancella mai niente per fare ordine: PV `Released`, directory NFS e
  volumi di database possono contenere l'unica copia di qualche dato. Vedi
  [Storage](storage.md#volume-directories).
- `hack/inventory-released-pvs.py` produce un inventario in sola lettura dei
  PV che non sono `Bound`.
- Rimuovere un'app e, molto più tardi, i suoi dati segue
  [Dismettere un'app](retiring-an-app.md).
