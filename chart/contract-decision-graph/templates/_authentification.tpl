{{- /*
Authentification et entrée réseau (PR D1, ADR 005).
*/}}

{{- /* Valeurs exigées : aucune entrée sans authentification, rien de flou avec elle. */}}
{{- define "cdg.authentification.valider" -}}
{{- $auth := .Values.authentification -}}
{{- if and .Values.ingress.active (not $auth.active) -}}
{{- fail "ingress.active exige authentification.active : aucune entrée réseau sans authentification (ADR 004, ADR 005)" -}}
{{- end -}}
{{- if $auth.active -}}
{{- if not $auth.emetteur }}{{ fail "authentification.active exige authentification.emetteur (https)" }}{{ end -}}
{{- if not $auth.clientId }}{{ fail "authentification.active exige authentification.clientId" }}{{ end -}}
{{- if not $auth.image.digest }}{{ fail "authentification.active exige authentification.image.digest : oauth2-proxy par empreinte, jamais par étiquette" }}{{ end -}}
{{- if not .Values.ingress.hote }}{{ fail "authentification.active exige ingress.hote : adresse publique de l'interface" }}{{ end -}}
{{- end -}}
{{- end -}}

{{- define "cdg.adressePublique" -}}
{{- printf "https://%s" .Values.ingress.hote -}}
{{- end -}}

{{- define "cdg.imageOauth2Proxy" -}}
{{- printf "%s@%s" .Values.authentification.image.repository .Values.authentification.image.digest -}}
{{- end -}}

{{- /* Taille maximale d'un envoi : celle de l'interface (adapters/web/security.py), tirée
de la même configuration : input.max_chars × 4 octets + 64 Kio de formulaire. */}}
{{- define "cdg.tailleMaxEnvoi" -}}
{{- $config := default (.Files.Get "files/decision.yaml") .Values.configuration.decision | fromYaml -}}
{{- add (mul (int $config.input.max_chars) 4) 65536 -}}
{{- end -}}

{{- /* Arguments d'oauth2-proxy v7.15.4 (options vérifiées dans son code) : seul chemin
vers l'interface ; jeton d'identité transmis, en-têtes d'identité du client retirés ;
journal de connexion réduit au sub (revendication d'e-mail remplacée par sub, ni message,
ni adresse du client) ; ni e-mail demandé au fournisseur. */}}
{{- define "cdg.argsOauth2Proxy" -}}
{{- $auth := .Values.authentification -}}
{{- $journal := `{"journal":"oauth2-proxy","evenement":"authentification","statut":"{{.Status}}","sub":"{{.Username}}"}` -}}
- --http-address=0.0.0.0:{{ $auth.port }}
- --upstream=http://127.0.0.1:8000/
- --provider=oidc
- --oidc-issuer-url={{ $auth.emetteur }}
- --client-id={{ $auth.clientId }}
- --client-secret-file=/run/secrets/oauth2-proxy/client-secret
- --cookie-secret-file=/run/secrets/oauth2-proxy/cookie-secret
- --redirect-url={{ include "cdg.adressePublique" . }}/oauth2/callback
- --email-domain=*
- --oidc-email-claim=sub
- --oidc-groups-claim=groups
- --scope=openid profile groups offline_access
- --code-challenge-method=S256
- --skip-provider-button=true
- --pass-authorization-header=true
- --skip-auth-strip-headers=true
- --pass-host-header=false
- --reverse-proxy=true
- --cookie-name=_cdg_session
- --cookie-secure=true
- --cookie-httponly=true
- --cookie-samesite=lax
- --cookie-expire={{ $auth.session.duree }}
- --cookie-refresh={{ $auth.session.revalidation }}
- --request-logging=false
- --auth-logging=true
- {{ printf "--auth-logging-format=%s" $journal | quote }}
- --silence-ping-logging=true
{{- if $auth.autorites }}
- --provider-ca-file=/run/autorites/ca.crt
{{- end }}
{{- if $auth.deconnexionFournisseur }}
- --whitelist-domain={{ (urlParse $auth.emetteur).host }}
{{- end }}
{{- end -}}
