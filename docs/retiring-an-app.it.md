# Dismettere un'app e i suoi dati

In questo cluster niente cancella i dati da solo. Ogni StorageClass ha
`reclaimPolicy: Retain`, e PVC, namespace con stato, cluster CNPG e CRD hanno
`kustomize.toolkit.fluxcd.io/prune: disabled` (vedi
[Storage](storage.md#data-protection)). Un errore in Git non può distruggere
dati. Il prezzo è che i dati restano per sempre, a meno che qualcuno non li
rimuova di proposito. Questa pagina serve a quello: dismettere un'app senza
lasciarsi dietro dati per anni, e senza cancellare niente per sbaglio.

## Ciclo di vita

| Passo | Cosa succede | Chi |
|---|---|---|
| 1. Parcheggio | I pod si fermano, i dati restano. Un database va in ibernazione. | pull request |
| 2. Rimozione da Git | L'app esce dal repository. Il suo namespace, il database e i PVC restano nel cluster. | pull request con la label `allow-destructive` |
| 3. Conservazione | Una voce in `.github/ci/retention.yaml` registra cosa si conserva e fino a quando. | la stessa pull request |
| 4. Archiviazione (facoltativa) | Una copia dei dati va nel dataset di backup. | a mano |
| 5. Cancellazione | Si rimuovono gli oggetti, i PV e infine le directory sull'host NFS. | a mano, solo dal proprietario |

I passi 1–3 passano da pull request, come qualsiasi altra modifica. I passi 4
e 5 non sono mai automatizzati, e i guardrail AI di questo repository si
rifiutano di eseguirli: si fanno a mano, dopo aver letto questa pagina.

## 1. Parcheggio

Prima di rimuovere un'app da Git, assicurati che la rimozione ne conservi i
dati:

- **Gli oggetti di Flux** (PVC, Namespace, `Cluster` CNPG presenti nel
  repository) hanno `kustomize.toolkit.fluxcd.io/prune: disabled`. La CI
  avvisa se manca.
- **Gli oggetti creati da un chart Helm** (un PVC da un template, un
  `Cluster` generato dal chart) hanno bisogno di
  `helm.sh/resource-policy: keep` nella release installata. Impostala con un
  value del chart, oppure con un post-renderer se il chart non ne ha uno.
  Controlla la release installata, non solo Git:
  `helm -n <ns> get manifest <release> | grep -B3 resource-policy`.
- **Ferma i workload**: scala i Deployment a 0 e metti in ibernazione i
  cluster CNPG (`cnpg.io/hibernation: "on"`), così niente scrive mentre l'app
  aspetta di essere cancellata.

Esempio: [#415](https://github.com/lucaam/k8s-iannamo/pull/415) ha
parcheggiato TeslaMate, e [#431](https://github.com/lucaam/k8s-iannamo/pull/431)
l'ha poi rimossa da Git con la sua voce di conservazione (passi 2 e 3).

## 2. Rimozione da Git e 3. conservazione

In una sola pull request:

1. Rimuovi la directory dell'app e la sua voce in `apps/kustomization.yaml`.
2. Aggiungi una voce a `.github/ci/retention.yaml`: cosa si conserva (oggetti
   e directory sull'host), da quando e fino a quando (`retainUntil`, di solito
   90 giorni).
3. Aggiungi la label `allow-destructive`. Il data guard della CI segnala la
   HelmRelease e il Namespace come rimossi da Git: la label conferma che i
   dati sono stati protetti al passo 1.

Dopo il merge, controlla che siano spariti solo gli oggetti senza stato:
`kubectl -n <ns> get cluster,pvc,secret`.

Ogni notte `ci-main` controlla il registro. Quando una voce supera
`retainUntil`, apre una issue con label `data-retention`. La issue si chiude
quando la voce viene rimossa o riceve una nuova data.

## 4. Archiviazione (facoltativa)

I dati di un database in ibernazione sono una copia coerente dei file del
database, perché CNPG ha fermato PostgreSQL in modo pulito. Per conservarli
basta un archivio tar delle directory:

```bash
ssh root@proxmox 'tar -C /export/flash -czf /path/to/backup/<app>-$(date +%F).tgz <namespace>'
```

Per un dump portabile, invece, riattiva il database (togli l'annotazione di
ibernazione con una pull request), poi esegui `pg_dump`:

```bash
kubectl -n <ns> exec <cluster>-1 -- pg_dump -Fc <database> > <app>.dump
```

## 5. Cancellazione

Esegui questi passi a mano, uno alla volta, leggendo l'output di ciascuno.
Non si possono annullare.

1. **Scopri cosa si può cancellare.** Esegui l'inventario in sola lettura:

    ```bash
    hack/inventory-released-pvs.py > /tmp/released-pvs.md
    ```

    Elenca ogni PV che non è Bound:

    - Un **alias** condivide la directory con un PV in uso. La maggior parte
      dei PV Released sono alias, perché un PVC ricreato con lo stesso nome
      corrisponde alla stessa directory `<namespace>/<pvc>`. **Non cancellare
      mai la directory di un alias.**
    - Un **orfano** ha una directory che nessun PV in uso utilizza. Viene
      mostrato con la sua dimensione e la data dell'ultima modifica.

2. **Oggetti Kubernetes.** CNPG cancella i PVC che possiede insieme al suo
   `Cluster`. Con `Retain`, i loro PV diventano Released e i file restano.

    ```bash
    kubectl -n <ns> delete cluster.postgresql.cnpg.io <cluster>
    kubectl -n <ns> delete pvc <pvc>...
    kubectl delete namespace <ns>
    ```

3. **Oggetti PV.** Cancellare un PV Released con `Retain` non tocca mai la sua
   directory, quindi è anche il modo per fare ordine tra gli alias:

    ```bash
    kubectl delete pv <pv>...
    ```

4. **Directory sull'host NFS.** È l'unico passo che libera spazio su disco.
   Cancella solo le directory che l'inventario segnala come **orfane**, ed
   esegui di nuovo l'inventario subito prima, nel caso un nuovo PVC abbia
   riusato il path:

    ```bash
    ssh root@proxmox 'rm -rf /export/<pool>/<namespace>/<pvc>'
    ```

    Gli eventuali snapshot ZFS del dataset trattengono i blocchi finché non
    scadono.

5. **Registro.** Rimuovi la voce da `.github/ci/retention.yaml` con una pull
   request.

## Conservare i dati più a lungo

Imposta un `retainUntil` più lontano in `.github/ci/retention.yaml`, con un
motivo. Conservare i dati è sempre consentito. Il registro serve solo a far sì
che sia una decisione e non una dimenticanza.
