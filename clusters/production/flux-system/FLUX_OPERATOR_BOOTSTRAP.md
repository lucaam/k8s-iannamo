Scopo
-----
Questi file aggiungono l'installazione GitOps del Flux Operator al cluster
seguendo le best practice (installazione tramite `OCIRepository` + `HelmRelease`).

Flusso consigliato
------------------
1) Commit/push solo i file di bootstrap dell'operator (OCIRepository + HelmRelease):

   git add clusters/production/flux-system/flux-operator-oci-repo.yaml \
     clusters/production/flux-system/flux-operator-helmrelease.yaml
   git commit -m "bootstrap(flux-operator): add OCIRepository + HelmRelease"
   git push origin main

   - Questo lascia all'attuale installazione di Flux (bootstrap) il compito di
     risolvere e installare il chart dell'operator tramite `helm-controller`.

2) Attendi che il `HelmRelease` abbia installato il Flux Operator e le CRD.
   Controlli utili (opzionali):

   kubectl -n flux-system get helmrelease flux-operator
   kubectl -n flux-system get pods -l app.kubernetes.io/name=flux-operator
   kubectl get crd fluxinstances.fluxcd.controlplane.io

   - Quando il pod dell'operator è `Ready` e le CRD esistono, sei pronto al passo successivo.

3) Commit/push il `FluxInstance` (file `fluxinstance.yaml`) in una seconda PR/commit:

   git add clusters/production/flux-system/fluxinstance.yaml
   git commit -m "flux: add FluxInstance to be managed by flux-operator"
   git push origin main

   - Il Flux Operator rileverà il `FluxInstance` e provvederà a installare/configurare
     i controller definiti in `spec` (source, kustomize, helm, notification, ...).

4) Migrazione del bootstrap legacy (opzionale, manuale)
   - Quando l'operator ha preso il controllo e tutto è stabile, puoi rimuovere i
     manifest generati da `flux bootstrap` (es. `gotk-components.yaml`, `gotk-sync.yaml`).
   - Fai un backup prima di cancellare e procedi con attenzione.

Alternative locali
------------------
- Se preferisci generare/applicare localmente, puoi installare l'operator con Helm:

  helm install flux-operator oci://ghcr.io/controlplaneio-fluxcd/charts/flux-operator \
    --namespace flux-system --create-namespace

  Oppure usa il CLI `flux-operator` se lo hai installato per esportare i manifest
  localmente nella cartella `clusters/production/flux-system/` e poi committa i file
  risultanti (approccio meno "pure GitOps" ma pratico per bootstrap iniziale).

Note e raccomandazioni
----------------------
- Non applicare il `FluxInstance` nello stesso commit del chart/operator: la CR non
  esisterebbe ancora e l'applicazione fallirebbe.
- Valuta l'uso di `spec.sync.provider: github` con GitHub App per autenticazione
  sicura (richiede la creazione del Secret con i campi `githubAppID` e `githubAppPrivateKey`).
- Se vuoi passare a OCI (gitless) in futuro, puoi aggiornare `fluxinstance.yaml` per usare
  `sync.kind: OCIRepository` e un artefact taggato.

File aggiunti
------------
- flux-operator-oci-repo.yaml  (OCIRepository)
- flux-operator-helmrelease.yaml (HelmRelease)
- fluxinstance.yaml (FluxInstance, applicare dopo operator ready)
