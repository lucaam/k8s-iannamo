# Retiring an app and its data

Nothing in this cluster deletes data on its own. Every StorageClass has
`reclaimPolicy: Retain`, and PVCs, stateful namespaces, CNPG clusters and CRDs
carry `kustomize.toolkit.fluxcd.io/prune: disabled`. A mistake in Git cannot
destroy data. The price is that data stays forever unless someone removes it
on purpose. This page is that purpose: how to retire an app without leaving
data behind for years, and without deleting anything by accident.

## Lifecycle

| Step | What happens | Who |
|---|---|---|
| 1. Park | The pods stop, the data stays. A database is hibernated. | pull request |
| 2. Remove from Git | The app leaves the repository. Its namespace, database and PVCs stay in the cluster. | pull request with the `allow-destructive` label |
| 3. Retain | An entry in `.github/ci/retention.yaml` records what is kept and until when. | same pull request |
| 4. Archive (optional) | A copy of the data goes to the backup dataset. | by hand |
| 5. Delete | The objects, the PVs and finally the directories on the NFS host are removed. | by hand, by the owner only |

Steps 1–3 go through pull requests, like any other change. Steps 4 and 5 are
never automated, and the AI guardrails of this repository refuse them: they
are run by hand, after reading this page.

## 1. Park

Before an app is removed from Git, make sure that removing it keeps its data:

- **Flux objects** (PVCs, Namespace, CNPG `Cluster` in the repository) carry
  `kustomize.toolkit.fluxcd.io/prune: disabled`. CI warns about missing ones.
- **Objects created by a Helm chart** (a PVC from a template, a `Cluster` the
  chart renders) need `helm.sh/resource-policy: keep` in the deployed release.
  Set it through a chart value, or through a post-renderer when the chart has
  none. Check the deployed release, not only Git:
  `helm -n <ns> get manifest <release> | grep -B3 resource-policy`.
- **Stop the workloads**: scale the Deployments to 0 and hibernate CNPG
  clusters (`cnpg.io/hibernation: "on"`), so that nothing writes while the
  app waits for its deletion.

Example: #415 parked TeslaMate.

## 2. Remove from Git and 3. retain

In one pull request:

1. Remove the app directory and its entry in `apps/kustomization.yaml`.
2. Add an entry to `.github/ci/retention.yaml`: what is kept (objects and host
   directories), since when, and until when (`retainUntil`, usually 90 days).
3. Add the `allow-destructive` label. The CI data guard lists the HelmRelease
   and the Namespace as removed from Git: the label confirms that the data has
   been protected in step 1.

After the merge, check that only the stateless objects went away:
`kubectl -n <ns> get cluster,pvc,secret`.

Every night, `ci-main` checks the registry. When an entry passes `retainUntil`,
it opens an issue labelled `data-retention`. The issue goes away when the entry
is removed or gets a new date.

## 4. Archive (optional)

The data of a hibernated database is a consistent copy of the database files,
because CNPG stopped PostgreSQL cleanly. A tarball of the directories is
enough to keep it:

```bash
ssh root@proxmox 'tar -C /export/flash -czf /path/to/backup/<app>-$(date +%F).tgz <namespace>'
```

For a portable dump instead, wake the database (remove the hibernation
annotation through a pull request), then run `pg_dump`:

```bash
kubectl -n <ns> exec <cluster>-1 -- pg_dump -Fc <database> > <app>.dump
```

## 5. Delete

Run these by hand, one at a time, reading the output of each. They cannot be
undone.

1. **Find out what is safe to delete.** Run the read-only inventory:

   ```bash
   hack/inventory-released-pvs.py > /tmp/released-pvs.md
   ```

   It lists every PV that is not Bound:
   - An **alias** shares its directory with a live PV. Most Released PVs are
     aliases, because a PVC recreated with the same name maps to the same
     `<namespace>/<pvc>` directory. **Never delete an alias directory.**
   - An **orphan** has a directory that no live PV uses. It is shown with its
     size and last modification date.

2. **Kubernetes objects.** CNPG deletes the PVCs it owns together with its
   `Cluster`. With `Retain`, their PVs become Released and the files stay.

   ```bash
   kubectl -n <ns> delete cluster.postgresql.cnpg.io <cluster>
   kubectl -n <ns> delete pvc <pvc>...
   kubectl delete namespace <ns>
   ```

3. **PV objects.** Deleting a Released PV with `Retain` never touches its
   directory, so this is also how to tidy up aliases:

   ```bash
   kubectl delete pv <pv>...
   ```

4. **Directories on the NFS host.** This is the only step that frees disk
   space. Delete only directories that the inventory reports as **orphan**,
   and run the inventory again right before, in case a new PVC reused the
   path:

   ```bash
   ssh root@proxmox 'rm -rf /export/<pool>/<namespace>/<pvc>'
   ```

   ZFS snapshots of the dataset, if any, keep the blocks until they expire.

5. **Registry.** Remove the entry from `.github/ci/retention.yaml` with a pull
   request.

## Keeping data longer

Set a later `retainUntil` in `.github/ci/retention.yaml`, with a reason.
Keeping data is always allowed. The registry only makes sure that it is a
decision and not something forgotten.
