{{/*
  The authentication block is NOT defined here any more. Its shape depends on the
  provider document's own security scheme -- Aruba declares apiKey in eight of its
  thirteen documents and http/bearer in the other five -- so a single shared block
  was wrong for most kinds. See "aruba.gen.auth.<Kind>" in _generated.tpl.
*/}}

{{/* Per-verb query config carrying api-version for the given verbs. */}}
{{- define "aruba.queryConfig" -}}
{{- $v := .apiVersion -}}
query:
{{- range .verbs }}
  {{ . }}:
    api-version: {{ $v | quote }}
{{- end }}
{{- end -}}
