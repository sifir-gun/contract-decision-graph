{{/*
Gabarits communs du chart du proxy de sortie (ADR 005, PR C3).
*/}}

{{- define "cdg-proxy.nom" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Installé sous le nom cdg-proxy : le service s'appelle cdg-proxy, l'adresse que
l'application attend par défaut (proxy.url). */}}
{{- define "cdg-proxy.nomComplet" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "cdg-proxy.selecteur" -}}
app.kubernetes.io/name: {{ include "cdg-proxy.nom" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "cdg-proxy.etiquettes" -}}
{{ include "cdg-proxy.selecteur" . }}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/component: proxy-de-sortie
app.kubernetes.io/part-of: contract-decision-graph
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}
