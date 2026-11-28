# Flux sources and releases must be reproducible: a reconciliation of the same
# commit has to install the same thing.
package main

import rego.v1

exact_version(v) if regex.match(`^v?[0-9]+\.[0-9]+\.[0-9]+([-+][0-9A-Za-z.+-]+)?$`, v)

deny contains msg if {
	input.kind == "HelmRelease"
	chart := input.spec.chart.spec
	chart.sourceRef.kind == "HelmRepository"
	version := object.get(chart, "version", "")
	not exact_version(version)
	not excepted("helmrelease-version")
	msg := sprintf("%s: chart %s has no exact version (%q)", [object_id, chart.chart, version])
}

warn contains msg if {
	input.kind == "GitRepository"
	ref := object.get(input.spec, "ref", {})
	not ref.tag
	not ref.semver
	not ref.commit
	msg := sprintf("%s: follows a branch (%v); pin a tag or a commit", [object_id, ref])
}
