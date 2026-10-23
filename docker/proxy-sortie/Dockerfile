# Proxy de sortie (ADR 005, PR C3) : Smokescreen (Stripe, MIT), construit par nous, avec la
# même chaîne que l'image de l'application. Entrées figées et vérifiées :
# - l'image de construction de l'application (uv, Debian trixie), déjà vérifiée par
#   scripts/chaine.py ; seules ses commandes de base (tar, cp, find) servent ici ;
# - Go 1.26.10, archive officielle, vérifiée par son empreinte (https://go.dev/dl/?mode=json) :
#   failles de net/http du 08/10/2026 (1.26.9) et GO-2026-6809 du 21/10 (crypto/x509,
#   intermédiaires compromis refusés) corrigées dans la bibliothèque standard liée ;
# - Smokescreen, source figée par son commit, vérifié à l'extraction : master du 28/09/2026,
#   qui contient les correctifs de sécurité publiés après la v0.1.0 (ADR 005 : surveillance,
#   étiquette officielle dès la prochaine version) ; dépendances du dossier vendor ;
# - écart du 12/10/2026 (amendement de l'ADR 005) : golang.org/x/net passé de v0.59.0 à
#   v0.60.0 (failles HTTP/2 du 08/10, atteintes d'après govulncheck), par le proxy des
#   modules et vérifié par sum.golang.org, puis dossier vendor régénéré ; retour au vendor du
#   commit dès qu'un commit de Smokescreen passe à x/net 0.60.0 ou plus ;
# - suite de tests de Smokescreen, puis govulncheck v1.1.4 sur le binaire : tout échec, ou
#   toute faille atteignable, arrête la construction ;
# - l'image d'exécution distroless static, signée (scripts/chaine.py).
#   docker build --file docker/proxy-sortie/Dockerfile --tag cdg-proxy:verification \
#     docker/proxy-sortie
ARG TARGETARCH

FROM ghcr.io/astral-sh/uv:0.12.24-trixie-slim@sha256:71b09e37995e49a5aed44c63be80d3e15dcb39e18e31c24a2925b66d4b6d5980 AS go-amd64
ADD --checksum=sha256:ccfe7ae822d2c2f41f4fe7c956199967a4dac22d0ee0d2b2bfdb440aab3b20c5 https://go.dev/dl/go1.26.10.linux-amd64.tar.gz /go.tar.gz

FROM ghcr.io/astral-sh/uv:0.12.24-trixie-slim@sha256:71b09e37995e49a5aed44c63be80d3e15dcb39e18e31c24a2925b66d4b6d5980 AS go-arm64
ADD --checksum=sha256:3f4227ce7b206a513e794f1393adb473a07b46e94d18bd15d1c684bbb7319797 https://go.dev/dl/go1.26.10.linux-arm64.tar.gz /go.tar.gz

FROM go-${TARGETARCH} AS construction
# autorités de confiance pour le proxy et la base de sommes des modules de Go : celles de
# l'image d'exécution, figée et signée, l'image de construction n'en ayant pas
COPY --from=gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3 /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ADD --keep-git-dir=true https://github.com/stripe/smokescreen.git#6c44698887826662733b47bbdc461aabcbb365ee /src
WORKDIR /src
# GOTOOLCHAIN=local : le Go vérifié ci-dessus, jamais un autre
ENV PATH=/usr/local/go/bin:$PATH GOPATH=/go GOTOOLCHAIN=local
# le commit extrait est bien celui qui est figé, puis l'historique disparaît
RUN test "$(cat .git/HEAD)" = "6c44698887826662733b47bbdc461aabcbb365ee" \
 && rm -rf .git \
 && tar -C /usr/local -xzf /go.tar.gz
# x/net v0.60.0 (amendement du 12/10 à l'ADR 005) : téléchargé par le proxy des modules et
# vérifié par sum.golang.org, puis le dossier vendor régénéré et vérifié
RUN GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    go get golang.org/x/net@v0.60.0 \
 && GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org go mod vendor \
 && go mod verify \
 && test "$(go list -m -f '{{.Version}}' golang.org/x/net)" = "v0.60.0"
# suite de tests de Smokescreen, sur le dossier vendor, sans téléchargement : un échec
# arrête la construction
RUN CGO_ENABLED=0 GOFLAGS=-buildvcs=false GOPROXY=off go test -mod=vendor ./...
# binaire statique ; GOPROXY=off et -mod=vendor : aucune dépendance téléchargée
RUN CGO_ENABLED=0 GOFLAGS=-buildvcs=false GOPROXY=off \
    go build -mod=vendor -trimpath -ldflags="-s -w" -o /smokescreen .
# govulncheck v1.1.4 (vérifié par sum.golang.org) sur le binaire : une faille atteignable
# arrête la construction (code 3) ; le détail reste dans le journal de construction
RUN GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    go install golang.org/x/vuln/cmd/govulncheck@v1.1.4 \
 && /go/bin/govulncheck -mode=binary -show verbose /smokescreen
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
