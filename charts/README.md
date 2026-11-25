# Vendored Helm charts

Charts in this directory are served to Flux by the `flux-system` GitRepository
(this repository), for HelmReleases whose upstream source is no longer available.
Files are kept byte-for-byte as published upstream, so the original `LICENSE` and
`NOTICE` files stay inside each chart. Do not edit them in place: a change to a
vendored chart must bump `version` in its `Chart.yaml`, otherwise Flux keeps the
previously packaged artifact (`reconcileStrategy: ChartVersion`).

| Chart | Version | Upstream (no longer reachable) | License | Archive sha256 |
|---|---|---|---|---|
| `teslamate` | 0.3.0 | `oci://ghcr.io/dcardozoo/charts/teslamate` (returns 401 since 2026-11-11), sources at `https://github.com/dcardozoo/teslamate-helm-chart` (removed) | Apache-2.0 | `79036abb59f7dda75312e28bf9d47ef98557d4dd23e8443f378c88ed8b322484` |

## How the teslamate chart was recovered

The chart archive was copied from the source-controller artifact cache
(`helmchart/flux-system/teslamate-teslamate/teslamate-0.3.0.tgz`). Its sha256
matches the digest recorded in the `HelmChart` status, and it was extracted here
without changes. Rendering it with the values in `apps/teslamate/helm-release.yaml`
gives the same manifests as the release running in the cluster; the only
differences are the `${DOMAIN}` values that Flux substitutes at build time.
