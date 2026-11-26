#!/usr/bin/env python3
# /// script
# requires-python = ">=3.12"
# dependencies = ["pyyaml==6.0.3"]
# ///
"""fluxci: validate the Flux tree of this repository without a cluster.

fluxci rebuilds what Flux would apply and renders what helm-controller would
install, so that CI can validate the result before it reaches the cluster.

Commands:
  build     kustomize build of every Flux Kustomization reachable from the
            bootstrap Kustomization, with postBuild substitution in strict mode
  check     static checks on a build: SOPS, plaintext Secrets, references,
            duplicates, orphan files, prune protection
  plan      the HelmReleases to render: the changed ones, or all of them
  render    helm template of the planned HelmReleases, as helm-controller does
  schemas   kubeconform JSON schemas generated from the CRDs we ship
  bundle    the files to validate, one per Kustomization or HelmRelease
  validate  kubeconform and conftest on a bundle
  guard     destructive changes between two builds or two renders
  diff      markdown report of what a pull request changes

Configuration: .github/ci/config.yaml and .github/ci/substitutions.yaml.
Run `fluxci.py <command> --help` for the options of each command.
"""

from __future__ import annotations

import argparse
import base64
import copy
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import yaml

CONFIG_FILE = ".github/ci/config.yaml"
SUBSTITUTIONS_FILE = ".github/ci/substitutions.yaml"
OUTPUT_MARKER = ".fluxci-output"

KS_GROUP = "kustomize.toolkit.fluxcd.io"
HR_GROUP = "helm.toolkit.fluxcd.io"
SOURCE_GROUP = "source.toolkit.fluxcd.io"
PRUNE_KEY = "kustomize.toolkit.fluxcd.io/prune"
SUBSTITUTE_KEY = "kustomize.toolkit.fluxcd.io/substitute"
ORIGIN_NAME = "helm.toolkit.fluxcd.io/name"
ORIGIN_NAMESPACE = "helm.toolkit.fluxcd.io/namespace"
KUSTOMIZATION_FILES = ("kustomization.yaml", "kustomization.yml", "Kustomization")
VAR_NAME = re.compile(r"^[_a-zA-Z][_a-zA-Z0-9]*$")
REDACTED = "<redacted>"

Ident = tuple[str, str, str, str]  # (group, kind, namespace, name)


class CIError(Exception):
    """An error that is reported to the user without a traceback."""


# --------------------------------------------------------------------------
# YAML and object helpers
# --------------------------------------------------------------------------


class Loader(yaml.SafeLoader):
    """SafeLoader that keeps timestamps and the YAML 1.1 "=" value key as strings.

    Kubernetes treats both as plain strings, and PyYAML would otherwise fail on
    a bare `=` (kube-prometheus-stack has one) or turn dates into objects.
    """


Loader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag not in ("tag:yaml.org,2002:timestamp", "tag:yaml.org,2002:value")]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}


class Dumper(yaml.SafeDumper):
    """Block style for multi-line strings, so that diffs stay readable."""


def _represent_str(dumper: yaml.SafeDumper, data: str) -> yaml.Node:
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


Dumper.add_representer(str, _represent_str)


def load_yaml(text: str) -> Any:
    return yaml.load(text, Loader=Loader)


def load_docs(text: str, source: str = "") -> list[dict]:
    try:
        return [d for d in yaml.load_all(text, Loader=Loader) if isinstance(d, dict)]
    except yaml.YAMLError as e:
        raise CIError(f"{source or 'input'}: invalid YAML: {e}") from e


def dump_docs(docs: list[dict], sort_keys: bool = False) -> str:
    if not docs:
        return ""
    return yaml.dump_all(
        docs,
        Dumper=Dumper,
        sort_keys=sort_keys,
        default_flow_style=False,
        allow_unicode=True,
        width=10**9,
        explicit_start=True,
    )


def api_group(doc: dict) -> str:
    av = str(doc.get("apiVersion") or "")
    return av.rsplit("/", 1)[0] if "/" in av else ""


def meta(doc: dict) -> dict:
    m = doc.get("metadata")
    return m if isinstance(m, dict) else {}


def ns_of(doc: dict) -> str:
    return str(meta(doc).get("namespace") or "")


def name_of(doc: dict) -> str:
    return str(meta(doc).get("name") or "")


def annotations(doc: dict) -> dict:
    return meta(doc).get("annotations") or {}


def labels(doc: dict) -> dict:
    return meta(doc).get("labels") or {}


def ident(doc: dict) -> Ident:
    return (api_group(doc), str(doc.get("kind") or ""), ns_of(doc), name_of(doc))


def ident_str(i: Ident) -> str:
    group, kind, ns, name = i
    k = f"{kind}.{group}" if group else kind
    return f"{k} {ns}/{name}" if ns else f"{k} {name}"


def is_kind(doc: dict, group: str, kind: str) -> bool:
    return api_group(doc) == group and doc.get("kind") == kind


def is_flux_ks(doc: dict) -> bool:
    return is_kind(doc, KS_GROUP, "Kustomization")


def is_hr(doc: dict) -> bool:
    return is_kind(doc, HR_GROUP, "HelmRelease")


def is_crd(doc: dict) -> bool:
    return is_kind(doc, "apiextensions.k8s.io", "CustomResourceDefinition")


def is_sops(doc: dict) -> bool:
    return isinstance(doc.get("sops"), dict)


def is_hook(doc: dict) -> bool:
    return "helm.sh/hook" in annotations(doc)


def obj_id(namespace: str, name: str) -> str:
    return f"{namespace}/{name}"


def file_key(oid: str) -> str:
    return oid.replace("/", "__")


def deep_merge(base: Any, override: Any) -> Any:
    """Merge maps recursively, as Helm merges values; anything else is replaced."""
    if isinstance(base, dict) and isinstance(override, dict):
        out = dict(base)
        for k, v in override.items():
            out[k] = deep_merge(out[k], v) if k in out else copy.deepcopy(v)
        return out
    return copy.deepcopy(override)


def secret_data(doc: dict) -> dict[str, str]:
    """Plaintext values of a (non-SOPS) Secret."""
    out = {}
    for k, v in (doc.get("data") or {}).items():
        try:
            out[k] = base64.b64decode(str(v)).decode()
        except (ValueError, UnicodeDecodeError):
            out[k] = ""
    for k, v in (doc.get("stringData") or {}).items():
        out[k] = str(v)
    return out


def secret_keys(doc: dict) -> list[str]:
    return sorted(set((doc.get("data") or {}).keys()) | set((doc.get("stringData") or {}).keys()))


ONLY_VAR_REFS = re.compile(r"\s*(\$\{[_a-zA-Z][_a-zA-Z0-9]*\}\s*)+")


def plaintext_keys(doc: dict) -> list[str]:
    """Keys of a non-SOPS Secret that hold literal values.

    A stringData value made only of ${VAR} references is not a secret in Git:
    Flux fills it from a SOPS-encrypted Secret at build time.
    """
    if not is_kind(doc, "", "Secret") or is_sops(doc):
        return []
    keys = [f"data.{k}" for k, v in (doc.get("data") or {}).items() if v not in (None, "")]
    keys += [f"stringData.{k}" for k, v in (doc.get("stringData") or {}).items()
             if v not in (None, "") and not ONLY_VAR_REFS.fullmatch(str(v))]
    return keys


# --------------------------------------------------------------------------
# Process, files and reporting
# --------------------------------------------------------------------------


def run(cmd: list[str], *, cwd: Path | None = None, input: str | None = None,
        env: dict | None = None, retries: int = 0) -> subprocess.CompletedProcess:
    for attempt in range(retries + 1):
        p = subprocess.run(cmd, cwd=cwd, input=input, env=env, capture_output=True, text=True)
        if p.returncode == 0:
            return p
        if attempt < retries:
            time.sleep(3 * (attempt + 1))
    detail = (p.stderr or p.stdout).strip()
    raise CIError(f"`{' '.join(cmd[:3])} ...` failed (exit {p.returncode}): {detail[-2000:]}")


def tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise CIError(f"{name} not found in PATH (run `mise install`)")
    return path


def kustomize_cmd() -> list[str]:
    exe = shutil.which("kustomize")
    base = [exe, "build"] if exe else [tool("kubectl"), "kustomize"]
    return base + ["--load-restrictor", "LoadRestrictionsNone"]


def prepare_out(path: str | Path) -> Path:
    """Create an empty output directory; only directories made by fluxci are reused."""
    out = Path(path)
    if out.exists():
        if not (out / OUTPUT_MARKER).exists() and any(out.iterdir()):
            raise CIError(f"{out} exists and was not created by fluxci; choose another output directory")
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / OUTPUT_MARKER).write_text("generated by fluxci\n")
    return out


