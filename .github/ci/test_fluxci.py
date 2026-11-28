"""Unit tests for fluxci.py: run with
uv run --no-project --with pyyaml==6.0.3 python -m unittest discover -s .github/ci -p 'test_*.py'
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import fluxci  # noqa: E402


def doc(kind, name, namespace="", group="", **fields):
    api = f"{group}/v1" if group else "v1"
    d = {"apiVersion": api, "kind": kind, "metadata": {"name": name}}
    if namespace:
        d["metadata"]["namespace"] = namespace
    d.update(fields)
    return d


class YamlTest(unittest.TestCase):
    def test_equal_sign_and_dates_stay_strings(self):
        docs = fluxci.load_docs("a: =\nb: 2026-11-26\n")
        self.assertEqual(docs, [{"a": "=", "b": "2026-11-26"}])

    def test_multiline_roundtrip(self):
        d = {"data": {"conf": "line1\nline2 $${KEEP}\n"}}
        self.assertEqual(fluxci.load_docs(fluxci.dump_docs([d])), [d])


class SecretsTest(unittest.TestCase):
    def test_substitution_refs_are_not_plaintext(self):
        s = doc("Secret", "telegram-bot", "flux-system", stringData={"token": "${TELEGRAM_BOT_TOKEN}"})
        self.assertEqual(fluxci.plaintext_keys(s), [])

    def test_literal_values_are_plaintext(self):
        s = doc("Secret", "x", "ns", stringData={"password": "hunter2", "ref": "pre-${VAR}"}, data={"k": "YQ=="})
        self.assertEqual(sorted(fluxci.plaintext_keys(s)), ["data.k", "stringData.password", "stringData.ref"])

    def test_sops_secret_is_not_plaintext(self):
        s = doc("Secret", "x", "ns", stringData={"k": "ENC[AES256_GCM,data:abc]"}, sops={"mac": "m"})
        self.assertEqual(fluxci.plaintext_keys(s), [])

    def test_sops_selection(self):
        meta = {"encrypted_regex": "^(data|stringData)$"}
        self.assertTrue(fluxci.sops_selected(("stringData", "password"), meta))
        self.assertFalse(fluxci.sops_selected(("metadata", "name"), meta))
        self.assertTrue(fluxci.sops_selected(("spec", "x"), {}))
        self.assertFalse(fluxci.sops_selected(("spec", "x_unencrypted"), {}))


class VarsTest(unittest.TestCase):
    def setUp(self):
        self.ks = doc("Kustomization", "apps", "flux-system", group=fluxci.KS_GROUP, spec={"postBuild": {
            "substitute": {"INLINE": "1"},
            "substituteFrom": [{"kind": "Secret", "name": "cluster-vars"},
                               {"kind": "ConfigMap", "name": "settings"},
                               {"kind": "Secret", "name": "missing", "optional": True}]}})
        self.pool = {
            ("Secret", "flux-system", "cluster-vars"): doc("Secret", "cluster-vars", "flux-system",
                                                           stringData={"DOMAIN": "ENC[...]"}, sops={"mac": "m"}),
            ("ConfigMap", "flux-system", "settings"): doc("ConfigMap", "settings", "flux-system",
                                                          data={"TZ": "Europe/Rome", "bad.key": "x"}),
        }

    def test_placeholders_and_configmaps(self):
        report = fluxci.Report("t")
        subs = {"secrets": {"flux-system/cluster-vars": {"DOMAIN": "ci.example.com"}}, "external": {}}
        got = fluxci.resolve_vars("flux-system/apps", self.ks, self.pool, subs, report)
        self.assertEqual(got, {"DOMAIN": "ci.example.com", "TZ": "Europe/Rome", "INLINE": "1"})
        self.assertEqual(len(report.errors), 1)  # bad.key is not a valid variable name
        self.assertIn("bad.key", report.errors[0][0])

    def test_missing_and_stale_placeholders_fail(self):
        report = fluxci.Report("t")
        subs = {"secrets": {"flux-system/cluster-vars": {"OTHER": "x"}}, "external": {}}
        fluxci.resolve_vars("flux-system/apps", self.ks, self.pool, subs, report)
        messages = " ".join(m for m, _ in report.errors)
        self.assertIn("defines DOMAIN", messages)
        self.assertIn("OTHER", messages)


class HelmTest(unittest.TestCase):
    def test_release_name(self):
        hr = doc("HelmRelease", "app", "apps", group=fluxci.HR_GROUP, spec={})
        self.assertEqual(fluxci.release_name(hr), "app")
        hr["spec"]["targetNamespace"] = "target"
        self.assertEqual(fluxci.release_name(hr), "target-app")
        hr["spec"]["releaseName"] = "explicit"
        self.assertEqual(fluxci.release_name(hr), "explicit")
        hr["spec"]["releaseName"] = "x" * 60
        self.assertEqual(len(fluxci.release_name(hr)), 53)

    def test_deep_merge(self):
        self.assertEqual(fluxci.deep_merge({"a": {"b": 1, "c": [1]}}, {"a": {"c": [2], "d": 3}}),
                         {"a": {"b": 1, "c": [2], "d": 3}})

    def test_fill_namespace(self):
        crd = {"spec": {"scope": "Cluster", "names": {"kind": "Thing"}}}
        docs = [doc("ConfigMap", "a"), doc("ClusterRole", "b", group="rbac.authorization.k8s.io"),
                doc("Thing", "c", group="example.com"), doc("Service", "d", "other")]
        fluxci.fill_namespace(docs, "release", [crd])
        self.assertEqual([fluxci.ns_of(d) for d in docs], ["release", "", "", "other"])


class OverlapTest(unittest.TestCase):
    def test_object_with_two_writers(self):
        gw = doc("Gateway", "traefik-gateway", "traefik", group="gateway.networking.k8s.io")
        owners = {fluxci.ident(gw): "flux-system/infrastructure"}
        found = fluxci.find_overlaps(owners, {"traefik/traefik": [gw, doc("Service", "traefik", "traefik")]})
        self.assertEqual(len(found), 1)
        self.assertIn("HelmRelease traefik/traefik", found[0])
        self.assertEqual(fluxci.find_overlaps(owners, {"traefik/traefik": [doc("Service", "traefik", "traefik")]}), [])


class SchemaTest(unittest.TestCase):
    def test_conversion(self):
        js = fluxci.to_json_schema({"type": "object", "properties": {
            "port": {"x-kubernetes-int-or-string": True},
            "name": {"type": "string", "nullable": True},
            "free": {"type": "object", "x-kubernetes-preserve-unknown-fields": True, "properties": {"a": {}}},
        }}, strict=True)
        self.assertFalse(js["additionalProperties"])
        self.assertEqual(js["properties"]["port"]["anyOf"], [{"type": "string"}, {"type": "integer"}])
        self.assertEqual(js["properties"]["name"]["type"], ["string", "null"])
        self.assertNotIn("additionalProperties", js["properties"]["free"])


class GuardTest(unittest.TestCase):
    def ids(self, *docs):
        return {fluxci.ident(d): d for d in docs}

    def test_removed_and_changed_pvc(self):
        pvc = doc("PersistentVolumeClaim", "data", "app", spec={"storageClassName": "nfs-flash"})
        moved = doc("PersistentVolumeClaim", "data", "app", spec={"storageClassName": "nfs-spacex"})
        self.assertEqual(len(fluxci.compare_objects(self.ids(pvc), {})), 1)
        self.assertIn("storageClassName", fluxci.compare_objects(self.ids(pvc), self.ids(moved))[0])

    def test_unprotected_changes_are_ignored(self):
        cm = doc("ConfigMap", "x", "app", data={"a": "1"})
        self.assertEqual(fluxci.compare_objects(self.ids(cm), {}), [])

    def test_statefulset_without_volumes_is_not_protected(self):
        sts = doc("StatefulSet", "s", "app", group="apps", spec={})
        self.assertEqual(fluxci.compare_objects(self.ids(sts), {}), [])

    def test_lost_prune_protection_and_owner_move(self):
        ann = {"metadata": {"name": "data", "namespace": "app",
                            "annotations": {fluxci.PRUNE_KEY: "disabled"}}}
        base = {**doc("PersistentVolumeClaim", "data", "app"), **ann}
        head = doc("PersistentVolumeClaim", "data", "app")
        i = fluxci.ident(base)
        found = fluxci.compare_objects({i: base}, {i: head}, {i: "flux-system/apps"}, {i: "flux-system/infra"})
        self.assertEqual(len(found), 2)

    def test_kustomization_prune_enabled(self):
        b = doc("Kustomization", "secrets", "flux-system", group=fluxci.KS_GROUP, spec={"prune": False, "path": "./a"})
        h = doc("Kustomization", "secrets", "flux-system", group=fluxci.KS_GROUP, spec={"prune": True, "path": "a"})
        self.assertEqual(fluxci.compare_objects(self.ids(b), self.ids(h)), [
            "Kustomization.kustomize.toolkit.fluxcd.io flux-system/secrets: prune is enabled"])


if __name__ == "__main__":
    unittest.main()
