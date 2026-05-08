"""
Tests for Kubernetes manifests added/modified in this PR.

Covers:
- .sops.yaml         - SOPS encryption configuration
- apps/authentik/*   - Authentik application manifests
- apps/external-services/* - External services manifests
- apps/gatus/*       - Gatus monitoring manifests
- apps/grafana/grafana-dashboards/configmap-dashboard-k8s-resources.yaml
"""

import json
import os
import re
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml

REPO_ROOT = Path(__file__).parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_yaml(rel_path: str) -> Any:
    """Load a YAML file relative to REPO_ROOT; raises FileNotFoundError if absent."""
    full = REPO_ROOT / rel_path
    with open(full) as fh:
        return yaml.safe_load(fh)


def load_yaml_all(rel_path: str) -> list[Any]:
    """Load all YAML documents from a multi-document file."""
    full = REPO_ROOT / rel_path
    with open(full) as fh:
        return [doc for doc in yaml.safe_load_all(fh) if doc is not None]


def assert_k8s_resource(doc: dict, *, api_version: str | None = None,
                         kind: str | None = None,
                         name: str | None = None,
                         namespace: str | None = None) -> None:
    """Assert basic Kubernetes resource fields."""
    assert "apiVersion" in doc, "Missing apiVersion"
    assert "kind" in doc, "Missing kind"
    assert "metadata" in doc, "Missing metadata"
    assert "name" in doc["metadata"], "Missing metadata.name"
    if api_version is not None:
        assert doc["apiVersion"] == api_version, (
            f"Expected apiVersion={api_version!r}, got {doc['apiVersion']!r}"
        )
    if kind is not None:
        assert doc["kind"] == kind, (
            f"Expected kind={kind!r}, got {doc['kind']!r}"
        )
    if name is not None:
        assert doc["metadata"]["name"] == name, (
            f"Expected name={name!r}, got {doc['metadata']['name']!r}"
        )
    if namespace is not None:
        assert doc["metadata"].get("namespace") == namespace, (
            f"Expected namespace={namespace!r}, got {doc['metadata'].get('namespace')!r}"
        )


# ===========================================================================
# .sops.yaml
# ===========================================================================

class TestSopsConfig:
    """Validate the SOPS creation-rules configuration added in this PR."""

    def setup_method(self):
        self.sops = load_yaml(".sops.yaml")

    def test_sops_has_creation_rules(self):
        assert "creation_rules" in self.sops
        assert isinstance(self.sops["creation_rules"], list)

    def test_sops_has_three_rules(self):
        assert len(self.sops["creation_rules"]) == 3

    def test_infrastructure_secrets_rule_present(self):
        rules = self.sops["creation_rules"]
        infra_rule = next(
            (r for r in rules if "infrastructure" in r.get("path_regex", "")),
            None,
        )
        assert infra_rule is not None, "No infrastructure rule found in creation_rules"

    def test_infrastructure_rule_fields(self):
        rules = self.sops["creation_rules"]
        infra_rule = next(r for r in rules if "infrastructure" in r.get("path_regex", ""))
        assert "path_regex" in infra_rule
        assert "encrypted_regex" in infra_rule
        assert "age" in infra_rule
        assert isinstance(infra_rule["age"], list)
        assert len(infra_rule["age"]) >= 1

    def test_infrastructure_rule_path_regex(self):
        rules = self.sops["creation_rules"]
        infra_rule = next(r for r in rules if "infrastructure" in r.get("path_regex", "")
                          and "notifications" not in r.get("path_regex", ""))
        assert re.search(r"infrastructure", infra_rule["path_regex"])
        assert "secret-" in infra_rule["path_regex"]
        assert r"\.sops\.yaml" in infra_rule["path_regex"]

    def test_infrastructure_rule_encrypted_regex(self):
        rules = self.sops["creation_rules"]
        infra_rule = next(r for r in rules if "infrastructure" in r.get("path_regex", "")
                          and "notifications" not in r.get("path_regex", ""))
        assert infra_rule["encrypted_regex"] == "^(data|stringData)$"

    def test_telegram_rule_present(self):
        rules = self.sops["creation_rules"]
        telegram_rule = next(
            (r for r in rules if "telegram" in r.get("path_regex", "")),
            None,
        )
        assert telegram_rule is not None, "No Telegram notification rule found"

    def test_telegram_rule_encrypted_spec(self):
        rules = self.sops["creation_rules"]
        telegram_rule = next(r for r in rules if "telegram" in r.get("path_regex", ""))
        assert telegram_rule["encrypted_regex"] == "^(spec)$"

    def test_apps_secrets_rule_present(self):
        rules = self.sops["creation_rules"]
        apps_rule = next(
            (r for r in rules if r.get("path_regex", "").startswith("apps/")),
            None,
        )
        assert apps_rule is not None, "No apps/* rule found in creation_rules"

    def test_apps_rule_path_regex(self):
        rules = self.sops["creation_rules"]
        apps_rule = next(r for r in rules if r.get("path_regex", "").startswith("apps/"))
        assert "secret-" in apps_rule["path_regex"]
        assert r"\.sops\.yaml" in apps_rule["path_regex"]

    def test_apps_rule_encrypted_regex(self):
        rules = self.sops["creation_rules"]
        apps_rule = next(r for r in rules if r.get("path_regex", "").startswith("apps/"))
        assert apps_rule["encrypted_regex"] == "^(data|stringData)$"

    def test_all_rules_have_age_recipient(self):
        """Verify all rules have a valid age recipient with consistent format."""
        rules = self.sops["creation_rules"]
        assert len(rules) > 0, "No creation rules found"
        # Extract the first rule's age recipient as the expected value
        first_recipient = rules[0]["age"][0]
        # Validate it matches the age key format
        assert re.match(r"^age[0-9a-z]+$", first_recipient), (
            f"First rule's age recipient {first_recipient!r} doesn't match expected format"
        )
        # Ensure all rules use the same age recipient
        for rule in rules:
            assert first_recipient in rule["age"], (
                f"Rule {rule.get('path_regex')} is missing expected age recipient"
            )

    def test_apps_rule_path_matches_sops_files(self):
        """Verify the apps regex actually matches the sops file paths in this PR."""
        rules = self.sops["creation_rules"]
        apps_rule = next(r for r in rules if r.get("path_regex", "").startswith("apps/"))
        pattern = re.compile(apps_rule["path_regex"])
        test_paths = [
            "apps/authentik/secret-authentik.sops.yaml",
            "apps/gatus/secret-gatus.sops.yaml",
        ]
        for path in test_paths:
            assert pattern.search(path), (
                f"Pattern {apps_rule['path_regex']!r} should match {path!r}"
            )

    def test_apps_rule_does_not_match_non_secret(self):
        rules = self.sops["creation_rules"]
        apps_rule = next(r for r in rules if r.get("path_regex", "").startswith("apps/"))
        pattern = re.compile(apps_rule["path_regex"])
        non_secret_paths = [
            "apps/authentik/namespace.yaml",
            "apps/authentik/helm-release.yaml",
        ]
        for path in non_secret_paths:
            assert not pattern.search(path), (
                f"Pattern should NOT match {path!r}"
            )