def repo_files(repo: Path) -> list[str]:
    """Tracked and untracked (not ignored) files, relative to the repository root."""
    p = subprocess.run(["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
                       cwd=repo, capture_output=True)
    if p.returncode == 0:
        names = {f for f in p.stdout.decode().split("\0") if f}
        return sorted(f for f in names if (repo / f).is_file())
    return sorted(str(f.relative_to(repo)) for f in repo.rglob("*")
                  if f.is_file() and ".git" not in f.parts)


def _esc_data(s: str) -> str:
    return s.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _esc_prop(s: str) -> str:
    return _esc_data(s).replace(":", "%3A").replace(",", "%2C")


class Report:
    """Collects findings and prints them as GitHub annotations and a step summary."""

    def __init__(self, title: str):
        self.title = title
        self.errors: list[tuple[str, str | None]] = []
        self.warnings: list[tuple[str, str | None]] = []
        self.notes: list[str] = []

    def error(self, msg: str, file: str | None = None) -> None:
        self.errors.append((msg, file))

    def warning(self, msg: str, file: str | None = None) -> None:
        self.warnings.append((msg, file))

    def note(self, msg: str) -> None:
        self.notes.append(msg)

    def finish(self, *, lenient: bool = False) -> int:
        gha = os.environ.get("GITHUB_ACTIONS") == "true"
        errors = [] if lenient else self.errors
        warnings = self.warnings + (self.errors if lenient else [])
        for level, items in (("error", errors), ("warning", warnings)):
            for msg, file in items:
                if gha:
                    props = [f"title={_esc_prop('fluxci ' + self.title)}"]
                    if file:
                        props.insert(0, f"file={_esc_prop(file)}")
                    print(f"::{level} {','.join(props)}::{_esc_data(msg)}")
                else:
                    where = f"{file}: " if file else ""
                    print(f"{level.upper()}: {where}{msg}")
        for n in self.notes:
            print(f"note: {n}")
        status = "failed" if errors else "passed"
        print(f"fluxci {self.title}: {status}, {len(errors)} errors, {len(warnings)} warnings")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as fh:
                fh.write(self._markdown(errors, warnings))
        return 1 if errors else 0

    def _markdown(self, errors, warnings) -> str:
        icon = ":x:" if errors else (":warning:" if warnings else ":white_check_mark:")
        lines = [f"### {icon} fluxci {self.title}", ""]
        for label, items in (("Errors", errors), ("Warnings", warnings)):
            if not items:
                continue
            lines.append(f"**{label}** ({len(items)})")
            lines.append("")
            for msg, file in items[:100]:
                where = f"`{file}`: " if file else ""
                lines.append(f"- {where}{msg}")
            if len(items) > 100:
                lines.append(f"- ... and {len(items) - 100} more")
            lines.append("")
        for n in self.notes:
            lines.append(f"- {n}")
        lines.append("")
        return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------


def load_config(repo: Path) -> dict:
    path = repo / CONFIG_FILE
    if not path.exists():
        raise CIError(f"{CONFIG_FILE} not found in {repo}")
    cfg = load_yaml(path.read_text()) or {}
    for key in ("bootstrap", "gitRepository", "kubernetesVersion"):
        if not cfg.get(key):
            raise CIError(f"{CONFIG_FILE}: '{key}' is required")
    cfg["_repo"] = str(repo)
    return cfg


def load_substitutions(repo: Path) -> dict:
    path = repo / SUBSTITUTIONS_FILE
    data = (load_yaml(path.read_text()) if path.exists() else None) or {}
    return {"secrets": data.get("secrets") or {}, "external": data.get("external") or {}}


def api_versions(cfg: dict) -> list[str]:
    name = cfg.get("apiVersionsFile")
    if not name:
        return []
    path = Path(cfg["_repo"]) / name
    return [line.strip() for line in path.read_text().splitlines()
            if line.strip() and not line.startswith("#")]


# --------------------------------------------------------------------------
# build
# --------------------------------------------------------------------------


def generated_resources(path: Path) -> list[Path]:
    """What Flux lists in the kustomization.yaml it generates for a plain directory."""
    found: list[Path] = []

    def walk(d: Path) -> None:
        for entry in sorted(d.iterdir()):
            if entry.name.startswith("."):
                continue
            if entry.is_dir():
                if any((entry / f).exists() for f in KUSTOMIZATION_FILES):
                    found.append(entry)
                else:
                    walk(entry)
            elif entry.suffix in (".yaml", ".yml"):
                try:
                    docs = load_docs(entry.read_text(), str(entry))
                except CIError:
                    continue
                if any(d.get("apiVersion") and d.get("kind") for d in docs):
                    found.append(entry)

    walk(path)
    return found


OVERLAY_FIELDS = ("targetNamespace", "patches", "images", "components",
                  "commonMetadata", "namePrefix", "nameSuffix")


def flux_build(repo: Path, ks: dict, tmp: Path) -> str:
    """kustomize build of a Flux Kustomization, as kustomize-controller does it."""
    spec = ks.get("spec") or {}
    path = (repo / (spec.get("path") or ".")).resolve()
    if repo.resolve() not in (path, *path.parents):
        raise CIError(f"path {spec.get('path')!r} is outside the repository")
    if not path.is_dir():
        raise CIError(f"path {spec.get('path')!r} does not exist")
    has_kfile = any((path / f).exists() for f in KUSTOMIZATION_FILES)
    overlay = {k: spec[k] for k in OVERLAY_FIELDS if spec.get(k)}
    if has_kfile and not overlay:
        return run(kustomize_cmd() + [str(path)], retries=2).stdout
    d = Path(tempfile.mkdtemp(dir=tmp))
    kz: dict[str, Any] = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization"}
    targets = [path] if has_kfile else generated_resources(path)
    kz["resources"] = [os.path.relpath(t, d) for t in targets]
    if overlay.get("targetNamespace"):
        kz["namespace"] = overlay["targetNamespace"]
    for key in ("patches", "images", "namePrefix", "nameSuffix"):
        if overlay.get(key):
            kz[key] = overlay[key]
    if overlay.get("components"):
        kz["components"] = [os.path.relpath(path / c, d) for c in overlay["components"]]
    common = overlay.get("commonMetadata") or {}
    if common.get("labels"):
        kz["labels"] = [{"pairs": common["labels"], "includeSelectors": False}]
    if common.get("annotations"):
        kz["commonAnnotations"] = common["annotations"]
    (d / "kustomization.yaml").write_text(yaml.safe_dump(kz, sort_keys=False))
    return run(kustomize_cmd() + [str(d)], retries=2).stdout


def substitution_disabled(doc: dict) -> bool:
    return (annotations(doc).get(SUBSTITUTE_KEY) == "disabled"
            or labels(doc).get(SUBSTITUTE_KEY) == "disabled")


def resolve_vars(kid: str, ks: dict, pool: dict, subs: dict, report: Report) -> dict[str, str]:
    """Variables kustomize-controller would substitute for this Kustomization."""
    ns = ns_of(ks) or "flux-system"
    pb = (ks.get("spec") or {}).get("postBuild") or {}
    variables: dict[str, str] = {}
    for ref in pb.get("substituteFrom") or []:
        kind, name = ref.get("kind"), ref.get("name")
        rid = obj_id(ns, name)
        obj = pool.get((kind, ns, name))
        if obj is None:
            if rid in subs["external"]:
                data = {k: str(v) for k, v in (subs["external"][rid] or {}).items()}
            elif ref.get("optional"):
                report.note(f"{kid}: optional substituteFrom {kind} {rid} is not in the repository")
                continue
            else:
                report.error(f"Kustomization {kid}: substituteFrom {kind} {rid} is not defined in the "
                             f"repository; if it is created outside Git, add it under 'external' in "
                             f"{SUBSTITUTIONS_FILE}")
                continue
        elif kind == "ConfigMap":
            data = {k: str(v) for k, v in (obj.get("data") or {}).items()}
        elif is_sops(obj):
            keys = secret_keys(obj)
            placeholders = subs["secrets"].get(rid) or {}
            missing = [k for k in keys if k not in placeholders]
            stale = [k for k in placeholders if k not in keys]
            if missing:
                report.error(f"Kustomization {kid}: Secret {rid} defines {', '.join(missing)}; add a "
                             f"placeholder for each key under secrets.{rid} in {SUBSTITUTIONS_FILE}")
            if stale:
                report.error(f"Kustomization {kid}: {SUBSTITUTIONS_FILE} has placeholders for "
                             f"{', '.join(stale)}, which Secret {rid} does not define; remove them, or "
                             f"CI would accept variables that the cluster does not have")
            data = {k: str(placeholders[k]) for k in keys if k in placeholders}
        else:
            data = secret_data(obj)
        for key, value in data.items():
            if not VAR_NAME.match(key):
                report.error(f"Kustomization {kid}: key {key!r} of {kind} {rid} is not a valid variable "
                             f"name, and Flux rejects it")
                continue
            variables[key] = value
    for key, value in (pb.get("substitute") or {}).items():
        variables[str(key)] = str(value)
    return variables


def substitute(kid: str, docs: list[dict], variables: dict[str, str], report: Report) -> list[dict]:
    """postBuild substitution with flux envsubst --strict, the library Flux uses."""
    flux = tool("flux")
    env = {"PATH": os.environ.get("PATH", ""), "HOME": os.environ.get("HOME", "/tmp"), **variables}
    idx = [i for i, d in enumerate(docs) if not is_sops(d) and not substitution_disabled(d)]
    if not idx:
        return docs
    p = subprocess.run([flux, "envsubst", "--strict"], input=dump_docs([docs[i] for i in idx]),
                       env=env, capture_output=True, text=True)
    if p.returncode == 0:
        new = load_docs(p.stdout, f"substitution of {kid}")
        if len(new) != len(idx):
            raise CIError(f"{kid}: substitution changed the number of objects")
        out = list(docs)
        for j, i in enumerate(idx):
            out[i] = new[j]
        return out
    # Run each object on its own to point at the ones that fail.
    for i in idx:
        q = subprocess.run([flux, "envsubst", "--strict"], input=dump_docs([docs[i]]),
                           env=env, capture_output=True, text=True)
        if q.returncode != 0:
            msg = (q.stderr or q.stdout).strip().lstrip("✗ ").strip()
            report.error(f"Kustomization {kid}: {ident_str(ident(docs[i]))}: postBuild substitution "
                         f"fails: {msg}. Without strict mode Flux would silently replace it with an "
                         f"empty string; escape it as $${{...}} if the application expands it")
    return docs


def cmd_build(args: argparse.Namespace) -> int:
    repo = Path(args.repo).resolve()
    cfg_repo = Path(args.config_repo).resolve() if args.config_repo else repo
    cfg = load_config(cfg_repo)
    subs = load_substitutions(cfg_repo)
    out = prepare_out(args.out)
    report = Report(f"build ({args.label})")

    boot_path = repo / cfg["bootstrap"]
    boot_docs = load_docs(boot_path.read_text(), cfg["bootstrap"])
    self_repo = cfg["gitRepository"]
    queue = [d for d in boot_docs if is_flux_ks(d)]
    if not queue:
        raise CIError(f"{cfg['bootstrap']} has no Flux Kustomization")
    builds: dict[str, dict] = {}
    with tempfile.TemporaryDirectory() as tmp:
        while queue:
            ks = queue.pop(0)
            kid = obj_id(ns_of(ks), name_of(ks))
            if kid in builds:
                report.error(f"Kustomization {kid} is defined more than once")
                continue
            entry = {"ks": ks, "docs": [], "external": False, "vars": []}
            builds[kid] = entry
            src = (ks.get("spec") or {}).get("sourceRef") or {}
            src_id = obj_id(src.get("namespace") or ns_of(ks), src.get("name"))
            if src.get("kind") != "GitRepository" or src_id != self_repo:
                entry["external"] = True
                report.warning(f"Kustomization {kid} uses {src.get('kind')} {src_id}, not this "
                               f"repository: its content is not validated")
                continue
            try:
                entry["docs"] = load_docs(flux_build(repo, ks, Path(tmp)), kid)
            except CIError as e:
                report.error(f"Kustomization {kid}: build failed: {e}")
                continue
            queue.extend(d for d in entry["docs"] if is_flux_ks(d))

    pool: dict[tuple, dict] = {}
    for d in boot_docs + [d for e in builds.values() for d in e["docs"]]:
        if api_group(d) == "" and d.get("kind") in ("ConfigMap", "Secret"):
            pool[(d["kind"], ns_of(d), name_of(d))] = d
    # Plaintext Secrets are judged before substitution, while ${VAR} references
    # are still visible.
    plaintext = [{"kustomization": kid, "object": ident_str(ident(d)), "keys": plaintext_keys(d)}
                 for kid, e in builds.items() for d in e["docs"] if plaintext_keys(d)]
    for kid, entry in builds.items():
        spec = entry["ks"].get("spec") or {}
        if not spec.get("postBuild") or not entry["docs"]:
            continue
        variables = resolve_vars(kid, entry["ks"], pool, subs, report)
        entry["vars"] = sorted(variables)
        entry["docs"] = substitute(kid, entry["docs"], variables, report)

    (out / "ks").mkdir()
    index = {
        "repo": str(repo),
        "configRepo": str(cfg_repo),
        "bootstrap": cfg["bootstrap"],
        "kustomizations": {},
        "plaintextSecrets": plaintext,
    }
    for kid, entry in builds.items():
        (out / "ks" / f"{file_key(kid)}.yaml").write_text(dump_docs(entry["docs"]))
        index["kustomizations"][kid] = {
            "spec": entry["ks"].get("spec") or {},
            "external": entry["external"],
            "objects": len(entry["docs"]),
            "sops": sum(1 for d in entry["docs"] if is_sops(d)),
            "vars": entry["vars"],
        }
    (out / "build.json").write_text(json.dumps(index, indent=2, sort_keys=True))
    total = sum(len(e["docs"]) for e in builds.values())
    report.note(f"{len(builds)} Kustomizations, {total} objects")
    return report.finish(lenient=args.lenient)


class Build:
    """A build directory written by `fluxci build`."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        index_file = self.path / "build.json"
        if not index_file.exists():
            raise CIError(f"{self.path} is not a fluxci build directory")
        self.index = json.loads(index_file.read_text())
        self.repo = Path(self.index["repo"])
        self.docs: dict[str, list[dict]] = {}
        for kid in self.index["kustomizations"]:
            f = self.path / "ks" / f"{file_key(kid)}.yaml"
            self.docs[kid] = load_docs(f.read_text(), str(f)) if f.exists() else []
        cfg_repo = Path(self.index.get("configRepo") or self.repo)
        self.cfg = load_config(cfg_repo)
        self.boot_docs = load_docs((self.repo / self.index["bootstrap"]).read_text())

    def all_docs(self) -> list[tuple[str, dict]]:
        return [(kid, d) for kid, docs in self.docs.items() for d in docs]

    def objects(self) -> dict[Ident, dict]:
        out = {ident(d): d for d in self.boot_docs}
        for _, d in self.all_docs():
            out[ident(d)] = d
        return out

    def owners(self) -> dict[Ident, str]:
        return {ident(d): kid for kid, d in self.all_docs()}

    def pool(self) -> dict[tuple, dict]:
        out = {}
        for d in self.boot_docs + [d for _, d in self.all_docs()]:
            if api_group(d) == "" and d.get("kind") in ("ConfigMap", "Secret"):
                out[(d["kind"], ns_of(d), name_of(d))] = d
        return out

    def helmreleases(self) -> dict[str, tuple[str, dict]]:
        return {obj_id(ns_of(d), name_of(d)): (kid, d) for kid, d in self.all_docs() if is_hr(d)}


# --------------------------------------------------------------------------
# check
# --------------------------------------------------------------------------


def leaf_paths(node: Any, path: tuple = ()) -> list[tuple[tuple, Any]]:
    if isinstance(node, dict):
        return [x for k, v in node.items() for x in leaf_paths(v, path + (str(k),))]
    if isinstance(node, list):
        return [x for i, v in enumerate(node) for x in leaf_paths(v, path + (str(i),))]
    return [(path, node)]


def sops_selected(path: tuple, sops_meta: dict) -> bool:
    """Whether SOPS encrypts the value at this path, given the file's settings."""
    keys = [p for p in path if not p.isdigit()]
    if sops_meta.get("encrypted_regex"):
        rx = re.compile(sops_meta["encrypted_regex"])
        return any(rx.search(k) for k in keys)
    if sops_meta.get("unencrypted_regex"):
        rx = re.compile(sops_meta["unencrypted_regex"])
        return not any(rx.search(k) for k in keys)
    if sops_meta.get("encrypted_suffix"):
        return any(k.endswith(sops_meta["encrypted_suffix"]) for k in keys)
    suffix = sops_meta.get("unencrypted_suffix") or "_unencrypted"
    return not any(k.endswith(suffix) for k in keys)


def is_encrypted_value(value: Any) -> bool:
    return value is None or (isinstance(value, str) and value.startswith("ENC["))


def check_sops(repo: Path, files: list[str], report: Report) -> None:
    rules_file = repo / ".sops.yaml"
    rules = (load_yaml(rules_file.read_text()) or {}).get("creation_rules", []) if rules_file.exists() else []
    for f in files:
        if not f.endswith(".sops.yaml") or Path(f).name == ".sops.yaml":
            continue
        try:
            docs = load_docs((repo / f).read_text(), f)
        except CIError as e:
            report.error(str(e), f)
            continue
        rule = next((r for r in rules if re.search(r.get("path_regex", "$^"), f)), None)
        if rule is None:
            report.error("no creation rule in .sops.yaml matches this file, so `sops` cannot "
                         "re-encrypt it with the right keys", f)
        for d in docs:
            what = ident_str(ident(d))
            sops_meta = d.get("sops")
            if not isinstance(sops_meta, dict) or not sops_meta.get("mac"):
                report.error(f"{what} is not encrypted with SOPS", f)
                continue
            plain = [".".join(p) for p, v in leaf_paths({k: v for k, v in d.items() if k != "sops"})
                     if sops_selected(p, sops_meta) and not is_encrypted_value(v)]
            if d.get("kind") == "Secret":
                plain += [".".join(p) for key in ("data", "stringData")
                          for p, v in leaf_paths(d.get(key) or {}, (key,)) if not is_encrypted_value(v)]
            for p in sorted(set(plain)):
                report.error(f"{what}: {p} is stored in plaintext", f)
            if rule and rule.get("encrypted_regex") and sops_meta.get("encrypted_regex") != rule["encrypted_regex"]:
                report.warning(f"{what}: encrypted_regex {sops_meta.get('encrypted_regex')!r} differs from "
                               f"the .sops.yaml rule {rule['encrypted_regex']!r}; `sops edit` keeps the "
                               f"file's regex, so a new key may be saved in plaintext", f)
            want = {r.strip() for r in str((rule or {}).get("age") or "").split(",") if r.strip()}
            if isinstance((rule or {}).get("age"), list):
                want = {str(r) for r in rule["age"]}
            have = {str(a.get("recipient")) for a in sops_meta.get("age") or []}
            if want and have != want:
                report.warning(f"{what}: age recipients differ from the .sops.yaml rule; run "
                               f"`sops updatekeys {f}`", f)


def check_plaintext_secrets(repo: Path, files: list[str], build: Build, cfg: dict, report: Report) -> None:
    skip = tuple(cfg.get("secretScanIgnore") or [])
    in_files = set()
    for f in files:
        if not f.endswith((".yaml", ".yml")) or f.endswith(".sops.yaml") or f.startswith(skip):
            continue
        try:
            docs = load_docs((repo / f).read_text(), f)
        except (CIError, UnicodeDecodeError):
            continue
        for d in docs:
            keys = plaintext_keys(d)
            if keys:
                in_files.add(ident_str(ident(d)))
                report.error(f"{ident_str(ident(d))} holds plaintext values ({', '.join(keys)}) in a "
                             f"public repository; encrypt it with SOPS in a *.sops.yaml file", f)
    for p in build.index.get("plaintextSecrets") or []:
        if p["object"] not in in_files:
            report.error(f"Kustomization {p['kustomization']} produces {p['object']} with plaintext "
                         f"values ({', '.join(p['keys'])}), for example from a secretGenerator")


def check_references(build: Build, report: Report) -> None:
    objects = build.objects()
    ks_ids = {obj_id(i[2], i[3]) for i in objects if i[0] == KS_GROUP and i[1] == "Kustomization"}
    hr_ids = {obj_id(i[2], i[3]) for i in objects if i[0] == HR_GROUP and i[1] == "HelmRelease"}
    for kid, meta_ in build.index["kustomizations"].items():
        spec = meta_["spec"]
        ns = kid.split("/")[0]
        for dep in spec.get("dependsOn") or []:
            did = obj_id(dep.get("namespace") or ns, dep.get("name"))
            if did not in ks_ids:
                report.error(f"Kustomization {kid} depends on {did}, which does not exist")
        if meta_["sops"] and ((spec.get("decryption") or {}).get("provider") != "sops"):
            report.error(f"Kustomization {kid} contains {meta_['sops']} SOPS-encrypted objects but has "
                         f"no `decryption: {{provider: sops}}`; Flux would apply the ciphertext")
    for hid, (kid, hr) in build.helmreleases().items():
        spec = hr.get("spec") or {}
        hns = ns_of(hr)
        if spec.get("chartRef"):
            ref = spec["chartRef"]
            refs = [(ref.get("kind"), ref.get("namespace") or hns, ref.get("name"))]
        else:
            sref = ((spec.get("chart") or {}).get("spec") or {}).get("sourceRef") or {}
            refs = [(sref.get("kind"), sref.get("namespace") or hns, sref.get("name"))]
        for kind, sns, sname in refs:
            if (SOURCE_GROUP, kind, sns, sname) not in objects:
                report.error(f"HelmRelease {hid} uses {kind} {sns}/{sname}, which does not exist")
        for dep in spec.get("dependsOn") or []:
            did = obj_id(dep.get("namespace") or hns, dep.get("name"))
            if did not in hr_ids:
                report.error(f"HelmRelease {hid} depends on {did}, which does not exist")
        for vf in spec.get("valuesFrom") or []:
            if not vf.get("optional") and ("", vf.get("kind"), hns, vf.get("name")) not in objects:
                report.error(f"HelmRelease {hid}: valuesFrom {vf.get('kind')} {hns}/{vf.get('name')} "
                             f"does not exist in the repository")


def check_duplicates(build: Build, report: Report) -> None:
    seen: dict[Ident, str] = {}
    for kid, d in build.all_docs():
        i = ident(d)
        if i in seen and seen[i] != kid:
            report.error(f"{ident_str(i)} is applied by both Kustomization {seen[i]} and {kid}; "
                         f"they would fight over it, and pruning one would delete it")
        seen.setdefault(i, kid)


def kustomization_refs(kdir: Path, k: dict) -> list[str]:
    refs: list[str] = []
    for key in ("resources", "components", "crds", "bases"):
        refs += [r for r in k.get(key) or [] if isinstance(r, str)]
    for p in k.get("patches") or []:
        if isinstance(p, dict) and p.get("path"):
            refs.append(p["path"])
    for p in k.get("patchesStrategicMerge") or []:
        if isinstance(p, str) and "\n" not in p:
            refs.append(p)
    for p in k.get("patchesJson6902") or []:
        if isinstance(p, dict) and p.get("path"):
            refs.append(p["path"])
    for gen in (k.get("configMapGenerator") or []) + (k.get("secretGenerator") or []):
        refs += [str(f).split("=", 1)[-1] for f in gen.get("files") or []]
        refs += [str(f) for f in gen.get("envs") or []]
        if gen.get("env"):
            refs.append(str(gen["env"]))
    for r in k.get("replacements") or []:
        if isinstance(r, dict) and r.get("path"):
            refs.append(r["path"])
    return [r for r in refs if "://" not in r and not r.startswith(("github.com/", "git@"))]


def reachable_files(roots: list[Path]) -> set[Path]:
    seen: set[Path] = set()
    files: set[Path] = set()

    def visit(d: Path) -> None:
        d = d.resolve()
        if d in seen or not d.is_dir():
            return
        seen.add(d)
        kfile = next((d / f for f in KUSTOMIZATION_FILES if (d / f).exists()), None)
        if kfile is None:
            for r in generated_resources(d):
                visit(r) if r.is_dir() else files.add(r.resolve())
            return
        files.add(kfile.resolve())
        try:
            k = load_yaml(kfile.read_text()) or {}
        except yaml.YAMLError:
            return
        for ref in kustomization_refs(d, k):
            p = (d / ref).resolve()
            if p.is_dir():
                visit(p)
            elif p.exists():
                files.add(p)

    for r in roots:
        visit(r)
    return files


def check_orphans(repo: Path, files: list[str], build: Build, cfg: dict, report: Report) -> None:
    roots = [repo / (m["spec"].get("path") or ".") for m in build.index["kustomizations"].values()
             if not m["external"]]
    reached = {p.relative_to(repo.resolve()).as_posix() for p in reachable_files(roots)
               if repo.resolve() in p.parents}
    scan = tuple(cfg.get("orphanScan") or [])
    ignore = tuple(cfg.get("orphanIgnore") or [])
    groups: dict[str, list[str]] = {}
    for f in files:
        if f.startswith(scan) and not f.startswith(ignore) and f.endswith((".yaml", ".yml")) and f not in reached:
            groups.setdefault(str(Path(f).parent), []).append(f)
    for d, fs in sorted(groups.items()):
        report.warning(f"{len(fs)} file(s) in {d}/ are not applied by any Flux Kustomization "
                       f"(disabled app?): CI does not validate them", d)


def check_prune_protection(build: Build, cfg: dict, report: Report) -> None:
    level = (cfg.get("pruneProtection") or {}).get("level", "warning")
    kinds = {tuple(k.split("/", 1)) if "/" in k else ("", k)
             for k in (cfg.get("pruneProtection") or {}).get("kinds") or []}
    if level == "off" or not kinds:
        return
    emit = report.error if level == "error" else report.warning
    for kid, d in build.all_docs():
        if (api_group(d), d.get("kind")) not in kinds:
            continue
        if not build.index["kustomizations"][kid]["spec"].get("prune"):
            continue
        if annotations(d).get(PRUNE_KEY) != "disabled":
            emit(f"{ident_str(ident(d))} is in Kustomization {kid} (prune: true) without "
                 f"`{PRUNE_KEY}: disabled`: removing it from Git would delete it")


def cmd_check(args: argparse.Namespace) -> int:
    build = Build(args.build)
    repo = build.repo
    report = Report("check")
    files = repo_files(repo)
    check_sops(repo, files, report)
    check_plaintext_secrets(repo, files, build, build.cfg, report)
    check_references(build, report)
    check_duplicates(build, report)
    check_orphans(repo, files, build, build.cfg, report)
    check_prune_protection(build, build.cfg, report)
    return report.finish()


# --------------------------------------------------------------------------
# plan and render
# --------------------------------------------------------------------------


def release_name(hr: dict) -> str:
    spec = hr.get("spec") or {}
    name = spec.get("releaseName") or ""
    if not name:
        name = name_of(hr)
        if spec.get("targetNamespace"):
            name = f"{spec['targetNamespace']}-{name}"
    if len(name) > 53:
        digest = hashlib.sha256(name.encode()).hexdigest()
        name = f"{name[:40]}-{digest}"[:53]
    return name


def chart_info(hr: dict, objects: dict[Ident, dict], self_repo: str) -> dict:
    spec = hr.get("spec") or {}
    hns = ns_of(hr)
    if spec.get("chartRef"):
        ref = spec["chartRef"]
        kind, sns, sname = ref.get("kind"), ref.get("namespace") or hns, ref.get("name")
        chart, version = "", ""
    else:
        cspec = (spec.get("chart") or {}).get("spec") or {}
        sref = cspec.get("sourceRef") or {}
        kind, sns, sname = sref.get("kind"), sref.get("namespace") or hns, sref.get("name")
        chart, version = str(cspec.get("chart") or ""), str(cspec.get("version") or "")
    src = objects.get((SOURCE_GROUP, kind, sns, sname))
    return {
        "kind": kind,
        "source": obj_id(sns, sname),
        "sourceSpec": (src or {}).get("spec"),
        "chart": chart,
        "version": version,
        "local": kind == "GitRepository" and obj_id(sns, sname) == self_repo,
    }


def hr_fingerprint(hr: dict, info: dict, repo: Path, pool: dict) -> str:
    h = hashlib.sha256()
    values_from = []
    for vf in (hr.get("spec") or {}).get("valuesFrom") or []:
        obj = pool.get((vf.get("kind"), ns_of(hr), vf.get("name")))
        values_from.append(None if obj is None else {"data": obj.get("data"), "stringData": obj.get("stringData"),
                                                     "sops": is_sops(obj)})
    payload = {"metadata": [ns_of(hr), name_of(hr)], "spec": hr.get("spec"),
               "source": info["sourceSpec"], "valuesFrom": values_from}
    h.update(json.dumps(payload, sort_keys=True, default=str).encode())
    if info["local"]:
        chart_dir = (repo / info["chart"]).resolve()
        if chart_dir.is_dir():
            for f in sorted(p for p in chart_dir.rglob("*") if p.is_file()):
                h.update(f.relative_to(chart_dir).as_posix().encode())
                h.update(f.read_bytes())
    return h.hexdigest()


def describe_hrs(build: Build) -> dict[str, dict]:
    objects = build.objects()
    pool = build.pool()
    out = {}
    for hid, (kid, hr) in build.helmreleases().items():
        info = chart_info(hr, objects, build.cfg["gitRepository"])
        out[hid] = {"kustomization": kid, "release": release_name(hr),
                    "namespace": (hr.get("spec") or {}).get("targetNamespace") or ns_of(hr),
                    "chart": info["chart"], "version": info["version"], "sourceKind": info["kind"],
                    "source": info["source"],
                    "fingerprint": hr_fingerprint(hr, info, build.repo, pool)}
    return out


def cmd_plan(args: argparse.Namespace) -> int:
    head = describe_hrs(Build(args.head))
    base = describe_hrs(Build(args.base)) if args.base else None
    full = args.full or base is None
    if full:
        selected = sorted(head)
    else:
        selected = sorted(h for h in head if h not in base or base[h]["fingerprint"] != head[h]["fingerprint"])
    removed = sorted(h for h in (base or {}) if h not in head)
    plan = {"mode": "full" if full else "changed", "selected": selected, "removed": removed,
            "head": head, "base": base or {}}
    Path(args.out).write_text(json.dumps(plan, indent=2, sort_keys=True))
    print(f"plan: mode={plan['mode']}, {len(selected)} of {len(head)} HelmReleases to render, "
          f"{len(removed)} removed")
    for h in selected:
        print(f"  render {h} ({head[h]['chart']} {head[h]['version'] or '(no version)'})")
    for h in removed:
        print(f"  removed {h}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as fh:
            fh.write(f"### fluxci plan\n\nMode `{plan['mode']}`: {len(selected)} of {len(head)} "
                     f"HelmReleases rendered, {len(removed)} removed.\n\n")
            for h in selected:
                fh.write(f"- `{h}`: {head[h]['chart']} {head[h]['version'] or '(no version)'}\n")
            fh.write("\n")
    return 0


class ChartCache:
    """Charts fetched once per run (and across runs when the directory is cached)."""

    def __init__(self, root: Path):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.mem: dict[str, Path] = {}

    def get(self, key: str, fetch: Callable[[Path], Path], cacheable: bool) -> Path:
        if key in self.mem:
            return self.mem[key]
        d = self.root / hashlib.sha256(key.encode()).hexdigest()[:24]
        done = d / ".complete"
        if cacheable and done.exists():
            path = Path(done.read_text().strip())
            if path.exists():
                self.mem[key] = path
                return path
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        path = fetch(d)
        done.write_text(str(path))
        self.mem[key] = path
        return path


EXACT_VERSION = re.compile(r"^v?\d+\.\d+\.\d+([-+][0-9A-Za-z.+-]+)?$")


def git_fetch(url: str, ref: dict, subpath: str, dest: Path) -> None:
    if ref.get("semver"):
        raise CIError("GitRepository semver refs are not supported by fluxci")
    target = (ref.get("commit") or ref.get("name")
              or (f"refs/tags/{ref['tag']}" if ref.get("tag") else f"refs/heads/{ref.get('branch') or 'master'}"))
    git = [tool("git"), "-C", str(dest), "-c", "advice.detachedHead=false"]
    run([tool("git"), "init", "-q", str(dest)])
    run(git + ["remote", "add", "origin", url])
    run(git + ["sparse-checkout", "set", subpath.strip("./") or "."])
    run(git + ["fetch", "-q", "--depth", "1", "--filter=blob:none", "origin", target], retries=2)
    run(git + ["checkout", "-q", "FETCH_HEAD"], retries=1)


def fetch_chart(info: dict, repo: Path, cache: ChartCache) -> Path:
    spec = info["sourceSpec"]
    if spec is None:
        raise CIError(f"{info['kind']} {info['source']} not found")
    if (spec.get("secretRef") or spec.get("certSecretRef")) and not info["local"]:
        raise CIError(f"{info['kind']} {info['source']} needs credentials, which CI does not have")
    helm = tool("helm")
    kind = info["kind"]
    if kind == "HelmRepository":
        url = str(spec["url"]).rstrip("/")
        oci = spec.get("type") == "oci" or url.startswith("oci://")
        version = info["version"]

        def fetch(d: Path) -> Path:
            cmd = [helm, "pull", f"{url}/{info['chart']}"] if oci else [helm, "pull", info["chart"], "--repo", url]
            cmd += ["--destination", str(d)] + (["--version", version] if version else [])
            run(cmd, retries=2)
            return next(d.glob("*.tgz"))

        return cache.get(f"helm|{url}|{info['chart']}|{version}", fetch, bool(EXACT_VERSION.match(version)))
    if kind == "OCIRepository":
        url = str(spec["url"]).rstrip("/")
        ref = spec.get("ref") or {}

        def fetch_oci(d: Path) -> Path:
            if ref.get("digest"):
                cmd = [helm, "pull", f"{url}@{ref['digest']}"]
            else:
                cmd = [helm, "pull", url] + (["--version", str(ref.get("tag") or ref.get("semver"))]
                                             if (ref.get("tag") or ref.get("semver")) else [])
            run(cmd + ["--destination", str(d)], retries=2)
            return next(d.glob("*.tgz"))

        exact = bool(ref.get("digest")) or bool(EXACT_VERSION.match(str(ref.get("tag") or "")))
        return cache.get(f"oci|{url}|{json.dumps(ref, sort_keys=True)}", fetch_oci, exact)
    if kind == "GitRepository":
        sub = info["chart"]
        if info["local"]:
            path = (repo / sub).resolve()
            if not (path / "Chart.yaml").exists():
                raise CIError(f"local chart {sub} not found in the repository")
            return path

        def fetch_git(d: Path) -> Path:
            git_fetch(str(spec["url"]), spec.get("ref") or {}, sub, d / "src")
            return (d / "src" / sub).resolve()

        ref = spec.get("ref") or {}
        path = cache.get(f"git|{spec['url']}|{json.dumps(ref, sort_keys=True)}|{sub}", fetch_git,
                         bool(ref.get("commit")))
        if not (path / "Chart.yaml").exists():
            raise CIError(f"chart {sub} not found in {spec['url']}")
        return path
    raise CIError(f"chart source kind {kind} is not supported by fluxci")


def set_target_path(values: dict, target: str, value: str) -> None:
    parts = [p for p in re.split(r"(?<!\\)\.", target) if p]
    node = values
    for p in parts[:-1]:
        node = node.setdefault(p.replace("\\.", "."), {})
    node[parts[-1].replace("\\.", ".")] = value


def compose_values(hid: str, hr: dict, pool: dict, report: Report) -> dict:
    spec = hr.get("spec") or {}
    hns = ns_of(hr)
    result: dict = {}
    for vf in spec.get("valuesFrom") or []:
        kind, vname = vf.get("kind"), vf.get("name")
        key = vf.get("valuesKey") or "values.yaml"
        obj = pool.get((kind, hns, vname))
        if obj is None:
            if vf.get("optional"):
                continue
            raise CIError(f"valuesFrom {kind} {hns}/{vname} not found")
        if kind == "Secret" and is_sops(obj):
            report.warning(f"HelmRelease {hid}: valuesFrom Secret {hns}/{vname} is SOPS-encrypted; "
                           f"rendered without it")
            continue
        data = {k: str(v) for k, v in (obj.get("data") or {}).items()} if kind == "ConfigMap" else secret_data(obj)
        if key not in data:
            if vf.get("optional"):
                continue
            raise CIError(f"valuesFrom {kind} {hns}/{vname} has no key {key!r}")
        if vf.get("targetPath"):
            set_target_path(result, vf["targetPath"], data[key])
        else:
            loaded = load_yaml(data[key]) or {}
            if not isinstance(loaded, dict):
                raise CIError(f"valuesFrom {kind} {hns}/{vname} key {key!r} is not a YAML map")
            result = deep_merge(result, loaded)
    return deep_merge(result, spec.get("values") or {})


def post_render(hr: dict, docs: list[dict]) -> list[dict]:
    """User post-renderers, then the origin labels helm-controller adds."""
    spec = hr.get("spec") or {}
    for pr in spec.get("postRenderers") or []:
        k = (pr or {}).get("kustomize")
        if not k:
            continue
        with tempfile.TemporaryDirectory() as t:
            tdir = Path(t)
            (tdir / "rendered.yaml").write_text(dump_docs(docs))
            kz: dict[str, Any] = {"apiVersion": "kustomize.config.k8s.io/v1beta1", "kind": "Kustomization",
                                  "resources": ["rendered.yaml"]}
            if k.get("patches"):
                kz["patches"] = k["patches"]
            if k.get("images"):
                kz["images"] = k["images"]
            if k.get("patchesStrategicMerge"):
                kz["patchesStrategicMerge"] = [p if isinstance(p, str) else yaml.safe_dump(p)
                                               for p in k["patchesStrategicMerge"]]
            if k.get("patchesJson6902"):
                kz["patchesJson6902"] = [{"target": p["target"], "patch": yaml.safe_dump(p["patch"])}
                                         for p in k["patchesJson6902"]]
            (tdir / "kustomization.yaml").write_text(yaml.safe_dump(kz, sort_keys=False))
            docs = load_docs(run(kustomize_cmd() + [str(tdir)]).stdout, f"post-render of {name_of(hr)}")
    common = spec.get("commonMetadata") or {}
    for d in docs:
        if is_hook(d):
            continue
        md = d.setdefault("metadata", {})
        lbl = dict(md.get("labels") or {})
        lbl.update(common.get("labels") or {})
        lbl[ORIGIN_NAME] = name_of(hr)
        lbl[ORIGIN_NAMESPACE] = ns_of(hr)
        md["labels"] = lbl
        if common.get("annotations"):
            md["annotations"] = {**(md.get("annotations") or {}), **common["annotations"]}
    return docs


CLUSTER_SCOPED = {
    "APIService", "CertificateSigningRequest", "ClusterIssuer", "ClusterRole", "ClusterRoleBinding",
    "CSIDriver", "CSINode", "CustomResourceDefinition", "FlowSchema", "GatewayClass", "IngressClass",
    "MutatingWebhookConfiguration", "Namespace", "Node", "PersistentVolume", "PriorityClass",
    "PriorityLevelConfiguration", "RuntimeClass", "StorageClass", "ValidatingAdmissionPolicy",
    "ValidatingAdmissionPolicyBinding", "ValidatingWebhookConfiguration", "VolumeAttachment",
    "VolumeSnapshotClass",
}


def fill_namespace(docs: list[dict], namespace: str, crds: list[dict]) -> None:
    """Set the release namespace on namespaced objects that leave it out.

    Helm creates them in the release namespace anyway; making it explicit keeps
    object identities stable for the guard, the diff and policy messages.
    """
    cluster_kinds = CLUSTER_SCOPED | {str(((c.get("spec") or {}).get("names") or {}).get("kind"))
                                      for c in crds if (c.get("spec") or {}).get("scope") == "Cluster"}
    for d in docs:
        if not ns_of(d) and d.get("kind") not in cluster_kinds:
            d.setdefault("metadata", {})["namespace"] = namespace


def chart_version(chart_path: Path) -> str:
    """Version from Chart.yaml, for a chart directory or a packaged .tgz."""
    try:
        if chart_path.is_dir():
            return str((load_yaml((chart_path / "Chart.yaml").read_text()) or {}).get("version") or "")
        with tarfile.open(chart_path) as tar:
            member = next(m for m in tar.getmembers() if m.name.count("/") == 1 and m.name.endswith("/Chart.yaml"))
            return str((load_yaml(tar.extractfile(member).read().decode()) or {}).get("version") or "")
    except (OSError, StopIteration, tarfile.TarError, yaml.YAMLError, AttributeError):
        return ""


def render_hr(hid: str, hr: dict, build: Build, cache: ChartCache, apis: list[str],
              report: Report) -> tuple[list[dict], list[dict], dict]:
    spec = hr.get("spec") or {}
    info = chart_info(hr, build.objects(), build.cfg["gitRepository"])
    chart_path = fetch_chart(info, build.repo, cache)
    values = compose_values(hid, hr, build.pool(), report)
    release = release_name(hr)
    namespace = spec.get("targetNamespace") or ns_of(hr)
    with tempfile.TemporaryDirectory() as t:
        vfile = Path(t) / "values.json"
        vfile.write_text(json.dumps(values))
        cmd = [tool("helm"), "template", release, str(chart_path), "--namespace", namespace,
               "--kube-version", str(build.cfg["kubernetesVersion"]), "--values", str(vfile)]
        if not (spec.get("test") or {}).get("enable"):
            cmd.append("--skip-tests")
        if any((spec.get(a) or {}).get("disableSchemaValidation") for a in ("install", "upgrade")):
            cmd.append("--skip-schema-validation")
        for a in apis:
            cmd += ["--api-versions", a]
        docs = load_docs(run(cmd).stdout, f"helm template {hid}")
        with_crds = load_docs(run(cmd + ["--include-crds"]).stdout, f"helm template {hid}")
    templated = {ident(d) for d in docs}
    crds_dir = [d for d in with_crds if is_crd(d) and ident(d) not in templated]
    if (spec.get("install") or {}).get("crds", "Create") != "Skip":
        docs = crds_dir + docs
    docs = post_render(hr, docs)
    crds = [d for d in docs if is_crd(d)] + [d for d in crds_dir if ident(d) not in {ident(x) for x in docs}]
    fill_namespace(docs, namespace, crds)
    version = chart_version(chart_path) or info["version"] or "?"
    return docs, crds, {"chart": info["chart"] or info["source"], "version": str(version),
                        "release": release, "namespace": namespace}


def cmd_render(args: argparse.Namespace) -> int:
    build = Build(args.build)
    plan = json.loads(Path(args.plan).read_text())
    out = prepare_out(args.out)
    (out / "crds").mkdir()
    report = Report(f"render ({args.side})")
    cache = ChartCache(Path(args.cache) if args.cache else Path(tempfile.mkdtemp(prefix="fluxci-charts-")))
    apis = api_versions(build.cfg)
    hrs = build.helmreleases()
    targets = plan["selected"] + (plan["removed"] if args.side == "base" else [])
    allowed = set((build.cfg.get("render") or {}).get("allowFailure") or [])
    results = {}
    for hid in targets:
        if hid not in hrs:
            continue
        try:
            docs, crds, info = render_hr(hid, hrs[hid][1], build, cache, apis, report)
        except CIError as e:
            results[hid] = {"status": "error", "error": str(e)}
            msg = f"HelmRelease {hid}: render failed: {e}"
            if args.side == "head" and hid not in allowed:
                report.error(msg)
            else:
                report.warning(msg + (" (listed in render.allowFailure)" if hid in allowed else ""))
            continue
        (out / f"{file_key(hid)}.yaml").write_text(dump_docs(docs))
        if crds:
            (out / "crds" / f"{file_key(hid)}.yaml").write_text(dump_docs(crds))
        results[hid] = {"status": "ok", "objects": len(docs), **info}
        print(f"rendered {hid}: {info['chart']} {info['version']}, {len(docs)} objects")
    (out / "render.json").write_text(json.dumps(results, indent=2, sort_keys=True))
    return report.finish(lenient=args.side == "base")


# --------------------------------------------------------------------------
# schemas, bundle and validate
# --------------------------------------------------------------------------


def to_json_schema(node: Any, strict: bool) -> Any:
    """OpenAPI v3 (CRD) schema to JSON schema, as openapi2jsonschema does."""
    if isinstance(node, list):
        return [to_json_schema(x, strict) for x in node]
    if not isinstance(node, dict):
        return node
    out: dict = {}
    for k, v in node.items():
        if k in ("properties", "patternProperties", "definitions") and isinstance(v, dict):
            out[k] = {pk: to_json_schema(pv, strict) for pk, pv in v.items()}
        elif k in ("items", "additionalProperties", "not") and isinstance(v, dict):
            out[k] = to_json_schema(v, strict)
        elif k in ("allOf", "anyOf", "oneOf") and isinstance(v, list):
            out[k] = [to_json_schema(x, strict) for x in v]
        else:
            out[k] = v
    if out.get("x-kubernetes-int-or-string"):
        out.pop("type", None)
        if "anyOf" not in out and "oneOf" not in out:
            out["anyOf"] = [{"type": "string"}, {"type": "integer"}]
    if out.get("nullable") is True:
        t = out.get("type")
        if isinstance(t, str):
            out["type"] = [t, "null"]
        elif isinstance(t, list) and "null" not in t:
            out["type"] = t + ["null"]
    if (strict and out.get("type") == "object" and "properties" in out
            and "additionalProperties" not in out and not out.get("x-kubernetes-preserve-unknown-fields")):
        out["additionalProperties"] = False
    return out


def crd_schemas(crd: dict, strict: bool) -> list[tuple[str, str, str, dict]]:
    spec = crd.get("spec") or {}
    group = spec.get("group") or ""
    kind = str((spec.get("names") or {}).get("kind") or "").lower()
    out = []
    for v in spec.get("versions") or []:
        schema = ((v.get("schema") or {}).get("openAPIV3Schema"))
        if not schema or not group or not kind:
            continue
        js = to_json_schema(copy.deepcopy(schema), strict)
        props = js.setdefault("properties", {})
        props.setdefault("apiVersion", {"type": "string"})
        props.setdefault("kind", {"type": "string"})
        props.setdefault("metadata", {"type": "object"})
        out.append((group, kind, str(v.get("name")), js))
    return out


def yaml_files(paths: list[str]) -> list[Path]:
    out: list[Path] = []
    for p in map(Path, paths):
        if p.is_dir():
            out += sorted(x for x in p.rglob("*") if x.suffix in (".yaml", ".yml"))
        elif p.exists():
            out.append(p)
    return out


def cmd_schemas(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.repo).resolve())
    out = prepare_out(args.out)
    sources = list(args.inputs) + [str(Path(cfg["_repo"]) / f) for f in cfg.get("crdFiles") or []]
    strict = (cfg.get("kubeconform") or {}).get("strictCRDs", True)
    count = 0
    for f in yaml_files(sources):
        for d in load_docs(f.read_text(), str(f)):
            if not is_crd(d):
                continue
            for group, kind, version, schema in crd_schemas(d, strict):
                target = out / group / f"{kind}_{version}.json"
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(json.dumps(schema))
                count += 1
    print(f"schemas: {count} CRD versions written to {out}")
    return 0


def cmd_bundle(args: argparse.Namespace) -> int:
    out = prepare_out(args.out)
    count = 0
    if args.build:
        build = Build(args.build)
        for kid, docs in build.docs.items():
            keep = [d for d in docs if not is_sops(d)]
            if keep:
                (out / f"ks__{file_key(kid)}.yaml").write_text(dump_docs(keep))
                count += len(keep)
    for r in args.render or []:
        for f in sorted(Path(r).glob("*.yaml")):
            docs = load_docs(f.read_text(), str(f))
            if docs:
                (out / f"hr__{f.name}").write_text(dump_docs(docs))
                count += len(docs)
    print(f"bundle: {count} objects in {out}")
    return 0


def kubeconform_report(data: dict, allowed_missing: set[str], ignore: list[dict], report: Report) -> None:
    def ignored(kind: str, message: str) -> bool:
        return any(rule.get("kind") in (None, kind) and str(rule.get("message", "")) in message for rule in ignore)

    missing: dict[str, int] = {}
    for r in data.get("resources") or []:
        status = r.get("status")
        where = Path(str(r.get("filename") or "")).name
        kind = str(r.get("kind"))
        what = f"{kind} {r.get('name')}"
        if status in ("statusInvalid", "statusError"):
            errs = [f"{e.get('path') or '/'}: {e.get('msg')}" for e in r.get("validationErrors") or []] \
                or [str(r.get("msg"))]
            kept = [e for e in errs if not ignored(kind, e)]
            if kept:
                report.error(f"{where}: {what}: {'; '.join(kept)}")
            else:
                report.note(f"{where}: {what}: ignored by kubeconform.ignoreErrors: {'; '.join(errs)}")
        elif status == "statusSkipped":
            key = f"{r.get('version')}/{kind}"
            missing[key] = missing.get(key, 0) + 1
    for key, n in sorted(missing.items()):
        if key in allowed_missing:
            report.note(f"no schema for {key} ({n} objects), allowed by kubeconform.allowMissingSchemas")
        else:
            report.error(f"no schema for {key} ({n} objects): ship its CRD in the repository or list it "
                         f"in kubeconform.allowMissingSchemas in {CONFIG_FILE}")


def conftest_report(data: list, report: Report) -> None:
    for entry in data or []:
        where = Path(str(entry.get("filename") or "")).name
        for f in entry.get("failures") or []:
            report.error(f"{where}: {f.get('msg')}")
        for w in entry.get("warnings") or []:
            report.warning(f"{where}: {w.get('msg')}")


def cmd_validate(args: argparse.Namespace) -> int:
    cfg = load_config(Path(args.repo).resolve())
    kc = cfg.get("kubeconform") or {}
    files = sorted(str(f) for f in Path(args.bundle).glob("*.yaml"))
    report = Report(f"validate ({args.label})")
    if not files:
        report.note("nothing to validate")
        return report.finish()
    locations = []
    if args.schemas:
        locations.append(str(Path(args.schemas).resolve()) + "/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json")
    locations.append("default")
    locations += kc.get("extraSchemaLocations") or []
    cmd = [tool("kubeconform"), "-strict", "-ignore-missing-schemas", "-output", "json", "-verbose",
           "-kubernetes-version", str(cfg["kubernetesVersion"])]
    if args.cache:
        Path(args.cache).mkdir(parents=True, exist_ok=True)
        cmd += ["-cache", args.cache]
    for loc in locations:
        cmd += ["-schema-location", loc]
    p = subprocess.run(cmd + files, capture_output=True, text=True)
    try:
        kubeconform_report(json.loads(p.stdout or "{}"), set(kc.get("allowMissingSchemas") or []),
                           kc.get("ignoreErrors") or [], report)
    except json.JSONDecodeError:
        report.error(f"kubeconform did not produce a report (exit {p.returncode}): {p.stderr.strip()[-1500:]}")
    if p.returncode not in (0, 1):
        report.error(f"kubeconform exited with {p.returncode}: {p.stderr.strip()[-1500:]}")
    policy = Path(cfg["_repo"]) / (cfg.get("policyDir") or "policy")
    if not args.no_conftest and policy.is_dir():
        q = subprocess.run([tool("conftest"), "test", "--no-color", "--output", "json", "--all-namespaces",
                            "--policy", str(policy)] + files, capture_output=True, text=True)
        try:
            conftest_report(json.loads(q.stdout or "[]"), report)
        except json.JSONDecodeError:
            report.error(f"conftest did not produce a report (exit {q.returncode}): {q.stderr.strip()[-1500:]}")
    return report.finish()


# --------------------------------------------------------------------------
# guard
# --------------------------------------------------------------------------

PROTECTED_KINDS = {
    ("", "PersistentVolumeClaim"),
    ("", "PersistentVolume"),
    ("", "Namespace"),
    ("apps", "StatefulSet"),
    ("postgresql.cnpg.io", "Cluster"),
    ("storage.k8s.io", "StorageClass"),
    (HR_GROUP, "HelmRelease"),
    (KS_GROUP, "Kustomization"),
}


def _get(doc: dict, *path: str) -> Any:
    node: Any = doc
    for p in path:
        if not isinstance(node, dict):
            return None
        node = node.get(p)
    return node


def _norm_path(p: Any) -> str:
    return os.path.normpath(str(p or ".")).lstrip("./") or "."


def compare_objects(base: dict[Ident, dict], head: dict[Ident, dict],
                    base_owner: dict[Ident, str] | None = None,
                    head_owner: dict[Ident, str] | None = None) -> list[str]:
    """Changes that can delete or orphan data. Each finding is one line."""
    out: list[str] = []
    for i, b in sorted(base.items()):
        group, kind = i[0], i[1]
        what = ident_str(i)
        h = head.get(i)
        if h is None:
            if (group, kind) in PROTECTED_KINDS:
                if kind == "StatefulSet" and not _get(b, "spec", "volumeClaimTemplates"):
                    continue
                out.append(f"{what} is removed")
            continue
        if base_owner and head_owner and (group, kind) in PROTECTED_KINDS and \
                base_owner.get(i) and head_owner.get(i) and base_owner[i] != head_owner[i]:
            out.append(f"{what} moves from Kustomization {base_owner[i]} to {head_owner[i]}: the old "
                       f"Kustomization may prune it before the new one adopts it")
        if annotations(b).get(PRUNE_KEY) == "disabled" and annotations(h).get(PRUNE_KEY) != "disabled":
            out.append(f"{what} loses `{PRUNE_KEY}: disabled`")
        if (group, kind) == ("", "PersistentVolumeClaim"):
            for f in ("storageClassName", "accessModes", "volumeName", "volumeMode", "dataSource", "dataSourceRef"):
                if _get(b, "spec", f) != _get(h, "spec", f):
                    out.append(f"{what}: spec.{f} changes from {_get(b, 'spec', f)!r} to {_get(h, 'spec', f)!r}")
        elif (group, kind) == ("apps", "StatefulSet"):
            if _get(b, "spec", "volumeClaimTemplates") != _get(h, "spec", "volumeClaimTemplates"):
                out.append(f"{what}: volumeClaimTemplates change; the field is immutable, so the "
                           f"StatefulSet has to be recreated")
        elif (group, kind) == ("postgresql.cnpg.io", "Cluster"):
            for f in ("storage", "walStorage", "bootstrap"):
                if _get(b, "spec", f) != _get(h, "spec", f):
                    out.append(f"{what}: spec.{f} changes")
        elif (group, kind) == (KS_GROUP, "Kustomization"):
            if not _get(b, "spec", "prune") and _get(h, "spec", "prune"):
                out.append(f"{what}: prune is enabled")
            if _norm_path(_get(b, "spec", "path")) != _norm_path(_get(h, "spec", "path")):
                out.append(f"{what}: path changes from {_get(b, 'spec', 'path')!r} to "
                           f"{_get(h, 'spec', 'path')!r}; objects only in the old path are pruned")
            if _get(b, "spec", "targetNamespace") != _get(h, "spec", "targetNamespace"):
                out.append(f"{what}: targetNamespace changes; every object is recreated elsewhere")
        elif (group, kind) == (HR_GROUP, "HelmRelease"):
            if release_name(b) != release_name(h):
                out.append(f"{what}: release name changes from {release_name(b)!r} to {release_name(h)!r}; "
                           f"Helm uninstalls the old release")
            for f in ("targetNamespace", "storageNamespace"):
                if _get(b, "spec", f) != _get(h, "spec", f):
                    out.append(f"{what}: spec.{f} changes; Helm uninstalls and reinstalls the release")
    for i, h in sorted(head.items()):
        group, kind = i[0], i[1]
        if (group, kind) == ("storage.k8s.io", "StorageClass") and h.get("reclaimPolicy") == "Delete" \
                and (i not in base or base[i].get("reclaimPolicy") != "Delete"):
            out.append(f"{ident_str(i)} has reclaimPolicy Delete")
        if (group, kind) == ("", "PersistentVolume") and _get(h, "spec", "persistentVolumeReclaimPolicy") == "Delete" \
                and (i not in base or _get(base[i], "spec", "persistentVolumeReclaimPolicy") != "Delete"):
            out.append(f"{ident_str(i)} has persistentVolumeReclaimPolicy Delete")
    return out


def load_render_dir(d: Path) -> dict[str, dict[Ident, dict]]:
    out = {}
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.yaml")):
        out[f.stem] = {ident(x): x for x in load_docs(f.read_text(), str(f))}
    return out


def cmd_guard(args: argparse.Namespace) -> int:
    report = Report(f"guard ({args.label})")
    findings: list[str] = []
    if args.base_build and args.head_build:
        base, head = Build(args.base_build), Build(args.head_build)
        b_objs = {ident(d): d for _, d in base.all_docs()}
        h_objs = {ident(d): d for _, d in head.all_docs()}
        findings += compare_objects(b_objs, h_objs, base.owners(), head.owners())
    if args.base_render and args.head_render:
        base_r, head_r = load_render_dir(Path(args.base_render)), load_render_dir(Path(args.head_render))
        for key in sorted(set(base_r) & set(head_r)):
            hid = key.replace("__", "/", 1)
            findings += [f"HelmRelease {hid}: {f}" for f in compare_objects(base_r[key], head_r[key])]
    if args.out:
        Path(args.out).write_text(json.dumps({"findings": findings, "allowed": args.allow}, indent=2))
    hint = ("allowed by the `allow-destructive` label" if args.allow
            else "add the `allow-destructive` label to the pull request if this is intended")
    for f in findings:
        (report.warning if args.allow else report.error)(f"{f} ({hint})")
    if not findings:
        report.note("no destructive changes")
    return report.finish()


# --------------------------------------------------------------------------
# diff
# --------------------------------------------------------------------------

DIFF_STRIP_LABELS = ("helm.sh/chart", "chart", "app.kubernetes.io/version")
DIFF_LIMIT = 60000


def normalize_for_diff(docs: list[dict]) -> str:
    clean = []
    for d in docs:
        d = copy.deepcopy(d)
        md = d.get("metadata") or {}
        if md.get("labels"):
            for k in DIFF_STRIP_LABELS:
                md["labels"].pop(k, None)
        if md.get("annotations"):
            for k in [k for k in md["annotations"] if k.startswith("checksum/")]:
                md["annotations"].pop(k)
        if is_kind(d, "", "Secret"):
            for key in ("data", "stringData"):
                if isinstance(d.get(key), dict):
                    d[key] = {k: REDACTED for k in d[key]}
        if is_sops(d):
            d["sops"] = REDACTED
            for key in ("data", "stringData", "spec"):
                if isinstance(d.get(key), dict):
                    d[key] = {k: REDACTED for k in d[key]}
        clean.append(d)
    clean.sort(key=lambda x: ident(x))
    return dump_docs(clean, sort_keys=True)


def unified(a: str, b: str, name: str) -> tuple[str, int, int]:
    lines = list(difflib.unified_diff(a.splitlines(True), b.splitlines(True), f"base/{name}", f"head/{name}", n=3))
    added = sum(1 for x in lines if x.startswith("+") and not x.startswith("+++"))
    removed = sum(1 for x in lines if x.startswith("-") and not x.startswith("---"))
    return "".join(lines), added, removed


def details(title: str, body: str, budget: list[int]) -> str:
    block = f"<details><summary>{title}</summary>\n\n```diff\n{body}```\n\n</details>\n"
    if len(block) > budget[0]:
        return f"<details><summary>{title}</summary>\n\nDiff too large for a comment; see the job artifacts.\n\n</details>\n"
    budget[0] -= len(block)
    return block


def cmd_diff(args: argparse.Namespace) -> int:
    plan = json.loads(Path(args.plan).read_text())
    base_b, head_b = Build(args.base_build), Build(args.head_build)
    budget = [DIFF_LIMIT]
    parts = ["<!-- fluxci:diff -->", "## fluxci: what this pull request changes", ""]

    guard = json.loads(Path(args.guard).read_text()) if args.guard and Path(args.guard).exists() else None
    if guard is not None:
        if guard["findings"]:
            state = "allowed by the `allow-destructive` label" if guard["allowed"] else "blocking"
            parts += [f"### :warning: Destructive changes ({state})", ""]
            parts += [f"- {f}" for f in guard["findings"]] + [""]
        else:
            parts += ["### :white_check_mark: No destructive changes", ""]

    # Rendered HelmReleases
    rows, blocks = [], []
    base_dir, head_dir = Path(args.base_render), Path(args.head_render)

    def render_index(d: Path) -> dict:
        f = d / "render.json"
        return json.loads(f.read_text()) if f.exists() else {}

    base_idx, head_idx = render_index(base_dir), render_index(head_dir)
    for hid in plan["selected"] + plan["removed"]:
        fname = f"{file_key(hid)}.yaml"
        b_info, h_info = base_idx.get(hid, {}), head_idx.get(hid, {})
        chart = h_info.get("chart") or b_info.get("chart") or plan["head"].get(hid, plan["base"].get(hid, {})).get("chart", "")
        bv, hv = b_info.get("version") or "-", h_info.get("version") or "-"
        change = f"`{chart}` {bv} → {hv}" if bv != hv else f"`{chart}` {hv}"
        failed = [side for side, info in (("base", b_info), ("head", h_info)) if info.get("status") == "error"]
        if failed:
            rows.append(f"| `{hid}` | {change} | render failed on {' and '.join(failed)} |")
            continue
        a_docs = load_docs((base_dir / fname).read_text()) if (base_dir / fname).exists() else []
        b_docs = load_docs((head_dir / fname).read_text()) if (head_dir / fname).exists() else []
        a, b = normalize_for_diff(a_docs), normalize_for_diff(b_docs)
        if a == b:
            continue
        text, added, removed = unified(a, b, hid)
        rows.append(f"| `{hid}` | {change} | +{added} / −{removed} |")
        blocks.append(details(f"<code>{hid}</code>", text, budget))
    parts += [f"### Rendered HelmReleases ({plan['mode']} mode)", ""]
    if rows:
        parts += ["| HelmRelease | Chart | Lines |", "|---|---|---|"] + rows + [""] + blocks
    else:
        parts += ["No change in the rendered manifests.", ""]

    # Plain manifests
    changes = []
    for kid in sorted(set(base_b.docs) | set(head_b.docs)):
        a = {ident(d): d for d in base_b.docs.get(kid, [])}
        b = {ident(d): d for d in head_b.docs.get(kid, [])}
        for i in sorted(set(a) | set(b)):
            if a.get(i) == b.get(i):
                continue
            state = "added" if i not in a else ("removed" if i not in b else "changed")
            ta = normalize_for_diff([a[i]]) if i in a else ""
            tb = normalize_for_diff([b[i]]) if i in b else ""
            if ta == tb:
                continue
            text, _, _ = unified(ta, tb, ident_str(i))
            changes.append((kid, i, state, text))
    parts += ["### Flux Kustomizations", ""]
    if changes:
        parts += ["| Kustomization | Object | Change |", "|---|---|---|"]
        parts += [f"| `{kid}` | `{ident_str(i)}` | {state} |" for kid, i, state, _ in changes] + [""]
        for kid, i, state, text in changes:
            parts.append(details(f"<code>{ident_str(i)}</code> ({state})", text, budget))
    else:
        parts += ["No change in the objects applied by Flux.", ""]
    parts += ["", "<sub>Secret values are redacted; <code>helm.sh/chart</code>, <code>chart</code> and <code>app.kubernetes.io/version</code> "
                  "labels and <code>checksum/*</code> annotations are left out of the diff.</sub>", ""]
    Path(args.out).write_text("\n".join(parts))
    print(f"diff: {len(rows)} HelmReleases and {len(changes)} objects changed")
    return 0


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="fluxci", description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("build", help="build every Flux Kustomization")
    s.add_argument("--repo", default=".", help="repository checkout to build")
    s.add_argument("--config-repo", help="checkout whose .github/ci configuration to use (default: --repo)")
    s.add_argument("--out", required=True)
    s.add_argument("--label", default="head")
    s.add_argument("--lenient", action="store_true", help="report errors as warnings (base revision)")
    s.set_defaults(func=cmd_build)

    s = sub.add_parser("check", help="static checks on a build")
    s.add_argument("--build", required=True)
    s.set_defaults(func=cmd_check)

    s = sub.add_parser("plan", help="choose the HelmReleases to render")
    s.add_argument("--head", required=True)
    s.add_argument("--base")
    s.add_argument("--full", action="store_true")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_plan)

    s = sub.add_parser("render", help="helm template of the planned HelmReleases")
    s.add_argument("--build", required=True)
    s.add_argument("--plan", required=True)
    s.add_argument("--side", choices=("head", "base"), default="head")
    s.add_argument("--out", required=True)
    s.add_argument("--cache", help="chart cache directory")
    s.set_defaults(func=cmd_render)

    s = sub.add_parser("schemas", help="JSON schemas from CRDs")
    s.add_argument("--repo", default=".")
    s.add_argument("--out", required=True)
    s.add_argument("inputs", nargs="*", help="files or directories with CRDs")
    s.set_defaults(func=cmd_schemas)

    s = sub.add_parser("bundle", help="files to validate")
    s.add_argument("--build")
    s.add_argument("--render", action="append")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_bundle)

    s = sub.add_parser("validate", help="kubeconform and conftest on a bundle")
    s.add_argument("--repo", default=".")
    s.add_argument("--bundle", required=True)
    s.add_argument("--schemas")
    s.add_argument("--cache", help="kubeconform schema cache directory")
    s.add_argument("--label", default="manifests")
    s.add_argument("--no-conftest", action="store_true")
    s.set_defaults(func=cmd_validate)

    s = sub.add_parser("guard", help="destructive changes between base and head")
    s.add_argument("--base-build")
    s.add_argument("--head-build")
    s.add_argument("--base-render")
    s.add_argument("--head-render")
    s.add_argument("--allow", action="store_true", help="report findings as warnings")
    s.add_argument("--label", default="manifests")
    s.add_argument("--out", help="write the findings as JSON")
    s.set_defaults(func=cmd_guard)

    s = sub.add_parser("diff", help="markdown report of the changes")
    s.add_argument("--plan", required=True)
    s.add_argument("--base-build", required=True)
    s.add_argument("--head-build", required=True)
    s.add_argument("--base-render", required=True)
    s.add_argument("--head-render", required=True)
    s.add_argument("--guard", help="guard findings JSON")
    s.add_argument("--out", required=True)
    s.set_defaults(func=cmd_diff)

    args = p.parse_args(argv)
    try:
        return args.func(args)
    except CIError as e:
        print(f"::error title=fluxci {args.command}::{_esc_data(str(e))}" if os.environ.get("GITHUB_ACTIONS")
              else f"ERROR: {e}", file=sys.stderr if not os.environ.get("GITHUB_ACTIONS") else sys.stdout)
        return 2


if __name__ == "__main__":
    sys.exit(main())
