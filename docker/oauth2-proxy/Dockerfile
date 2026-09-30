# oauth2-proxy (ADR 005, PR D1, option B) : image construite par notre chaîne à partir du
# binaire publié dans la release GitHub v7.15.4 (MIT). L'image officielle (quay.io) n'est ni
# signée ni attestée, et ses binaires ne correspondent pas aux empreintes publiées dans la
# release (relevé du 30/09/2026, ADR 005). Entrées figées et vérifiées :
# - l'archive de la release, par son empreinte ; le binaire extrait, comparé à l'empreinte
#   publiée pour son architecture : la construction échoue s'il diffère ;
# - la licence d'oauth2-proxy, au commit de l'étiquette v7.15.4 ;
# - les licences des modules Go liés au binaire (go version -m), téléchargés à leur version
#   exacte et vérifiés par la base de sommes de Go ; un module sans licence l'arrête ;
# - Go, archive officielle vérifiée par son empreinte, pour lire le binaire et télécharger
#   les modules ; l'image de construction et l'image d'exécution distroless static, signées
#   (scripts/chaine.py).
#   docker build --file docker/oauth2-proxy/Dockerfile --tag cdg-oauth2-proxy:verification \
#     docker/oauth2-proxy
ARG TARGETARCH

FROM ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:c40e42de0e1516439b5139d7a214657dbffbbd3d2366661347efae7b8337c197 AS archive-amd64
ADD --checksum=sha256:4fbe902189aab713d9c0519b90a645032d4636ecb523dc36f5cc312d8ebef1e2 https://github.com/oauth2-proxy/oauth2-proxy/releases/download/v7.15.4/oauth2-proxy-v7.15.4.linux-amd64.tar.gz /archive.tar.gz
ADD --checksum=sha256:d0f743b33e8d8945e6b1f432edd15785c70507121d6e2a723b21285eddf8b57b https://go.dev/dl/go1.26.8.linux-amd64.tar.gz /go.tar.gz
ENV BINAIRE_SHA256=d2cc1a814d189ad3c5d3ac712dcfbea43176afacae814c8f64e8dded885f5c41

FROM ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:c40e42de0e1516439b5139d7a214657dbffbbd3d2366661347efae7b8337c197 AS archive-arm64
ADD --checksum=sha256:b3fb0b61aecfb4b776dccb6daff406676ed0ead74245dc6e4bdf71f993eb1710 https://github.com/oauth2-proxy/oauth2-proxy/releases/download/v7.15.4/oauth2-proxy-v7.15.4.linux-arm64.tar.gz /archive.tar.gz
ADD --checksum=sha256:211ffced9dcb9633a55eac6364816ec0ddd951389a740e88fa8b3337971bdda0 https://go.dev/dl/go1.26.8.linux-arm64.tar.gz /go.tar.gz
ENV BINAIRE_SHA256=dd70759fdd210ea10e16d44a8072356f74789da2d58fd1a7f3b9bb08922b9c40

FROM archive-${TARGETARCH} AS binaire
RUN tar -xzf /archive.tar.gz -C /tmp \
 && mv /tmp/oauth2-proxy-v7.15.4.linux-*/oauth2-proxy /oauth2-proxy \
 && echo "${BINAIRE_SHA256}  /oauth2-proxy" | sha256sum --check --strict

FROM binaire AS licences
# autorités de confiance pour le proxy des modules de Go : celles de l'image d'exécution,
# figée et signée, l'image de construction n'en ayant pas (uv embarque les siennes)
COPY --from=gcr.io/distroless/static-debian13:nonroot@sha256:e2e927ec666bae08560abb3c55d0659eceabb657f56b6782ab500a9fc7f555e3 /etc/ssl/certs/ca-certificates.crt /etc/ssl/certs/ca-certificates.crt
ADD --checksum=sha256:89807acf2309bd285f033404ee78581602f3cd9b819a16ac2f0e5f60ff4a473e https://raw.githubusercontent.com/oauth2-proxy/oauth2-proxy/81ff034fe2ff3246e670c694b02e3267d1ae46bc/LICENSE /licences/oauth2-proxy/LICENSE
RUN tar -C /usr/local -xzf /go.tar.gz \
 && mkdir -p /licences/go \
 && cp /usr/local/go/LICENSE /licences/go/
# modules liés au binaire, remplacements compris ; la licence de chacun, à sa version
RUN export PATH=/usr/local/go/bin:$PATH GOPATH=/go GOTOOLCHAIN=local GOSUMDB=sum.golang.org \
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
      org.opencontainers.image.description="oauth2-proxy v7.15.4, binaire publié vérifié par son empreinte"
COPY --from=binaire /oauth2-proxy /oauth2-proxy
COPY --from=licences /licences /licences
USER 65532:65532
EXPOSE 4180
ENTRYPOINT ["/oauth2-proxy"]
