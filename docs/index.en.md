# k8s-iannamo

Bare-metal Kubernetes cluster for homelab, fully managed via **GitOps**.

## Stack overview

| Layer | Technology |
|-------|------------|
| OS | Talos Linux v1.11 |
| Orchestrator | Kubernetes v1.31 |
| GitOps | Flux CD v2 |
| Ingress | Traefik v3 + Gateway API |
| Load Balancer | MetalLB (L2) |
| Storage | NFS-CSI → ZFS on Proxmox |
| Certificates | cert-manager + Let's Encrypt (Cloudflare DNS-01) |
| Auth | Authentik (OIDC/SSO) |
| Monitoring | Prometheus + Grafana + Gatus |
| Alerting | Alertmanager + Flux Notifications → Telegram |
| Secret | SOPS + age |

## Nodes

| Role | Hostname | IP |
|------|----------|-----|
| Control Plane | talos-homelab-cp-1 | 192.168.178.50 |
| Worker | talos-homelab-worker-1 | 192.168.178.51 |
| Worker | talos-homelab-worker-2 | 192.168.178.52 |

All nodes run as VMs on Proxmox (host: `192.168.178.162`).

## Features

- **Declarative deployment**: every change to the `main` branch is automatically applied to the cluster by Flux
- **Service high availability**: Traefik with 2 replicas, proactive alerting
- **Telegram notifications**: Prometheus alerts (cluster metrics) + Flux alerts (GitOps errors)
- **Centralized SSO**: all web services protected by Authentik OIDC
- **ZFS data backup**: separate SSD (flash) and HDD (spacex) pools with Retain policy

## What it does NOT do

- Not a multi-tenant / production-grade cluster
- No off-site backup management (only local NFS/ZFS persistence)
- No auto-scaling (fixed nodes on Proxmox)
- No public internet exposure without tunnel/proxy (local network only)
