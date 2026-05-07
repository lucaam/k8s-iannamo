# Architettura

## Panoramica

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

    subgraph Apps["Applicazioni"]
        HA[Home Assistant]
        Immich[Immich]
        Grafana[Grafana]
        More[...]
    end

    Cluster -->|NFS-CSI| ZFS
    Flux[Flux CD] -->|GitOps| Cluster
    GitHub[GitHub Repo] -->|Pull| Flux
```

## Flusso GitOps

```mermaid
sequenceDiagram
    participant Dev as Developer
    participant GH as GitHub
    participant Flux as Flux CD
    participant K8s as Cluster

    Dev->>GH: git push (main)
    Flux->>GH: Poll ogni 1m
    Flux->>K8s: Apply Kustomization
    K8s-->>Flux: Status (Ready/Failed)
    alt Errore
        Flux->>Telegram: Alert errore
    end
```

## Struttura Repository

```
├── clusters/production/     # Entry point Flux: definisce le Kustomization
│   ├── secrets.yaml         # Kustomization per SOPS secrets
│   ├── infrastructure.yaml  # Kustomization per infrastruttura
│   └── apps.yaml            # Kustomization per applicazioni
├── infrastructure/          # Componenti di piattaforma
│   ├── crds/                # Gateway API CRDs
│   ├── metallb/             # Load balancer L2
│   ├── cert-manager/        # Certificati TLS wildcard
│   ├── nfs-csi/             # Storage driver NFS
│   ├── traefik/             # Ingress controller + Gateway
│   ├── kube-system/         # Patch sistema (metrics-server, ecc.)
│   └── notifications/       # Flux → Telegram alert
├── apps/                    # Applicazioni utente
│   ├── authentik/           # SSO / Identity Provider
│   ├── home-assistant/      # Domotica
│   ├── immich/              # Photo management
│   ├── grafana/             # Dashboard metriche
│   ├── prometheus/          # Monitoring stack
│   ├── gatus/               # Uptime monitoring
│   └── ...                  # Altre app
└── scripts/                 # Script di diagnostica
```

## Dipendenze tra Kustomization

```mermaid
graph LR
    secrets[secrets] --> infrastructure
    infrastructure --> apps
```

Flux applica le risorse nell'ordine: `secrets` → `infrastructure` → `apps`. Ogni livello dipende dal precedente tramite `dependsOn`.
