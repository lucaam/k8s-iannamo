# k8s-iannamo

Repository GitOps di un cluster Kubernetes homelab. Tre macchine virtuali
Talos Linux girano su un unico host Proxmox VE, e Flux mantiene il cluster
allineato al branch `main` di
[lucaam/k8s-iannamo](https://github.com/lucaam/k8s-iannamo). Questo sito
spiega com'è fatto il cluster e come modificarlo in sicurezza.

## In breve

| Livello | Tecnologia |
|---|---|
| Hypervisor | Proxmox VE su un solo host, con i pool ZFS `flash` (SSD) e `spacex` (HDD) |
| Sistema operativo | Talos Linux v1.11 |
| Kubernetes | v1.31: un nodo di control plane e due worker |
| GitOps | Flux v2.8; segreti cifrati con SOPS e age |
| Load balancing | MetalLB in modalità layer 2 |
| Ingress | Traefik v3 con la Gateway API |
| Certificati | cert-manager: certificato wildcard Let's Encrypt, DNS-01 su Cloudflare |
| Identità | Authentik: OIDC e forward authentication |
| Storage | driver NFS CSI servito dall'host Proxmox, reclaim policy `Retain` |
| Database | CloudNativePG per Immich, un PostgreSQL incluso nel chart per Authentik |
| Monitoraggio | kube-prometheus-stack, Grafana, Gatus |
| Alert | Alertmanager, Gatus e Flux, tutti verso Telegram |
| Automazione | gate delle pull request su GitHub Actions (`ci-gate`), Renovate |

## Da dove partire

<div class="grid cards" markdown>

-   :material-sitemap-outline:{ .lg .middle } **[Architettura](architecture.md)**

    ---

    Proxmox, Talos, i tre nodi e come Flux applica questo repository.

-   :material-apps:{ .lg .middle } **[Applicazioni](apps.md)**

    ---

    Cosa gira nel cluster, dove si raggiunge e come gli utenti fanno login.

-   :material-server-network:{ .lg .middle } **[Piattaforma](infrastructure.md)**

    ---

    MetalLB, Traefik, cert-manager, storage, sicurezza e monitoraggio.

-   :material-source-pull:{ .lg .middle } **[Flusso delle modifiche](workflow.md)**

    ---

    Branch, pull request, gate della CI, merge, riconciliazione e Renovate.

</div>

## Regole di base

- **Git è la fonte di verità.** Flux applica ciò che sta su `main`, e `main`
  accetta solo squash merge di pull request con `ci-gate` verde. Una modifica
  fatta a mano si discosta da Git e viene sovrascritta dalla riconciliazione o
  dall'upgrade successivi. Vedi [Flusso delle modifiche](workflow.md).
- **I dati non vengono mai cancellati in automatico.** I volumi usano la
  reclaim policy `Retain`, i PVC e gli altri oggetti con stato sono protetti
  dal prune di Flux, e il data guard della CI ferma le pull request che li
  rimuovono. Le app si dismettono seguendo [un runbook](retiring-an-app.md),
  mai cancellandone la directory.
- **I segreti si committano solo cifrati.** I segreti sono file SOPS cifrati
  con age, e il nome di dominio è la variabile `${DOMAIN}`, che Flux
  sostituisce. Vedi [Sicurezza e segreti](security.md).

!!! danger "Non esistono backup"
    Tutto, dalla libreria di foto ai database alla configurazione di Home
    Assistant, esiste solo sui pool ZFS dell'unico host Proxmox. La ridondanza
    di ZFS copre un disco guasto e `Retain` copre un PVC cancellato; niente
    copre un `rm` sbagliato, un pool perso o un host perso. Vedi
    [Storage](storage.md#backups).

## Limiti

- **Nessuna alta disponibilità.** C'è un solo host fisico e un solo nodo di
  control plane, con un unico membro etcd: se uno dei due si ferma, si ferma
  il cluster.
- **Capacità fissa.** Tre VM di dimensione fissa, nessun autoscaling.
- **Un solo proprietario.** È un homelab, non un cluster multi-tenant o di
  produzione.
