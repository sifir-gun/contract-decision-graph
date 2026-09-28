# Proxy de sortie (ADR 005, PR C3) : Smokescreen (Stripe, MIT), construit par nous, avec la
# même chaîne que l'image de l'application. Entrées figées et vérifiées :
# - l'image de construction de l'application (uv, Debian trixie), déjà vérifiée par
#   scripts/chaine.py ; seules ses commandes de base (tar, cp, find) servent ici ;
# - Go, archive officielle, vérifiée par son empreinte (https://go.dev/dl/?mode=json) ;
# - Smokescreen, source figée par son commit : master du 28/09/2026, qui contient les
#   correctifs de sécurité publiés après la v0.1.0 (ADR 005 : surveillance, étiquette
#   officielle dès la prochaine version) ; dépendances du dossier vendor, sans téléchargement ;
# - l'image d'exécution distroless static, signée (scripts/chaine.py).
#   docker build --file docker/proxy-sortie/Dockerfile --tag cdg-proxy:verification \
#     docker/proxy-sortie
ARG TARGETARCH

FROM ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:c40e42de0e1516439b5139d7a214657dbffbbd3d2366661347efae7b8337c197 AS go-amd64
ADD --checksum=sha256:d0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b https://go.dev/dl/go1.26.8.linux-amd64.tar.gz /go.tar.gz

FROM ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:c40e42de0e1516439b5139d7a214657dbffbbd3d2366661347efae7b8337c197 AS go-arm64
ADD --checksum=sha256:211ffced9dcb9633a55eac6364816ec0ddd951389a740e88fa8b3337971bdda0 https://go.dev/dl/go1.26.8.linux-arm64.tar.gz /go.tar.gz

FROM go-${TARGETARCH} AS construction
ADD --keep-git-dir=false https://github.com/stripe/smokescreen.git#6c44698887826662733b47bbdc461aabcbb365ee /src
WORKDIR /src
# binaire statique ; GOPROXY=off et -mod=vendor : aucune dépendance téléchargée ;
# GOTOOLCHAIN=local : le Go vérifié ci-dessus, jamais un autre
RUN tar -C /usr/local -xzf /go.tar.gz \
 && CGO_ENABLED=0 GOFLAGS=-buildvcs=false GOPROXY=off GOTOOLCHAIN=local \
    /usr/local/go/bin/go build -mod=vendor -trimpath -ldflags="-s -w" -o /smokescreen .
# licences : Smokescreen, Go (bibliothèque standard liée au binaire), chaque dépendance
RUN mkdir -p /licences/smokescreen /licences/go /licences/vendor \
 && cp /src/LICENSE.txt /licences/smokescreen/ \
 && cp /usr/local/go/LICENSE /licences/go/ \
 && cd /src/vendor \
 && find . -type f \( -iname 'LICENSE*' -o -iname 'LICENCE*' -o -iname 'COPYING*' -o -iname 'NOTICE*' \) \
    -exec cp --parents {} /licences/vendor/ \;

FROM gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3
LABEL org.opencontainers.image.source="https://github.com/sifir-gun/contract-decision-graph" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="contract-decision-graph proxy de sortie" \
      org.opencontainers.image.description="Smokescreen, proxy de sortie : seule l'API de Mistral"
COPY --from=construction /smokescreen /smokescreen
COPY --from=construction /licences /licences
USER 65532:65532
EXPOSE 4750
ENTRYPOINT ["/smokescreen"]
CMD ["--config-file", "/configuration/smokescreen.yaml"]
