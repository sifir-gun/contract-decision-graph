{{/*
Gabarits communs du chart (ADR 005) : noms, étiquettes recommandées, images par
empreinte, sécurité des conteneurs, environnement partagé par l'interface et les tâches.
*/}}

{{- define "cdg.nom" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "cdg.nomComplet" -}}
{{- if contains .Chart.Name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name .Chart.Name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/* Étiquettes recommandées par Kubernetes (app.kubernetes.io/*) et par Helm. */}}
{{- define "cdg.etiquettes" -}}
app.kubernetes.io/name: {{ include "cdg.nom" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{ include "cdg.etiquettesCommunes" . }}
{{- end -}}

{{/* Les mêmes, hors nom et instance, que porte déjà le sélecteur d'un composant. */}}
{{- define "cdg.etiquettesCommunes" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/part-of: contract-decision-graph
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end -}}

{{/* Sélecteur d'un composant : nom, instance, composant (web, taches, test). */}}
{{- define "cdg.selecteur" -}}
app.kubernetes.io/name: {{ include "cdg.nom" .racine }}
app.kubernetes.io/instance: {{ .racine.Release.Name }}
app.kubernetes.io/component: {{ .composant }}
{{- end -}}

{{/* Étiquettes d'un pod d'un composant : son sélecteur, puis les étiquettes communes,
chacune une seule fois (une clé en double est refusée, par kubeconform notamment). */}}
{{- define "cdg.etiquettesComposant" -}}
{{ include "cdg.selecteur" . }}
{{ include "cdg.etiquettesCommunes" .racine }}
{{- end -}}

{{/* Images : toujours par empreinte, jamais par étiquette. */}}
{{- define "cdg.image" -}}
{{- printf "%s@%s" .Values.image.repository .Values.image.digest -}}
{{- end -}}

{{- define "cdg.imageModele" -}}
{{- printf "%s@%s" .Values.modele.image.repository .Values.modele.image.digest -}}
{{- end -}}

{{- define "cdg.imageBusybox" -}}
{{- printf "%s@%s" .Values.modele.copie.busybox.repository .Values.modele.copie.busybox.digest -}}
{{- end -}}

{{/* Nombre de fils de calcul de l'embedder : la limite CPU, entière (schéma). */}}
{{- define "cdg.fils" -}}
{{- .limits.cpu | toString -}}
{{- end -}}

{{/* Sécurité du pod : non root, seccomp du runtime (Pod Security « restricted »). */}}
{{- define "cdg.securitePod" -}}
runAsNonRoot: true
runAsUser: 65532
runAsGroup: 65532
seccompProfile:
  type: RuntimeDefault
{{- end -}}

{{/* Sécurité d'un conteneur : racine en lecture seule, aucune capacité, pas d'élévation. */}}
{{- define "cdg.securiteConteneur" -}}
allowPrivilegeEscalation: false
readOnlyRootFilesystem: true
runAsNonRoot: true
capabilities:
  drop: ["ALL"]
{{- end -}}

{{/* Connexion d'app_role à PostgreSQL : adresse, et mot de passe par le Secret. */}}
{{- define "cdg.envBase" -}}
- name: POSTGRES_HOST
  value: {{ .Values.base.hote | quote }}
- name: POSTGRES_PORT
  value: {{ .Values.base.port | quote }}
- name: POSTGRES_DB
  value: {{ .Values.base.nom | quote }}
- name: APP_DB_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.base.application.secret }}
      key: {{ .Values.base.application.cle }}
{{- end -}}

{{/* Administrateur de PostgreSQL : migrations et ingestion seulement. */}}
{{- define "cdg.envAdministrateur" -}}
- name: POSTGRES_USER
  valueFrom:
    secretKeyRef:
      name: {{ .Values.base.administrateur.secret }}
      key: {{ .Values.base.administrateur.cleUtilisateur }}
- name: POSTGRES_PASSWORD
  valueFrom:
    secretKeyRef:
      name: {{ .Values.base.administrateur.secret }}
      key: {{ .Values.base.administrateur.cleMotDePasse }}
{{- end -}}

{{/* Volume du modèle : volume image, ou volume éphémère rempli par copie. */}}
{{- define "cdg.volumeModele" -}}
- name: modele
{{- if eq .Values.modele.montage "image" }}
  image:
    reference: {{ include "cdg.imageModele" . }}
    pullPolicy: {{ .Values.modele.image.pullPolicy }}
{{- else }}
  emptyDir:
    sizeLimit: {{ .Values.modele.copie.tailleMax }}
- name: outils
  emptyDir:
    sizeLimit: 8Mi
{{- end }}
{{- end -}}

{{/*
Repli par copie : busybox dépose son binaire statique dans un volume partagé, puis un
conteneur de l'image du modèle, qui n'a aucun programme, l'utilise pour transférer les
poids par tar (liens physiques conservés, contrairement à cp de busybox).
*/}}
{{- define "cdg.initCopieModele" -}}
{{- if eq .Values.modele.montage "copie" }}
- name: outil-de-copie
  image: {{ include "cdg.imageBusybox" . }}
  imagePullPolicy: IfNotPresent
  command: ["/bin/busybox", "cp", "/bin/busybox", "/outils/busybox"]
  securityContext:
    {{- include "cdg.securiteConteneur" . | nindent 4 }}
  resources:
    {{- toYaml .Values.ressources.copie | nindent 4 }}
  volumeMounts:
    - name: outils
      mountPath: /outils
- name: copie-du-modele
  image: {{ include "cdg.imageModele" . }}
  imagePullPolicy: {{ .Values.modele.image.pullPolicy }}
  command:
    - /outils/busybox
    - sh
    - -c
    - >-
      /outils/busybox tar -C / -cf - CACHEDIR.TAG LICENCE-MODELE.md blobs flat
      models--qdrant--multilingual-e5-large-onnx | /outils/busybox tar -C /modele -xf -
  securityContext:
    {{- include "cdg.securiteConteneur" . | nindent 4 }}
  resources:
    {{- toYaml .Values.ressources.copie | nindent 4 }}
  volumeMounts:
    - name: outils
      mountPath: /outils
      readOnly: true
    - name: modele
      mountPath: /modele
{{- end }}
{{- end -}}

{{/* Sortie vers le DNS du cluster (UDP et TCP 53). */}}
{{- define "cdg.sortieDns" -}}
- to:
    - namespaceSelector:
        matchLabels:
          kubernetes.io/metadata.name: {{ .Values.dns.espaceDeNoms }}
      podSelector:
        matchLabels:
          {{- toYaml .Values.dns.selecteur | nindent 10 }}
  ports:
    - port: 53
      protocol: UDP
    - port: 53
      protocol: TCP
{{- end -}}

{{/* Sortie vers les pods de PostgreSQL. */}}
{{- define "cdg.sortieBase" -}}
- to:
    - podSelector:
        matchLabels:
          {{- toYaml .Values.base.selecteur | nindent 10 }}
  ports:
    - port: {{ .Values.base.port }}
      protocol: TCP
{{- end -}}
