{{- define "teslamate.teslamateEnv" -}}
- name: ENCRYPTION_KEY
  valueFrom:
    secretKeyRef:
      name: {{ include "teslamate.encryptionKeySecretName" . }}
      key: {{ .Values.teslamate.existingSecretKey }}
- name: RELEASE_TMP
  value: /tmp
{{ include "teslamate.db.env" . }}
{{- end }}

{{- define "teslamate.teslamateVolumeMounts" -}}
- name: tmp
  mountPath: /tmp
- name: srtm-cache
  mountPath: /opt/app/.srtm_cache
{{- if include "teslamate.cnpgEnabled" . }}
- name: db-ca
  mountPath: /etc/teslamate/db-ca
  readOnly: true
{{- end }}
{{- end }}