# ===========================================================================
# apps/authentik/*
# ===========================================================================

class TestAuthentikNamespace:
    def test_namespace_structure(self):
        doc = load_yaml("apps/authentik/namespace.yaml")
        assert_k8s_resource(doc, api_version="v1", kind="Namespace", name="authentik")

    def test_namespace_has_no_namespace_field(self):
        doc = load_yaml("apps/authentik/namespace.yaml")
        assert "namespace" not in doc.get("metadata", {})


class TestAuthentikPVC:
    def setup_method(self):
        self.docs = load_yaml_all("apps/authentik/pvc.yaml")

    def test_pvc_count(self):
        assert len(self.docs) == 3

    def test_all_pvcs_are_v1_pvc(self):
        for doc in self.docs:
            assert_k8s_resource(doc, api_version="v1", kind="PersistentVolumeClaim",
                                 namespace="authentik")

    def test_pvc_names(self):
        names = {doc["metadata"]["name"] for doc in self.docs}
        assert "authentik-media" in names
        assert "authentik-certs" in names
        assert "authentik-templates" in names

    def test_pvcs_use_rwx_access_mode(self):
        for doc in self.docs:
            modes = doc["spec"]["accessModes"]
            assert "ReadWriteMany" in modes, (
                f"PVC {doc['metadata']['name']} should use ReadWriteMany"
            )

    def test_pvcs_have_storage_class(self):
        for doc in self.docs:
            assert "storageClassName" in doc["spec"], (
                f"PVC {doc['metadata']['name']} must specify storageClassName"
            )
            assert doc["spec"]["storageClassName"] == "nfs-spacex"

    def test_media_pvc_storage_size(self):
        media = next(d for d in self.docs if d["metadata"]["name"] == "authentik-media")
        assert media["spec"]["resources"]["requests"]["storage"] == "10Gi"

    def test_certs_pvc_storage_size(self):
        certs = next(d for d in self.docs if d["metadata"]["name"] == "authentik-certs")
        assert certs["spec"]["resources"]["requests"]["storage"] == "1Gi"

    def test_templates_pvc_storage_size(self):
        tmpl = next(d for d in self.docs if d["metadata"]["name"] == "authentik-templates")
        assert tmpl["spec"]["resources"]["requests"]["storage"] == "1Gi"


