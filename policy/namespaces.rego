package main

warn[msg] {
  input.kind == "Namespace"
  not input.metadata.labels["pod-security.kubernetes.io/enforce"]
  msg := sprintf("Namespace %s manca label pod-security enforce", [input.metadata.name])
}
