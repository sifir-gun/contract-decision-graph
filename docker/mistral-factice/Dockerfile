# Image de test du serveur factice de Mistral (ADR 005, PR C3), jamais publiée. Elle part
# de l'image de l'application déjà construite et vérifiée (même Python, mêmes dépendances,
# même jeu de démonstration) et n'y ajoute que tests/mistral_factice.py, hors de l'image
# de l'application (tests/test_image.py le vérifie).
#   docker build --file docker/mistral-factice/Dockerfile \
#     --build-arg APPLICATION=cdg:verification --tag cdg-mistral-factice:verification .
ARG APPLICATION=cdg:verification
FROM ${APPLICATION}
COPY tests/mistral_factice.py /test/mistral_factice.py
ENV PYTHONPATH=/app/src:/test
EXPOSE 8080
ENTRYPOINT ["python", "-m", "mistral_factice"]
CMD []
