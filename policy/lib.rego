# Helpers shared by the policies. conftest evaluates each document of the CI
# bundle on its own: everything Flux applies plus the manifests rendered from
# the HelmReleases (see .github/ci/README.md).
package main

import rego.v1

pod_kinds := {"Deployment", "StatefulSet", "DaemonSet", "ReplicaSet", "Job"}

pod_spec := input.spec.template.spec if input.kind in pod_kinds

pod_spec := input.spec.jobTemplate.spec.template.spec if input.kind == "CronJob"

pod_spec := input.spec if input.kind == "Pod"

containers contains c if {
	some c in pod_spec.containers
}

containers contains c if {
	some c in pod_spec.initContainers
}

object_id := sprintf("%s %s/%s", [input.kind, object.get(input.metadata, "namespace", ""), input.metadata.name])

# Known violations of a deny rule, listed in exceptions.rego.
excepted(rule) if object_id in exceptions[rule]
