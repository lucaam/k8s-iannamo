{{- define "teslamate.testPodSpec" -}}
restartPolicy: Never
automountServiceAccountToken: false
enableServiceLinks: false
securityContext:
  runAsNonRoot: true
  runAsUser: {{ .uid | default 65532 }}
  seccompProfile:
    type: RuntimeDefault
{{- include "teslamate.imagePullSecrets" .ctx }}
{{- end }}

{{- define "teslamate.testContainerSecurityContext" -}}
securityContext:
  allowPrivilegeEscalation: false
  readOnlyRootFilesystem: true
  capabilities:
    drop: [ALL]
{{- end }}

{{- define "teslamate.testMetadata" -}}
name: {{ include "teslamate.componentName" (dict "ctx" .ctx "component" (printf "test-%s" .component)) }}
labels:
  {{- include "teslamate.labels" (dict "ctx" .ctx "component" "test") | nindent 2 }}
annotations:
  helm.sh/hook: test
  helm.sh/hook-delete-policy: before-hook-creation,hook-succeeded
{{- end }}
