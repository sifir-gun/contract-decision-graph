{{/*
Tâches rendues, séparées par des espaces : chacune selon son réglage, en mode réel
seulement ; aucune en démonstration. Leur compte de service et leur règle réseau n'existent
qu'avec elles (une règle qui ne désigne aucun pod est orpheline).
*/}}
{{- define "cdg.taches" -}}
{{- $t := .Values.taches -}}
{{- if eq .Values.mode "reel" -}}
{{- if $t.migrations.active }} migrations{{ end -}}
{{- if $t.ingestion.active }} ingestion{{ end -}}
{{- if and $t.controleConfiguration.active (not $t.controleConfiguration.passerOutre) }} controle-configuration{{ end -}}
{{- end -}}
{{- end -}}

{{/*
Tâche Helm commune (ADR 005) : non root, racine en lecture seule, tentatives limitées,
nettoyage automatique ; `args` : arguments de la CLI après l'entrée de l'image.
*/}}
{{- define "cdg.tache" -}}
{{- $racine := .racine -}}
{{- $v := $racine.Values -}}
apiVersion: batch/v1
kind: Job
metadata:
  name: {{ include "cdg.nomComplet" $racine }}-{{ .nom }}
  namespace: {{ $racine.Release.Namespace }}
  labels:
    {{- include "cdg.etiquettes" $racine | nindent 4 }}
    app.kubernetes.io/component: taches
  annotations:
    "helm.sh/hook": {{ .crochets }}
    "helm.sh/hook-weight": {{ .poids | quote }}
    # une tâche réussie disparaît ; une tâche en échec reste pour le diagnostic,
    # puis ttlSecondsAfterFinished la supprime (après `helm uninstall` : nettoyage dans
    # docs/exploitation.md). Ses journaux sont d'abord recopiés sur la sortie de Helm.
    "helm.sh/hook-delete-policy": before-hook-creation,hook-succeeded
    "helm.sh/hook-output-log-policy": hook-succeeded,hook-failed
    # exclusions de kube-linter, justifiées ici et dans l'ADR 005
    ignore-check.kube-linter.io/restart-policy: "chaque tentative dans un nouveau pod (backoffLimit) : le journal d'une tentative ratée reste lisible"
    ignore-check.kube-linter.io/no-liveness-probe: "tâche qui s'exécute jusqu'au bout, bornée par activeDeadlineSeconds : aucune sonde de vie"
    ignore-check.kube-linter.io/no-readiness-probe: "tâche sans service ni trafic à recevoir : aucune sonde de disponibilité"
    ignore-check.kube-linter.io/no-node-affinity: "aucune contrainte de matériel ni de zone : n'importe quel nœud convient, amd64 comme arm64 (images multi-architecture)"
    ignore-check.kube-linter.io/dnsconfig-options: "ne résout que des noms internes au cluster ; api.mistral.ai est résolu par le proxy de sortie, qui porte ndots 2 (ADR 005)"
spec:
  backoffLimit: {{ $v.taches.tentatives }}
  ttlSecondsAfterFinished: {{ $v.taches.conservation }}
  activeDeadlineSeconds: {{ .delai }}
  template:
    metadata:
      labels:
        {{- include "cdg.etiquettesComposant" (dict "racine" $racine "composant" "taches") | nindent 8 }}
    spec:
      restartPolicy: Never
      serviceAccountName: {{ include "cdg.nomComplet" $racine }}-taches
      automountServiceAccountToken: false
      enableServiceLinks: false
      securityContext:
        {{- include "cdg.securitePod" $racine | nindent 8 }}
      {{- if .modele }}
      {{- with (include "cdg.initCopieModele" $racine) }}
      initContainers:
        {{- . | nindent 8 }}
      {{- end }}
      {{- end }}
      containers:
        - name: {{ .nom }}
          image: {{ include "cdg.image" $racine }}
          imagePullPolicy: {{ $v.image.pullPolicy }}
          args:
            {{- toYaml .args | nindent 12 }}
          env:
            {{- include "cdg.envBase" $racine | nindent 12 }}
            {{- if .modele }}
            - name: EMBEDDING_CACHE_DIR
              value: /modele
            {{- end }}
          resources:
            {{- toYaml .ressources | nindent 12 }}
          securityContext:
            {{- include "cdg.securiteConteneur" $racine | nindent 12 }}
          volumeMounts:
            - name: tmp
              mountPath: /tmp
            - name: configuration
              mountPath: /app/config
              readOnly: true
            {{- include "cdg.montageSecrets" $racine | nindent 12 }}
            {{- if .modele }}
            - name: modele
              mountPath: /modele
              readOnly: true
            {{- end }}
      volumes:
        - name: tmp
          emptyDir:
            sizeLimit: {{ $v.tmp.tailleMax }}
        - name: configuration
          configMap:
            name: {{ .configMap }}
        {{- include "cdg.volumeSecrets" (dict "racine" $racine "llm" false "administrateur" .administrateur) | nindent 8 }}
        {{- if .modele }}
        {{- include "cdg.volumeModele" $racine | nindent 8 }}
        {{- end }}
{{- end -}}
