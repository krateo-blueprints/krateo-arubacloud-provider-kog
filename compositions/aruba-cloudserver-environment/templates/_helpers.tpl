{{/* Shared authentication block reused by every <Kind>Configuration. */}}
{{- define "aruba.authentication" -}}
authentication:
  bearer:
    tokenRef:
      name: {{ .Values.tokenSecret.name | quote }}
      namespace: {{ .Values.tokenSecret.namespace | quote }}
      key: {{ .Values.tokenSecret.key | quote }}
{{- end -}}

{{/* Per-verb query config carrying api-version for the given verbs. */}}
{{- define "aruba.queryConfig" -}}
{{- $v := .apiVersion -}}
query:
{{- range .verbs }}
  {{ . }}:
    api-version: {{ $v | quote }}
{{- end }}
{{- end -}}
