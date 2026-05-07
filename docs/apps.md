# Applicazioni

Tutte le app sono deployate come HelmRelease gestite da Flux, nel folder `apps/`.

## Elenco Servizi

| App | Namespace | Chart | Descrizione |
|-----|-----------|-------|-------------|
| **Authentik** | `authentik` | authentik/authentik | Identity Provider SSO (OIDC, SAML, LDAP) |
| **Home Assistant** | `home-assistant` | home-assistant | Automazione domotica |
| **Home Assistant Matter** | `home-assistant` | matter-server | Bridge protocollo Matter/Thread |
| **Immich** | `immich` | immich | Gestione foto/video self-hosted (alternativa Google Photos) |
| **Grafana** | `grafana` | grafana | Dashboard metriche e visualizzazione |
| **Prometheus** | `prometheus` | kube-prometheus-stack | Raccolta metriche + Alertmanager |
| **Gatus** | `gatus` | gatus | Uptime monitoring con status page |
| **Kubernetes Dashboard** | `kubernetes-dashboard` | kubernetes-dashboard | UI web per gestione cluster |
| **Mosquitto** | `mosquitto` | mosquitto | Broker MQTT per IoT |
| **Zigbee2MQTT** | `zigbee2mqtt` | zigbee2mqtt | Bridge Zigbee → MQTT |
| **Tado API Proxy** | `tado-api-proxy` | custom | Proxy per termostati Tado |
| **Trek** | `trek` | trek | Pianificatore viaggi |
| **External Services** | `external-services` | — | Proxy per servizi esterni alla rete |

## Pattern comune

Ogni app segue questa struttura:

```
apps/<nome-app>/
├── namespace.yaml           # Namespace dedicato
├── helm-repository.yaml     # Fonte del chart Helm
├── helm-release.yaml        # Configurazione deployment
├── httproute.yaml           # Routing HTTP (Gateway API)
├── kustomization.yaml       # Lista risorse Kustomize
├── pvc.yaml                 # (opzionale) PersistentVolumeClaim
├── secret-*.sops.yaml       # (opzionale) Secret cifrati con SOPS
└── middleware*.yaml          # (opzionale) Middleware Traefik/Authentik
```

## Accesso ai servizi

Tutti i servizi esposti usano **Gateway API HTTPRoute** con hostname `<servizio>.${DOMAIN}`:

- `auth.${DOMAIN}` → Authentik
- `ha.${DOMAIN}` → Home Assistant
- `photos.${DOMAIN}` → Immich
- `grafana.${DOMAIN}` → Grafana
- `status.${DOMAIN}` → Gatus
- `kubernetes.${DOMAIN}` → Kubernetes Dashboard

!!! note "Autenticazione"
    I servizi senza auth nativa sono protetti dal middleware forward-auth di Authentik su Traefik.
