package main

import rego.v1

warn contains msg if {
	input.kind == "Namespace"
	not input.metadata.labels["pod-security.kubernetes.io/enforce"]
	msg := sprintf("Namespace %s has no pod-security.kubernetes.io/enforce label", [input.metadata.name])
}
