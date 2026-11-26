# Container policies: deny what breaks reproducibility, warn about what
# weakens isolation or scheduling.
package main

import rego.v1

image_pinned(image) if contains(image, "@sha256:")

image_pinned(image) if {
	not contains(image, "@")
	parts := split(image, "/")
	name := parts[count(parts) - 1]
	contains(name, ":")
	not endswith(name, ":latest")
}

deny contains msg if {
	some c in containers
	not image_pinned(c.image)
	not excepted("image-tag")
	msg := sprintf("%s: container %s uses %q without a version tag or a digest", [object_id, c.name, c.image])
}

warn contains msg if {
	some c in containers
	c.securityContext.privileged == true
	msg := sprintf("%s: container %s is privileged", [object_id, c.name])
}

warn contains msg if {
	some c in pod_spec.containers
	not c.resources.requests.memory
	msg := sprintf("%s: container %s has no memory request", [object_id, c.name])
}

warn contains msg if {
	some v in pod_spec.volumes
	v.hostPath
	msg := sprintf("%s: volume %s mounts the host path %s", [object_id, v.name, v.hostPath.path])
}
