# Known violations of the deny policies, as "Kind namespace/name". Every entry
# needs the reason and the change that removes it; delete the entry when that
# change is merged.
package main

import rego.v1

exceptions := {}
