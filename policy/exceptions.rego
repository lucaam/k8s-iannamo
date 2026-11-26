# Known violations of the deny policies, as "Kind namespace/name". Every entry
# needs the reason and the change that removes it; delete the entry when that
# change is merged.
package main

import rego.v1

exceptions := {
	"image-tag": {
		# busybox without a tag in the fix-permissions initContainer; removed by
		# the Home Assistant hardening PR (#421).
		"StatefulSet home-assistant/home-assistant",
	},
}
