# Kubernetes GitOps (Talos + Flux)

GitOps repository for a bare-metal Kubernetes cluster running **Talos Linux** on Proxmox, managed by **Flux CD**.

📖 **[Full Documentation](https://lucaam.github.io/k3s-gitops-iannamo/)**

## Quick Overview

| | |
|---|---|
| **OS** | Talos Linux v1.11 |
| **K8s** | v1.31 (3 nodes on Proxmox) |
| **GitOps** | Flux CD v2 + SOPS/age |
| **Ingress** | Traefik v3 + Gateway API |
| **LB** | MetalLB (L2) |
| **Storage** | NFS-CSI → ZFS (SSD + HDD) |
| **Auth** | Authentik SSO (OIDC) |
| **Monitoring** | Prometheus + Grafana + Gatus |
| **Alerting** | Telegram (Alertmanager + Flux + Gatus) |

## Repository Structure

```
clusters/production/   → Flux entry point (kustomization order)
infrastructure/        → Platform: MetalLB, Traefik, cert-manager, NFS-CSI
apps/                  → User apps: HA, Immich, Grafana, Prometheus, ...
docs/                  → Documentation (MkDocs → GitHub Pages)
```

## Apps Deployed

Authentik · Home Assistant · Immich · Grafana · Prometheus · Gatus · Kubernetes Dashboard · Mosquitto · Zigbee2MQTT · Trek · Tado API Proxy

## Setup

Requires:
- Talos cluster running
- `age.agekey` for SOPS decryption
- Flux bootstrap pointing to this repo

```bash
flux bootstrap github \
  --owner=lucaam \
  --repository=k3s-gitops-iannamo \
  --branch=main \
  --path=clusters/production
```
