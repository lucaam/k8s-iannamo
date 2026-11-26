# Infrastruttura

I componenti di piattaforma stanno in
[`infrastructure/`](https://github.com/lucaam/k8s-iannamo/tree/main/infrastructure)
e li applica la Kustomization `infrastructure`, che `apps` aspetta. Al suo
interno non c'è un ordine: Flux applica come un unico insieme tutte le
directory elencate in `infrastructure/kustomization.yaml`, e i controller
convergono. Le versioni dei chart sono fissate in ogni `helm-release.yaml`.

| Directory | Cosa fornisce | Dove gira | Sorgente |
|---|---|---|---|
| `crds/` | CRD della Gateway API, operatore e CRD di CloudNativePG | `cnpg-system` | manifest delle release upstream |
| `priority-classes/` | `homelab-critical`, `homelab-standard`, `homelab-low` | tutto il cluster | manifest |
| `metallb/` | load balancer, pool di indirizzi, annuncio L2 | `metallb` | chart `metallb` |
| `cert-manager/` | ClusterIssuer e certificato wildcard | `cert-manager` | chart `cert-manager` |
| `nfs-csi/` | driver NFS CSI, StorageClass `nfs-flash` e `nfs-spacex` | `kube-system` | chart `csi-driver-nfs` |
| `traefik/` | Traefik, Gateway, TLS store, middleware, pagine di errore | `traefik` | chart `traefik` |
| `kube-system/` | configurazione di CoreDNS | `kube-system` | manifest |
| `notifications/` | alert di Flux verso Telegram | `flux-system` | manifest |
| `secrets/` | Secret per la sostituzione postBuild | `flux-system` | applicati dalla Kustomization `secrets` |

## CRD e CloudNativePG { #crds }

- **Gateway API**: le CRD del canale standard della release v1.5.1
  (GatewayClass, Gateway, HTTPRoute, GRPCRoute, ReferenceGrant,
  BackendTLSPolicy, ListenerSet, TLSRoute) e la ValidatingAdmissionPolicy che
  blocca gli upgrade non sicuri delle CRD, prese dagli URL delle release
  upstream.
- **CloudNativePG**: il manifest di release `cnpg-1.28.0.yaml`, che contiene
  sia le CRD sia il Deployment dell'operatore in `cnpg-system`.
- Entrambi sono fissati negli URL; Renovate propone le nuove release tramite
  manager regex.
- Una patch kustomize annota ogni CRD con
  `kustomize.toolkit.fluxcd.io/prune: disabled`: cancellare una CRD cancella
  tutti gli oggetti di quel tipo, per esempio i cluster CloudNativePG con i
  loro PVC, oppure tutte le HTTPRoute.
- Le CRD di Traefik e del Prometheus Operator non sono qui. Entrambe le
  HelmRelease usano `crds: Skip`, quindi quelle CRD non vengono aggiornate con
  i chart e restano alla versione di un'installazione precedente.
- Il namespace `crd-ns` viene creato qui e resta vuoto.

## PriorityClass { #priority-classes }

| Classe | Valore | Uso |
|---|---|---|
| `homelab-critical` | 100000 | domotica, ingress, identità e i loro database |
| `homelab-standard` | 10000, default globale | ogni pod che non indica una classe |
| `homelab-low` | 1000, non fa mai preemption | batch e strumenti |

- `homelab-critical`: Traefik, Home Assistant, Matter Server, Zigbee2MQTT,
  Mosquitto, Authentik (server, worker e PostgreSQL), l'operatore
  CloudNativePG e il database di Immich.
- `homelab-low`: code-server, il machine learning di Immich e
  social-media-reuploader.
- Il default globale si applica quando un pod viene creato, quindi i pod più
  vecchi restano a priorità 0 finché non vengono ricreati.
- `value` e `preemptionPolicy` non si possono modificare sul posto: cambiarli
  significa ricreare la classe.

Le risorse seguono una convenzione, applicata un'app alla volta: request di
CPU e memoria in base all'uso osservato, un limite di memoria con margine
sopra il picco osservato e nessun limite di CPU, perché il throttling della
CPU faceva fallire le probe durante i picchi.

## MetalLB { #metallb }

- Modalità layer 2: il pool `external-pool`
  (`192.168.178.10`–`192.168.178.40`) è annunciato su `eth0`, il nome
  dell'interfaccia nelle immagini Talos usate qui.
- I Service scelgono un indirizzo fisso con l'annotazione
  `metallb.io/loadBalancerIPs`; gli indirizzi in uso sono elencati in
  [Rete](networking.md#lan-addresses).
- Il namespace è `privileged`: gli speaker hanno bisogno della rete
  dell'host. Il chart esegue anche FRR-K8s, come da default, che la modalità
  layer 2 non usa.

## cert-manager { #cert-manager }

- Il chart installa le proprie CRD (`installCRDs: true`).
- Il ClusterIssuer `cloudflare` usa Let's Encrypt in produzione con challenge
  ACME DNS-01, tramite un token API di Cloudflare salvato in
  `secret-cloudflare.sops.yaml`.
- Il Certificate `wildcard-apps`, nel namespace `traefik`, copre
  `*.${DOMAIN}` e `*.home.${DOMAIN}` ed è salvato nel Secret
  `wildcard-apps-tls`, usato dal Gateway e dal TLS store di default di
  Traefik.
- cert-manager lo rinnova 30 giorni prima della scadenza. Gli alert
  `CertManagerCert*` e i controlli di Gatus scattano quando mancano meno di 14
  giorni, cioè quando il rinnovo è bloccato.

## Driver NFS CSI { #nfs-csi }

- Chart `csi-driver-nfs` in `kube-system`: due repliche del controller e un
  node plugin su ogni nodo, control plane compreso. Il chart arriva dalla
  directory `charts` del branch `master` upstream, che pubblica le versioni
  rilasciate.
- Le StorageClass sono definite in questa directory, non dal chart. Vedi
  [Storage](storage.md).
- Il namespace `nfs-csi` viene creato qui e resta vuoto.

## Traefik { #traefik }

- Due repliche, dietro l'IP LoadBalancer `192.168.178.10`.
- Provider: la Gateway API, per le HTTPRoute delle app, e le CRD di Traefik,
  per i middleware e la IngressRoute della dashboard. Il provider Ingress di
  Kubernetes è spento.
- Entry point `web` (80) e `websecure` (443): ogni richiesta HTTP viene
  reindirizzata su HTTPS, e `websecure` serve le pagine di errore
  personalizzate del middleware `traefik-errors`.
- La dashboard è pubblicata su `traefik.${DOMAIN}`, dietro la forward auth di
  Authentik.

Routing, middleware e pagine di errore sono descritti in
[Rete](networking.md#traefik-and-the-gateway-api).

## Configurazione di CoreDNS { #coredns }

`kube-system/coredns-config.yaml` sostituisce la ConfigMap `coredns` che
installa Talos. Vedi [Rete](networking.md#dns).

!!! warning "Non cancellare questo file"
    La ConfigMap non ha la protezione dal prune: rimuovere il file cancella la
    ConfigMap, e CoreDNS fallisce al riavvio successivo, portandosi dietro il
    DNS del cluster.

## Notifiche di Flux { #notifications }

- L'Alert `flux-failures` manda al Provider `telegram` gli eventi `error` di
  ogni GitRepository, Kustomization, HelmRepository e HelmRelease, tranne i
  messaggi di routine `waiting.*retrying` e `Health check passed`.
- Tutto lo `spec` del Provider è cifrato con SOPS.
- Il Secret `telegram-bot` contiene solo `${TELEGRAM_BOT_TOKEN}`, che Flux
  riempie con il valore di `telegram-credentials`.

Vedi [Monitoraggio](monitoring.md#flux-notifications).

## Secret per la sostituzione { #substitution-secrets }

La Kustomization `secrets` applica `infrastructure/secrets/` con
`prune: false`, quindi Flux non cancella mai questi Secret:

| Secret | Chiavi | A cosa servono |
|---|---|---|
| `cluster-vars` | `DOMAIN` | ogni hostname e URL nei manifest |
| `telegram-credentials` | `TELEGRAM_BOT_TOKEN` | il provider Telegram di Flux |

Vedi [Sicurezza e segreti](security.md#postbuild-variables).
