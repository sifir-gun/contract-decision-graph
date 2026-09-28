{{/*
Gabarits communs du chart de la base (ADR 005, PR C3).
*/}}

{{- define "cdg-postgres.etiquettes" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: base-de-donnees
app.kubernetes.io/part-of: contract-decision-graph
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
