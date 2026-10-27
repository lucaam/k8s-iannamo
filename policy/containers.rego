package main

warn[msg] {
  input.kind == "Deployment"
  some i
  c := input.spec.template.spec.containers[i]
  c.securityContext.privileged == true
  not input.metadata.annotations["policy.example.com/privileged-reason"]
  msg := sprintf("Deployment %s/%s container %s privileged senza annotation", [input.metadata.namespace, input.metadata.name, c.name])
}

warn[msg] {
  input.kind == "Deployment"
  some i
  c := input.spec.template.spec.containers[i]
  endswith(c.image, ":latest")
  msg := sprintf("Deployment %s/%s container %s usa :latest", [input.metadata.namespace, input.metadata.name, c.name])
}
