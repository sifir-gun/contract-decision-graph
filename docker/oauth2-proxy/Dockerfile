# oauth2-proxy (ADR 005, PR D1) : jusqu'au 12/10/2026, image construite à partir du binaire
# publié dans la release GitHub v7.15.4 (option B). Amendement du 12/10/2026 à l'ADR 005 :
# aucune version publiée n'est compilée avec Go 1.26.9, qui corrige les failles de net/http
# du 08/10 (atteintes d'après govulncheck) ; compilée par notre chaîne depuis les sources de
# l'étiquette v7.15.5 (MIT), jusqu'à ce qu'une version officielle soit compilée avec Go
# 1.26.9 ou plus (retour à l'option B). Entrées figées et vérifiées :
# - Go 1.26.9, archive officielle, vérifiée par son empreinte (https://go.dev/dl/?mode=json) ;
# - les sources de l'étiquette v7.15.5 publiée : le commit extrait est vérifié ;
# - golang.org/x/net passé de v0.59.0 à v0.60.0, et les autres modules à leur version de
#   go.sum, téléchargés par le proxy des modules et vérifiés par sum.golang.org ;
# - suite de tests d'oauth2-proxy, puis govulncheck v1.1.4 sur le binaire : tout échec, ou
#   toute faille atteignable, arrête la construction ;
# - la licence d'oauth2-proxy, à l'étiquette, vérifiée par son empreinte ; les licences des
#   modules Go liés au binaire (go version -m), à leur version exacte ; un module sans
#   licence arrête la construction ;
# - l'image de construction et l'image d'exécution distroless static, signées
#   (scripts/chaine.py).
#   docker build --file docker/oauth2-proxy/Dockerfile --tag cdg-oauth2-proxy:verification \
#     docker/oauth2-proxy
ARG TARGETARCH

FROM ghcr.io/astral-sh/uv:0.12.24-trixie-slim@sha256:71b09e37995e49a5aed44c63be80d3e15dcb39e18e31c24a2925b66d4b6d5980 AS go-amd64
ADD --checksum=sha256:42d158b4d8f7b61ac0a830567c940a86098fb7aac52e467a5ebec03ef5cc2f8d https://go.dev/dl/go1.26.9.linux-amd64.tar.gz /go.tar.gz

FROM ghcr.io/astral-sh/uv:0.12.24-trixie-slim@sha256:71b09e37995e49a5aed44c63be80d3e15dcb39e18e31c24a2925b66d4b6d5980 AS go-arm64
ADD --checksum=sha256:4a97373d49fcacdcf3694fea368a500b00ee3e963974f3e7514132717632f052 https://go.dev/dl/go1.26.9.linux-arm64.tar.gz /go.tar.gz

FROM go-${TARGETARCH} AS binaire
# autorités de confiance pour le proxy et la base de sommes des modules de Go : celles de
# l'image d'exécution, figée et signée, l'image de construction n'en ayant pas
COPY --from=gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3 /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ADD --keep-git-dir=true https://github.com/oauth2-proxy/oauth2-proxy.git#v7.15.5 /src
WORKDIR /src
# GOTOOLCHAIN=local : le Go vérifié ci-dessus, jamais un autre
ENV PATH=/usr/local/go/bin:$PATH GOPATH=/go GOTOOLCHAIN=local
# le commit extrait est celui de l'étiquette v7.15.5 publiée (étiquette annotée 6e4bd00e…),
# puis l'historique disparaît
RUN test "$(cat .git/HEAD)" = "bea3f04bf87b93f5354f392fe2216a88e6031350" \
 && rm -rf .git \
 && tar -C /usr/local -xzf /go.tar.gz
# x/net v0.60.0 (amendement du 12/10 à l'ADR 005) et les modules de go.sum : téléchargés par
# le proxy des modules et vérifiés par sum.golang.org
RUN GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    go get golang.org/x/net@v0.60.0 \
 && GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org go mod download \
 && go mod verify \
 && test "$(go list -m -f '{{.Version}}' golang.org/x/net)" = "v0.60.0"
# suite de tests d'oauth2-proxy, sans téléchargement (sans -race, qui exige cgo) : un échec
# arrête la construction
RUN CGO_ENABLED=0 GOFLAGS=-buildvcs=false GOPROXY=off go test ./...
# binaire statique, à la version de l'étiquette (comme dist.sh de la release)
RUN CGO_ENABLED=0 GOFLAGS=-buildvcs=false GOPROXY=off \
    go build -trimpath -ldflags="-X github.com/oauth2-proxy/oauth2-proxy/v7/pkg/version.VERSION=v7.15.5" -o /oauth2-proxy .
# govulncheck v1.1.4 (vérifié par sum.golang.org) sur le binaire : une faille atteignable
# arrête la construction (code 3) ; le détail reste dans le journal de construction
RUN GOPROXY=https://proxy.golang.org GOSUMDB=sum.golang.org \
    go install golang.org/x/vuln/cmd/govulncheck@v1.1.4 \
 && /go/bin/govulncheck -mode=binary -show verbose /oauth2-proxy

FROM binaire AS licences
# licence d'oauth2-proxy, à l'étiquette, vérifiée par son empreinte ; licence de Go
RUN mkdir -p /licences/oauth2-proxy /licences/go \
 && cp /src/LICENSE /licences/oauth2-proxy/LICENSE \
 && echo "89807acf2309bd285f033404ee78581602f3cd9b819a16ac2f0e5f60ff4a473e  /licences/oauth2-proxy/LICENSE" | sha256sum --check --strict \
 && cp /usr/local/go/LICENSE /licences/go/
# modules liés au binaire, remplacements compris ; la licence de chacun, à sa version
RUN export GOSUMDB=sum.golang.org \
 && go version -m /oauth2-proxy \
    | awk '$1 == "dep" {if (m) print m; m = $2 "@" $3} $1 == "=>" {m = $2 "@" $3} END {if (m) print m}' \
    | sort -u > /licences/modules.txt \
 && missing="" \
 && for module in $(cat /licences/modules.txt); do \
      dir="$(go mod download -json "$module" | sed -n 's/^\t"Dir": "\(.*\)",$/\1/p')"; \
      found="$(find "$dir" -maxdepth 1 -type f \( -iname 'LICENSE*' -o -iname 'LICENCE*' \
        -o -iname 'COPYING*' -o -iname 'NOTICE*' -o -iname 'COPYRIGHT*' \))"; \
      if [ -z "$found" ]; then missing="$missing $module"; continue; fi; \
      mkdir -p "/licences/modules/$module" && cp $found "/licences/modules/$module/"; \
    done \
 && if [ -n "$missing" ]; then echo "module sans licence :$missing" >&2; exit 1; fi

FROM gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3
LABEL org.opencontainers.image.source="https://github.com/sifir-gun/contract-decision-graph" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="contract-decision-graph oauth2-proxy" \
      org.opencontainers.image.description="oauth2-proxy v7.15.5, compilé depuis les sources de l'étiquette (Go 1.26.9, x/net 0.60.0)"
COPY --from=binaire /oauth2-proxy /oauth2-proxy
COPY --from=licences /licences /licences
USER 65532:65532
EXPOSE 4180
ENTRYPOINT ["/oauth2-proxy"]