class TestAuthentikHelmRepository:
    def test_helm_repository_structure(self):
        doc = load_yaml("apps/authentik/helm-repository.yaml")
        assert_k8s_resource(
            doc,
            api_version="source.toolkit.fluxcd.io/v1beta2",
            kind="HelmRepository",
            name="authentik",
            namespace="flux-system",
        )

    def test_helm_repository_url(self):
        doc = load_yaml("apps/authentik/helm-repository.yaml")
        assert doc["spec"]["url"] == "https://charts.goauthentik.io"

    def test_helm_repository_interval(self):
        doc = load_yaml("apps/authentik/helm-repository.yaml")
        assert "interval" in doc["spec"]


class TestAuthentikHelmRelease:
    def setup_method(self):
        self.doc = load_yaml("apps/authentik/helm-release.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "helm.toolkit.fluxcd.io/v2"

    def test_kind(self):
        assert self.doc["kind"] == "HelmRelease"

    def test_name_and_namespace(self):
        assert_k8s_resource(self.doc, name="authentik", namespace="authentik")

    def test_chart_ref_name(self):
        chart_spec = self.doc["spec"]["chart"]["spec"]
        assert chart_spec["chart"] == "authentik"

    def test_chart_source_ref_points_to_authentik(self):
        source_ref = self.doc["spec"]["chart"]["spec"]["sourceRef"]
        assert source_ref["kind"] == "HelmRepository"
        assert source_ref["name"] == "authentik"
        assert source_ref["namespace"] == "flux-system"

    def test_target_namespace(self):
        assert self.doc["spec"]["targetNamespace"] == "authentik"

    def test_postgresql_enabled(self):
        assert self.doc["spec"]["values"]["postgresql"]["enabled"] is True

    def test_postgresql_storage_class(self):
        pg_persistence = self.doc["spec"]["values"]["postgresql"]["primary"]["persistence"]
        assert pg_persistence["storageClass"] == "nfs-flash"

    def test_postgresql_existing_secret(self):
        pg_auth = self.doc["spec"]["values"]["postgresql"]["auth"]
        assert pg_auth["existingSecret"] == "authentik-secrets"

    def test_server_enabled(self):
        assert self.doc["spec"]["values"]["server"]["enabled"] is True

    def test_worker_enabled(self):
        assert self.doc["spec"]["values"]["worker"]["enabled"] is True

    def test_server_metrics_enabled(self):
        metrics = self.doc["spec"]["values"]["server"]["metrics"]
        assert metrics["enabled"] is True

    def test_service_monitor_enabled(self):
        sm = self.doc["spec"]["values"]["server"]["metrics"]["serviceMonitor"]
        assert sm["enabled"] is True

    def test_ingress_disabled(self):
        ingress = self.doc["spec"]["values"]["server"]["ingress"]
        assert ingress["enabled"] is False

    def test_httproute_enabled(self):
        route = self.doc["spec"]["values"]["server"]["route"]["main"]
        assert route["enabled"] is True

    def test_httproute_parent_ref(self):
        parent_refs = self.doc["spec"]["values"]["server"]["route"]["main"]["parentRefs"]
        assert len(parent_refs) >= 1
        assert parent_refs[0]["name"] == "traefik-gateway"
        assert parent_refs[0]["namespace"] == "traefik"

    def test_env_references_secret(self):
        env_vars = self.doc["spec"]["values"]["global"]["env"]
        secret_refs = [e for e in env_vars if "valueFrom" in e]
        secret_names = [
            e["valueFrom"]["secretKeyRef"]["name"] for e in secret_refs
        ]
        assert all(name == "authentik-secrets" for name in secret_names)

    def test_env_contains_secret_key(self):
        env_vars = self.doc["spec"]["values"]["global"]["env"]
        names = [e["name"] for e in env_vars]
        assert "AUTHENTIK_SECRET_KEY" in names

    def test_server_resource_limits_defined(self):
        resources = self.doc["spec"]["values"]["server"]["resources"]
        assert "limits" in resources
        assert "requests" in resources

    def test_worker_resource_limits_defined(self):
        resources = self.doc["spec"]["values"]["worker"]["resources"]
        assert "limits" in resources
        assert "requests" in resources


class TestAuthentikHTTPRoute:
    def setup_method(self):
        self.doc = load_yaml("apps/authentik/httproute.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "gateway.networking.k8s.io/v1"

    def test_kind(self):
        assert self.doc["kind"] == "HTTPRoute"

    def test_name_and_namespace(self):
        assert_k8s_resource(self.doc, name="authentik-outpost", namespace="authentik")

    def test_parent_ref_traefik(self):
        parent_refs = self.doc["spec"]["parentRefs"]
        assert any(
            p["name"] == "traefik-gateway" and p["namespace"] == "traefik"
            for p in parent_refs
        )

    def test_rules_have_backend_ref(self):
        rules = self.doc["spec"]["rules"]
        assert len(rules) >= 1
        for rule in rules:
            assert "backendRefs" in rule

    def test_backend_port(self):
        backend = self.doc["spec"]["rules"][0]["backendRefs"][0]
        assert backend["port"] == 9000

    def test_matches_outpost_path(self):
        rule = self.doc["spec"]["rules"][0]
        path_match = rule["matches"][0]["path"]
        assert "/outpost.goauthentik.io" in path_match["value"]

    def test_wildcard_hostnames_present(self):
        hostnames = self.doc["spec"]["hostnames"]
        assert any("*." in h for h in hostnames)


class TestAuthentikKustomization:
    def setup_method(self):
        self.doc = load_yaml("apps/authentik/kustomization.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "kustomize.config.k8s.io/v1beta1"

    def test_kind(self):
        assert self.doc["kind"] == "Kustomization"

    def test_resources_list(self):
        resources = self.doc["resources"]
        assert "namespace.yaml" in resources
        assert "helm-release.yaml" in resources
        assert "helm-repository.yaml" in resources
        assert "pvc.yaml" in resources
        assert "httproute.yaml" in resources

    def test_secret_included(self):
        resources = self.doc["resources"]
        assert any("secret" in r for r in resources)

    def test_referenced_files_exist(self):
        base = REPO_ROOT / "apps/authentik"
        for res in self.doc["resources"]:
            assert (base / res).exists(), f"Referenced resource {res!r} does not exist"


class TestAuthentikSOPSSecret:
    def setup_method(self):
        self.doc = load_yaml("apps/authentik/secret-authentik.sops.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "v1"

    def test_kind(self):
        assert self.doc["kind"] == "Secret"

    def test_name_and_namespace(self):
        assert_k8s_resource(self.doc, name="authentik-secrets", namespace="authentik")

    def test_type_opaque(self):
        assert self.doc["type"] == "Opaque"

    def test_data_keys_present(self):
        data = self.doc["data"]
        expected_keys = [
            "secret-key",
            "email-password",
            "email-from",
            "email-host",
            "email-username",
            "postgres-username",
            "postgres-password",
        ]
        for key in expected_keys:
            assert key in data, f"Expected key {key!r} in secret data"

    def test_data_values_are_encrypted(self):
        """Ensure SOPS-encrypted values start with ENC[AES256_GCM,..."""
        for key, value in self.doc["data"].items():
            assert str(value).startswith("ENC["), (
                f"Key {key!r} does not appear to be SOPS-encrypted"
            )

    def test_sops_metadata_present(self):
        assert "sops" in self.doc

    def test_sops_encrypted_regex(self):
        assert self.doc["sops"]["encrypted_regex"] == "^(data|stringData)$"

    def test_sops_age_recipient_present(self):
        age_list = self.doc["sops"]["age"]
        assert len(age_list) >= 1
        assert "recipient" in age_list[0]


# ===========================================================================
# apps/external-services/*
# ===========================================================================

class TestExternalServicesNamespace:
    def test_namespace_structure(self):
        doc = load_yaml("apps/external-services/namespace.yaml")
        assert_k8s_resource(doc, api_version="v1", kind="Namespace", name="external-services")


class TestExternalServicesService:
    def setup_method(self):
        self.doc = load_yaml("apps/external-services/service.yaml")

    def test_structure(self):
        assert_k8s_resource(self.doc, api_version="v1", kind="Service",
                             name="external-http", namespace="external-services")

    def test_cluster_ip_type(self):
        assert self.doc["spec"]["type"] == "ClusterIP"

    def test_port_8080(self):
        ports = self.doc["spec"]["ports"]
        assert any(p["port"] == 8080 for p in ports)

    def test_http_port_name(self):
        ports = self.doc["spec"]["ports"]
        assert any(p["name"] == "http" for p in ports)


class TestExternalServicesEndpoints:
    def setup_method(self):
        self.doc = load_yaml("apps/external-services/endpoints.yaml")

    def test_structure(self):
        assert_k8s_resource(self.doc, api_version="v1", kind="Endpoints",
                             name="external-http", namespace="external-services")

    def test_service_name_matches(self):
        """Endpoints name must match Service name."""
        assert self.doc["metadata"]["name"] == "external-http"

    def test_subsets_present(self):
        assert "subsets" in self.doc
        assert len(self.doc["subsets"]) >= 1

    def test_ip_address_is_valid(self):
        ip = self.doc["subsets"][0]["addresses"][0]["ip"]
        parts = ip.split(".")
        assert len(parts) == 4
        assert all(p.isdigit() and 0 <= int(p) <= 255 for p in parts)

    def test_port_matches_service_port(self):
        port_entry = self.doc["subsets"][0]["ports"][0]
        assert port_entry["port"] == 8080

    def test_protocol_tcp(self):
        port_entry = self.doc["subsets"][0]["ports"][0]
        assert port_entry["protocol"] == "TCP"


class TestExternalServicesMiddleware:
    def setup_method(self):
        self.doc = load_yaml("apps/external-services/middleware-authentik.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "traefik.io/v1alpha1"

    def test_kind(self):
        assert self.doc["kind"] == "Middleware"

    def test_name_and_namespace(self):
        assert_k8s_resource(self.doc, name="authentik-forward-auth",
                             namespace="external-services")

    def test_forward_auth_address(self):
        addr = self.doc["spec"]["forwardAuth"]["address"]
        assert "ak-outpost-authentik-embedded-outpost" in addr
        assert "9000" in addr
        assert "/outpost.goauthentik.io/auth/traefik" in addr

    def test_trust_forward_header(self):
        assert self.doc["spec"]["forwardAuth"]["trustForwardHeader"] is True

    def test_auth_response_headers(self):
        headers = self.doc["spec"]["forwardAuth"]["authResponseHeaders"]
        expected = [
            "X-authentik-username",
            "X-authentik-groups",
            "X-authentik-email",
            "X-authentik-name",
            "X-authentik-uid",
            "X-authentik-jwt",
        ]
        for header in expected:
            assert header in headers, f"Expected header {header!r} in authResponseHeaders"

    def test_all_auth_response_headers_are_strings(self):
        headers = self.doc["spec"]["forwardAuth"]["authResponseHeaders"]
        assert all(isinstance(h, str) for h in headers)


class TestExternalServicesHTTPRoute:
    def setup_method(self):
        self.doc = load_yaml("apps/external-services/httproute.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "gateway.networking.k8s.io/v1"

    def test_kind(self):
        assert self.doc["kind"] == "HTTPRoute"

    def test_name_and_namespace(self):
        assert_k8s_resource(self.doc, name="external-http", namespace="external-services")

    def test_parent_ref_traefik(self):
        parent_refs = self.doc["spec"]["parentRefs"]
        assert parent_refs[0]["name"] == "traefik-gateway"
        assert parent_refs[0]["namespace"] == "traefik"
        assert parent_refs[0]["sectionName"] == "websecure"

    def test_hostnames_present(self):
        assert len(self.doc["spec"]["hostnames"]) >= 1

    def test_rule_has_authentik_filter(self):
        rule = self.doc["spec"]["rules"][0]
        filters = rule.get("filters", [])
        ext_refs = [f for f in filters if f["type"] == "ExtensionRef"]
        assert len(ext_refs) >= 1
        ext_ref = ext_refs[0]["extensionRef"]
        assert ext_ref["kind"] == "Middleware"
        assert ext_ref["name"] == "authentik-forward-auth"

    def test_backend_ref_name_and_port(self):
        backend = self.doc["spec"]["rules"][0]["backendRefs"][0]
        assert backend["name"] == "external-http"
        assert backend["port"] == 8080

    def test_filter_uses_traefik_group(self):
        rule = self.doc["spec"]["rules"][0]
        ext_ref = next(
            f["extensionRef"] for f in rule["filters"] if f["type"] == "ExtensionRef"
        )
        assert ext_ref["group"] == "traefik.io"


class TestExternalServicesKustomization:
    def setup_method(self):
        self.doc = load_yaml("apps/external-services/kustomization.yaml")

    def test_api_version(self):
        assert self.doc["apiVersion"] == "kustomize.config.k8s.io/v1beta1"

    def test_kind(self):
        assert self.doc["kind"] == "Kustomization"

    def test_resources_present(self):
        resources = self.doc["resources"]
        for expected in ["namespace.yaml", "service.yaml", "endpoints.yaml",
                         "middleware-authentik.yaml", "httproute.yaml"]:
            assert expected in resources

    def test_referenced_files_exist(self):
        base = REPO_ROOT / "apps/external-services"
        for res in self.doc["resources"]:
            assert (base / res).exists(), f"Referenced resource {res!r} does not exist"


# ===========================================================================
# apps/gatus/*
# ===========================================================================

class TestGatusNamespace:
    def test_namespace_structure(self):
        doc = load_yaml("apps/gatus/namespace.yaml")
        assert_k8s_resource(doc, api_version="v1", kind="Namespace", name="gatus")


class TestGatusPVC:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/pvc.yaml")

    def test_structure(self):
        assert_k8s_resource(self.doc, api_version="v1", kind="PersistentVolumeClaim",
                             name="gatus-data", namespace="gatus")

    def test_rwx_access_mode(self):
        assert "ReadWriteMany" in self.doc["spec"]["accessModes"]

    def test_storage_class(self):
        assert self.doc["spec"]["storageClassName"] == "nfs-spacex"

    def test_storage_size(self):
        assert self.doc["spec"]["resources"]["requests"]["storage"] == "200Mi"


class TestGatusHelmRepository:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/helm-repository.yaml")

    def test_structure(self):
        assert_k8s_resource(
            self.doc,
            api_version="source.toolkit.fluxcd.io/v1",
            kind="HelmRepository",
            name="twin",
            namespace="flux-system",
        )

    def test_url(self):
        assert "twin.github.io/helm-charts" in self.doc["spec"]["url"]

    def test_interval(self):
        assert "interval" in self.doc["spec"]


class TestGatusHelmRelease:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/helm-release.yaml")

    def test_structure(self):
        assert_k8s_resource(
            self.doc,
            api_version="helm.toolkit.fluxcd.io/v2",
            kind="HelmRelease",
            name="gatus",
            namespace="gatus",
        )

    def test_chart_name(self):
        assert self.doc["spec"]["chart"]["spec"]["chart"] == "gatus"

    def test_chart_source_ref(self):
        source_ref = self.doc["spec"]["chart"]["spec"]["sourceRef"]
        assert source_ref["kind"] == "HelmRepository"
        assert source_ref["name"] == "twin"
        assert source_ref["namespace"] == "flux-system"

    def test_persistence_existing_claim(self):
        assert self.doc["spec"]["values"]["persistence"]["existingClaim"] == "gatus-data"

    def test_service_type_cluster_ip(self):
        service = self.doc["spec"]["values"]["service"]
        assert service["type"] == "ClusterIP"

    def test_external_config_map_gatus(self):
        assert self.doc["spec"]["values"]["externalConfigMap"] == "gatus"

    def test_env_oidc_client_id_from_secret(self):
        env = self.doc["spec"]["values"]["env"]
        assert "OIDC_CLIENT_ID" in env
        sk = env["OIDC_CLIENT_ID"]["valueFrom"]["secretKeyRef"]
        assert sk["name"] == "gatus-secrets"
        assert sk["key"] == "OIDC_CLIENT_ID"

    def test_env_oidc_client_secret_from_secret(self):
        env = self.doc["spec"]["values"]["env"]
        assert "OIDC_CLIENT_SECRET" in env
        sk = env["OIDC_CLIENT_SECRET"]["valueFrom"]["secretKeyRef"]
        assert sk["name"] == "gatus-secrets"
        assert sk["key"] == "OIDC_CLIENT_SECRET"


class TestGatusHTTPRoute:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/httproute.yaml")

    def test_structure(self):
        assert_k8s_resource(
            self.doc,
            api_version="gateway.networking.k8s.io/v1",
            kind="HTTPRoute",
            name="gatus",
            namespace="gatus",
        )

    def test_parent_ref(self):
        parent_ref = self.doc["spec"]["parentRefs"][0]
        assert parent_ref["name"] == "traefik-gateway"
        assert parent_ref["sectionName"] == "websecure"

    def test_backend_ref_port(self):
        backend = self.doc["spec"]["rules"][0]["backendRefs"][0]
        assert backend["port"] == 80
        assert backend["name"] == "gatus"


class TestGatusServiceMonitor:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/servicemonitor-gatus.yaml")

    def test_structure(self):
        assert_k8s_resource(
            self.doc,
            api_version="monitoring.coreos.com/v1",
            kind="ServiceMonitor",
            name="gatus",
            namespace="gatus",
        )

    def test_prometheus_label(self):
        labels = self.doc["metadata"].get("labels", {})
        assert labels.get("release") == "prometheus"

    def test_selector_match_labels(self):
        match_labels = self.doc["spec"]["selector"]["matchLabels"]
        assert match_labels.get("app.kubernetes.io/name") == "gatus"

    def test_namespace_selector_not_any(self):
        ns_selector = self.doc["spec"]["namespaceSelector"]
        assert ns_selector.get("any") is False

    def test_endpoints_port(self):
        endpoint = self.doc["spec"]["endpoints"][0]
        assert endpoint["port"] == "http"

    def test_endpoints_metrics_path(self):
        endpoint = self.doc["spec"]["endpoints"][0]
        assert endpoint["path"] == "/metrics"

    def test_endpoints_interval(self):
        endpoint = self.doc["spec"]["endpoints"][0]
        assert "interval" in endpoint


class TestGatusKustomization:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/kustomization.yaml")

    def test_kind(self):
        assert self.doc["kind"] == "Kustomization"

    def test_all_resources_listed(self):
        resources = self.doc["resources"]
        expected = [
            "namespace.yaml",
            "configmap-template.yaml",
            "secret-gatus.sops.yaml",
            "helm-repository.yaml",
            "helm-release.yaml",
            "pvc.yaml",
            "httproute.yaml",
            "servicemonitor-gatus.yaml",
        ]
        for r in expected:
            assert r in resources, f"{r!r} not found in kustomization resources"

    def test_referenced_files_exist(self):
        base = REPO_ROOT / "apps/gatus"
        for res in self.doc["resources"]:
            assert (base / res).exists(), f"Referenced resource {res!r} does not exist"


class TestGatusSOPSSecret:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/secret-gatus.sops.yaml")

    def test_structure(self):
        assert_k8s_resource(self.doc, api_version="v1", kind="Secret",
                             name="gatus-secrets", namespace="gatus")

    def test_type_opaque(self):
        assert self.doc["type"] == "Opaque"

    def test_string_data_keys_present(self):
        string_data = self.doc["stringData"]
        expected_keys = ["OIDC_CLIENT_ID", "OIDC_CLIENT_SECRET",
                         "TELEGRAM_CHAT_ID", "TELEGRAM_BOT_TOKEN"]
        for key in expected_keys:
            assert key in string_data, f"Expected key {key!r} in stringData"

    def test_string_data_values_encrypted(self):
        for key, value in self.doc["stringData"].items():
            assert str(value).startswith("ENC["), (
                f"Key {key!r} should be SOPS-encrypted"
            )

    def test_sops_encrypted_regex_covers_string_data(self):
        """Verify encrypted_regex covers stringData so SOPS can decrypt it."""
        pattern = self.doc["sops"]["encrypted_regex"]
        assert "stringData" in pattern or re.search(r"stringData", pattern)


class TestGatusConfigMapTemplate:
    def setup_method(self):
        self.doc = load_yaml("apps/gatus/configmap-template.yaml")

    def test_structure(self):
        assert_k8s_resource(self.doc, api_version="v1", kind="ConfigMap",
                             name="gatus", namespace="gatus")

    def test_config_yaml_key_present(self):
        assert "config.yaml" in self.doc["data"]

    def test_config_contains_oidc_section(self):
        config_text = self.doc["data"]["config.yaml"]
        assert "oidc" in config_text

    def test_config_contains_alerting_section(self):
        config_text = self.doc["data"]["config.yaml"]
        assert "alerting" in config_text
        assert "telegram" in config_text

    def test_config_contains_endpoints_section(self):
        config_text = self.doc["data"]["config.yaml"]
        assert "endpoints:" in config_text

    def test_oidc_issuer_url_template(self):
        config_text = self.doc["data"]["config.yaml"]
        assert "${DOMAIN}" in config_text

    def test_config_has_metrics_enabled(self):
        config_text = self.doc["data"]["config.yaml"]
        assert "metrics: true" in config_text

    def test_config_has_ui_section(self):
        config_text = self.doc["data"]["config.yaml"]
        assert "ui:" in config_text


# ===========================================================================
# apps/grafana/grafana-dashboards/configmap-dashboard-k8s-resources.yaml
# ===========================================================================

class TestGrafanaK8sResourcesDashboard:
    """
    Tests for the Grafana Kubernetes Resources dashboard ConfigMap.

    Note: The embedded JSON contains PromQL label-matcher expressions such as
    container!~\\"POD|\\\\"} which use non-standard JSON escape sequences (backslash-}).
    Strict json.loads() therefore cannot be used here; tests rely on raw string
    matching of the literal block content instead.
    """

    def setup_method(self):
        self.doc = load_yaml(
            "apps/grafana/grafana-dashboards/configmap-dashboard-k8s-resources.yaml"
        )

    def test_structure(self):
        assert_k8s_resource(
            self.doc,
            api_version="v1",
            kind="ConfigMap",
            name="grafana-k8s-resources",
            namespace="grafana",
        )

    def test_grafana_dashboard_label(self):
        labels = self.doc["metadata"].get("labels", {})
        assert labels.get("grafana_dashboard") == "1"

    def test_dashboard_json_key_present(self):
        assert "k8s-resources-overview.json" in self.doc["data"]

    def test_dashboard_raw_content_is_non_empty(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert len(raw.strip()) > 100

    def test_dashboard_has_title_field(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"title"' in raw
        assert "Kubernetes Resources" in raw

    def test_dashboard_uid_present(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"uid"' in raw
        assert "k8s-resources-overview" in raw

    def test_dashboard_has_panels_key(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"panels"' in raw

    def test_dashboard_has_cpu_panel_title(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "CPU" in raw

    def test_dashboard_has_memory_panel_title(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "Memory" in raw

    def test_dashboard_tags_include_k8s(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"k8s"' in raw

    def test_dashboard_uses_prometheus_datasource(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"Prometheus"' in raw

    def test_dashboard_schema_version_present(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"schemaVersion"' in raw

    def test_dashboard_has_topk_expression(self):
        """Top 10 panels should use PromQL topk()."""
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "topk" in raw

    def test_dashboard_has_timeseries_panels(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"timeseries"' in raw

    def test_dashboard_contains_namespace_grouping(self):
        """Panels should group metrics by Kubernetes namespace."""
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "namespace" in raw

    def test_dashboard_contains_rate_function(self):
        """CPU panels should use PromQL rate() for CPU usage."""
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "rate(" in raw

    def test_dashboard_has_four_panel_ids(self):
        """Verify there are 4 panel id entries (id: 1,2,3,4)."""
        raw = self.doc["data"]["k8s-resources-overview.json"]
        # Count occurrences of "id": followed by a digit
        id_matches = re.findall(r'"id":\s*\d+', raw)
        # Each panel has one "id" field
        assert len(id_matches) >= 4

    def test_dashboard_top10_by_memory_present(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "Top 10 Pods by Memory" in raw

    def test_dashboard_top10_by_cpu_present(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert "Top 10 Pods by CPU" in raw

    def test_dashboard_tags_include_resources(self):
        raw = self.doc["data"]["k8s-resources-overview.json"]
        assert '"resources"' in raw


# ===========================================================================
# Cross-cutting: namespace consistency
# ===========================================================================

class TestNamespaceConsistency:
    """Each app's resources must use the same namespace as their Namespace manifest."""

    @pytest.mark.parametrize("app,expected_ns", [
        ("authentik", "authentik"),
        ("external-services", "external-services"),
        ("gatus", "gatus"),
    ])
    def test_namespace_name_matches_app(self, app: str, expected_ns: str):
        ns_doc = load_yaml(f"apps/{app}/namespace.yaml")
        assert ns_doc["metadata"]["name"] == expected_ns

    @pytest.mark.parametrize("app,manifest,expected_ns", [
        ("authentik", "httproute.yaml", "authentik"),
        ("authentik", "pvc.yaml", "authentik"),
        ("gatus", "httproute.yaml", "gatus"),
        ("gatus", "pvc.yaml", "gatus"),
        ("gatus", "servicemonitor-gatus.yaml", "gatus"),
        ("external-services", "service.yaml", "external-services"),
        ("external-services", "endpoints.yaml", "external-services"),
        ("external-services", "httproute.yaml", "external-services"),
        ("external-services", "middleware-authentik.yaml", "external-services"),
    ])
    def test_resource_namespace_matches_app_namespace(
        self, app: str, manifest: str, expected_ns: str
    ):
        # Use load_yaml_all to safely handle multi-document files
        docs = load_yaml_all(f"apps/{app}/{manifest}")
        assert len(docs) >= 1
        # All documents in the file must belong to the correct namespace
        for doc in docs:
            if doc is None:
                continue
            ns = doc.get("metadata", {}).get("namespace") if isinstance(doc, dict) else None
            assert ns == expected_ns, (
                f"apps/{app}/{manifest}: expected namespace {expected_ns!r}, got {ns!r}"
            )


# ===========================================================================
# Cross-cutting: HelmRelease → HelmRepository name consistency
# ===========================================================================

class TestHelmReleaseRepositoryConsistency:
    @pytest.mark.parametrize("app,release_file,repo_name", [
        ("authentik", "helm-release.yaml", "authentik"),
        ("gatus", "helm-release.yaml", "twin"),
    ])
    def test_helm_release_source_ref_matches_repository(
        self, app: str, release_file: str, repo_name: str
    ):
        release = load_yaml(f"apps/{app}/{release_file}")
        source_ref = release["spec"]["chart"]["spec"]["sourceRef"]
        assert source_ref["name"] == repo_name

    @pytest.mark.parametrize("app,repo_name_in_release", [
        ("authentik", "authentik"),
        ("gatus", "twin"),
    ])
    def test_helm_repository_name_matches_release_reference(
        self, app: str, repo_name_in_release: str
    ):
        repo = load_yaml(f"apps/{app}/helm-repository.yaml")
        assert repo["metadata"]["name"] == repo_name_in_release


# ===========================================================================
# Cross-cutting: SOPS secrets use correct namespace
# ===========================================================================

class TestSOPSSecretNamespaces:
    @pytest.mark.parametrize("path,expected_ns", [
        ("apps/authentik/secret-authentik.sops.yaml", "authentik"),
        ("apps/gatus/secret-gatus.sops.yaml", "gatus"),
    ])
    def test_sops_secret_namespace(self, path: str, expected_ns: str):
        doc = load_yaml(path)
        assert doc["metadata"]["namespace"] == expected_ns

    @pytest.mark.parametrize("path", [
        "apps/authentik/secret-authentik.sops.yaml",
        "apps/gatus/secret-gatus.sops.yaml",
    ])
    def test_sops_secret_has_sops_block(self, path: str):
        doc = load_yaml(path)
        assert "sops" in doc, f"{path} is missing the sops block"

    @pytest.mark.parametrize("path", [
        "apps/authentik/secret-authentik.sops.yaml",
        "apps/gatus/secret-gatus.sops.yaml",
    ])
    def test_sops_secret_version(self, path: str):
        doc = load_yaml(path)
        version = doc["sops"].get("version", "")
        # Must be a valid semver-ish string
        assert re.fullmatch(r"\d+\.\d+\.\d+", version), (
            f"{path}: sops version {version!r} does not look like semver"
        )
