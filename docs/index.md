# k8s-iannamo

Cluster Kubernetes bare-metal per homelab, gestito interamente via **GitOps**.

## Stack in breve

| Layer | Tecnologia |
|-------|-----------|
| OS | Talos Linux v1.11 |
| Orchestratore | Kubernetes v1.31 |
| GitOps | Flux CD v2 |
| Ingress | Traefik v3 + Gateway API |
| Load Balancer | MetalLB (L2) |
| Storage | NFS-CSI → ZFS su Proxmox |
| Certificati | cert-manager + Let's Encrypt (Cloudflare DNS-01) |
| Auth | Authentik (OIDC/SSO) |
| Monitoring | Prometheus + Grafana + Gatus |
| Alerting | Alertmanager + Flux Notifications → Telegram |
| Secret | SOPS + age |

## Nodi

| Ruolo | Hostname | IP |
|-------|---------|-----|
| Control Plane | talos-homelab-cp-1 | 192.168.178.50 |
| Worker | talos-homelab-worker-1 | 192.168.178.51 |
| Worker | talos-homelab-worker-2 | 192.168.178.52 |

Tutti i nodi girano come VM su Proxmox (host: `192.168.178.162`).

## Cosa fa

- **Deployment dichiarativo**: ogni modifica al branch `main` viene applicata automaticamente al cluster da Flux
- **Alta disponibilità servizi**: Traefik con 2 repliche, alerting proattivo
- **Notifiche Telegram**: alert Prometheus (metriche cluster) + alert Flux (errori GitOps)
- **SSO centralizzato**: tutti i servizi web protetti da Authentik OIDC
- **Backup dati su ZFS**: pool separati SSD (flash) e HDD (spacex) con policy Retain

## Cosa NON fa

- Non è un cluster multi-tenant / production-grade
- Non gestisce backup off-site (solo persistenza locale NFS/ZFS)
- Non ha auto-scaling (nodi fissi su Proxmox)
- Non espone servizi su internet senza tunnel/proxy (rete locale)
