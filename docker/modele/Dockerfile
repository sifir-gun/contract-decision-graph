# Image du modèle d'embedding (ADR 005, PR B) : les poids seuls, sans système ni outil,
# montés en lecture seule dans le pod (volume `image`, PR C) à EMBEDDING_CACHE_DIR.
#
# Contexte : le cache rempli par `cdg.cli fetch-embedding-model`, vérifié par
# `scripts/modele.py verifier` avant la construction ; liens physiques compris (la copie à
# plat que charge l'adaptateur), sans les artefacts du téléchargement
# (`Dockerfile.dockerignore`). La notice de licence vient du contexte nommé `notice`.
#
#   docker build --file docker/modele/Dockerfile --build-context notice=docker/modele \
#     --tag cdg-modele:verification "$EMBEDDING_CACHE_DIR"

FROM scratch

LABEL org.opencontainers.image.source="https://github.com/sifir-gun/contract-decision-graph" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.title="multilingual-e5-large (ONNX)" \
      org.opencontainers.image.description="Poids du modèle d'embedding de contract-decision-graph"

COPY --from=notice LICENCE-MODELE.md /LICENCE-MODELE.md
COPY . /
