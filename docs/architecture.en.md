# Architecture

## Overview

```mermaid
graph TB
    subgraph Proxmox["Proxmox Host (192.168.178.162)"]
        subgraph Cluster["Kubernetes Cluster"]
            CP[Control Plane<br/>192.168.178.50]
            W1[Worker 1<br/>192.168.178.51]
            W2[Worker 2<br/>192.168.178.52]
        end
        ZFS[(ZFS Pools<br/>flash / spacex)]
    end

    Internet((Internet)) -->|Cloudflare DNS| Traefik
    Traefik -->|Gateway API| Apps

    subgraph Apps["Applications"]
        HA[Home Assistant]
        Immich[Immich]
        Grafana[Grafana]
        More[...]
    end

    Cluster -->|NFS-CSI| ZFS
    Flux[Flux CD] -->|GitOps| Cluster
    GitHub[GitHub Repo] -->|Pull| Flux
```

## GitOps Flow

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub
    participant Flux as Flux CD
    participant K8s as Cluster

    Dev->>GH: git push (main)
    Flux->>GH: Poll every 1m
    Flux->>K8s: Apply Kustomization
    K8s-->>Flux: Status (Ready/Failed)
    alt Error
        Flux->>Telegram: Error alert
    end
```

## Repository Structure

```
├── clusters/production/     # Flux entry point: defines Kustomizations
│   ├── secrets.yaml         # Kustomization for SOPS secrets
│   ├── infrastructure.yaml  # Kustomization for infrastructure
│   └── apps.yaml            # Kustomization for applications
├── infrastructure/          # Platform components
│   ├── crds/                # Gateway API CRDs
│   ├── metallb/             # L2 load balancer
│   ├── cert-manager/        # TLS wildcard certificates
│   ├── nfs-csi/             # NFS storage driver
│   ├── traefik/             # Ingress controller + Gateway
│   ├── kube-system/         # System patch (metrics-server, etc.)
│   └── notifications/       # Flux → Telegram alert
├── apps/                    # User applications
│   ├── authentik/           # SSO / Identity Provider
│   ├── home-assistant/      # Home automation
│   ├── immich/              # Photo management
│   ├── grafana/             # Metrics dashboard
│   ├── prometheus/          # Monitoring stack
│   ├── gatus/               # Uptime monitoring
│   └── ...                  # Other apps
└── scripts/                 # Diagnostic scripts
```

## Kustomization Dependencies

```mermaid
graph LR
    secrets[secrets] --> infrastructure
    infrastructure --> apps
```

Flux applies resources in order: `secrets` → `infrastructure` → `apps`. Each level depends on the previous one via `dependsOn`.
