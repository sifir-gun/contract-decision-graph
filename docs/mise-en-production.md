# Mise en production : liste de contrôle

- **Statut** : validée le 27/09/2026. Périmètre retenu par le propriétaire, pour le moment : le déploiement (phase 2 de la feuille de route) et ce qu'il faut pour tenir dans la durée (phase 3). Rien n'est commencé.
- **Hors de cette liste, pour le moment** : l'authentification, le chiffrement des échanges et les rôles, traités à part dans la PR D (ADR 005 : authentification et entrée réseau en D1, rôles en D2) ; le traitement de vrais contrats et de leurs données personnelles (RGPD) ; la validation des règles et des fiches par un juriste ; un coffre de secrets.
- **Rappel de l'ADR 004** : l'interface n'est exposée sur aucun réseau avant l'authentification.

Chaque point dit d'où il vient et à quoi on reconnaît qu'il est fait.

## Déploiement (phase 2)

### API et hébergement

- [ ] **API FastAPI** : un adaptateur entrant de plus, sur le service des contrats, comme la CLI et l'interface web (ADR 004). *Fait quand* chaque action de l'API appelle la même méthode du service que sa commande, vérifié par `tests/test_parite.py`.
- [x] **Déploiement sur Kubernetes** (28/09, PR C2 et C3 : charts de l'application, de la base et du proxy de sortie ; installés et éprouvés à chaque pull request sur un cluster k3d de plusieurs nœuds, ADR 005) (k3s, Helm), selon la feuille de route. *Fait quand* l'application se déploie depuis le chart, sans étape manuelle, et que les migrations et l'indexation du corpus tournent comme tâches de déploiement (voir plus bas).
- [x] **Image de l'application** (28/09, PR A) : `Dockerfile` en deux étapes, bases figées par empreinte, dépendances strictement depuis uv.lock, utilisateur non root numérique, lecture seule, ni shell ni pip ; construite et vérifiée par le job `image` de la CI (ADR 005, « Image »). Publication sur ghcr.io, scan, inventaire, signature et provenance en PR B. *Fait quand* l'image construite en CI passe `tests/test_image.py`.
- [x] **Notices de licence complètes dans les images publiques** (28/09) : chaque composant porte sa licence (paquets Python, Debian, Python et ses bibliothèques liées, projet, HTMX, corpus, modèle), vérifié sur les images construites (`tests/test_licences.py`).
- [x] **Image publiée, signée et attestée** (28/09, PR B) : ghcr.io, étiquette du commit, jamais `latest` ; signature sans clé (cosign), provenance SLSA et inventaire SPDX attestés ; signatures des bases vérifiées avant la construction ; scan (Grype) bloquant sur toute faille critique ou haute corrigeable, exceptions datées ; Dependabot sur les bases (ADR 005). *Fait quand* la première publication, après la fusion, passe sa propre vérification (`cosign verify`, `gh attestation verify`).

### Santé et arrêt des pods

- [x] **Sondes de santé**, côté application (28/09, PR A : `web --port-sante`) ; leur déclaration dans le chart vient en PR C : vie (le processus répond), disponibilité (la base répond, le modèle d'embedding est chargé), et une sonde de démarrage qui laisse le temps de charger le modèle. Aujourd'hui l'interface n'a aucun point de santé, et répond 405 à `HEAD /` (journal du serveur d'aperçu, 27/09). *Fait quand* chaque sonde a son point, sans logique métier, testé en GET et en HEAD.
- [x] **Arrêt propre**, côté application (28/09, PR A : `--delai-arret`, reprise des analyses interrompues) ; la pause avant l'arrêt et le délai de grâce du pod viennent avec le chart (PR C) : à l'ordre d'arrêt, le réplica cesse d'accepter de nouvelles analyses et laisse finir celles en cours dans un délai configuré ; une analyse interrompue reprend depuis son dernier checkpoint, sans rien perdre ni sceller deux fois. Délai de grâce du pod aligné sur ce délai, et courte pause avant l'arrêt pour que le pod soit retiré du service avant de couper. *Fait quand* un test interrompt une analyse, puis la voit reprise et scellée une seule fois. Reprises comptées par contrat (28/09) : au-delà de `interrupted.max_resumes`, plus de reprise, ESCALADE vers la revue humaine ; vérifié avec de vrais processus tués à chaque reprise.

### Plusieurs copies de l'application

Le verrou du service (analyse, décision humaine, expiration) ne vaut que dans un processus (ADR 004, « Accès concurrents »).

- [x] **Décision du 27/09** : plusieurs réplicas. La création d'un contrat sera rendue sûre en base, dans la phase Kubernetes ; le verrou du service reste, pour un processus.
- [x] **Création d'un contrat sûre en base** (28/09, PR A) : verrou consultatif PostgreSQL par contrat, pour la création, la revue et l'expiration ; vérifié par deux processus réels (`tests/test_verrous.py`). Avant : rendre atomique en base la création d'un thread. Aujourd'hui, `run_contract` vérifie qu'un thread n'existe pas, puis le crée, sans verrou entre deux processus. *Fait quand* un test à deux processus lance deux analyses du même contrat et n'en obtient qu'une, la seconde refusée clairement, comme `tests/test_concurrence.py` le fait pour deux threads.
- Déjà en place, en base : le verrou consultatif et les index uniques du journal d'audit, qui empêchent un double scellement et une fourche de la chaîne.

### PostgreSQL de production

- [x] **Sauvegardes** planifiées, et une **restauration testée** (28/09, PR C3 : CloudNativePG et son greffon Barman Cloud ; le scénario du cluster de test restaure une sauvegarde dans un nouveau cluster et y passe `verify --expect-head` ; procédure dans `docs/exploitation.md`). En production, un stockage objet géré. *Fait quand* une base restaurée passe `verify --expect-head` contre la tête conservée ailleurs.
- [ ] **Tête du journal d'audit conservée hors de la base** après chaque scellement, et comparée régulièrement par `verify --expect-head`. La chaîne seule ne voit pas la suppression des derniers enregistrements (README, limites connues).
- [ ] **cert-manager sur le cluster de production** : le greffon de sauvegarde de CloudNativePG l'exige (ADR 005, documentation du greffon) ; testé avec la 1.21.2. *Fait quand* il est installé avant le greffon et que `cmctl check api` répond que l'API est prête.
- [ ] **Base de test séparée** de la base qui contient le vrai journal (déjà prévue en phase 3, spec).
- [x] **Migrations** (28/09, PR C2 et C3 : tâche Helm avant l'installation et chaque mise à jour, superutilisateur de CloudNativePG ; l'application en `app_role`, rôle géré par l'opérateur ; `setup-db` amorce une base vide) (`setup-db`) lancées comme tâche de déploiement, avant l'application, avec des identifiants administrateur distincts de ceux d'`app_role`.

### Modèle d'embedding et corpus

- [x] **Poids du modèle d'embedding**, côté image (28/09, PR B : image dédiée, figée par les empreintes de ses fichiers, testée sans réseau ; montage dans les pods en PR C) (environ 2,2 Go) livrés avec l'application, dans l'image ou sur un volume, sans téléchargement au démarrage. *Fait quand* l'application démarre sans réseau vers Hugging Face.
- [x] **Indexation du corpus** (28/09, PR C2 et C3 : tâche Helm après l'installation, plus de 10 minutes sur 2 CPU ; désactivable pour une mise à jour qui ne touche pas au corpus) (`ingest`) comme tâche de déploiement, rejouable, identifiants administrateur.

## Tenir dans la durée (phase 3)

### Suivi

- [ ] **Observabilité** : Langfuse auto-hébergé et logs structurés (feuille de route). Comme le journal d'accès aujourd'hui, jamais le texte d'un contrat.
- [ ] **Alertes** : analyse en échec, `verify` en échec, erreurs répétées du fournisseur LLM.
- [ ] **Coûts et quotas de Mistral** suivis. La consommation par nœud (tokens, latence) est déjà dans l'état de chaque contrat.

### Mise à jour du corpus

- [ ] **Processus de mise à jour** des textes publics et des fiches, avec leurs dates de validité : récupération, nettoyage, fiches revues, `ingest`, série de contrôle.
- [ ] **Première échéance** : la version de l'article L441-10 du corpus et la fiche qui la reprend cessent d'être valides le 01/01/2027 (journal, mode démonstration).
- [x] **Alerte avant l'échéance** d'une version du corpus (27/09) : le job `audit` de la CI, à chaque pull request et chaque lundi, échoue si une source cesse d'être valide dans les 60 jours, avec la source et la date (`scripts/echeances_corpus.py`).

### Évaluation en CI

- [ ] **Évaluation du système à chaque changement** (feuille de route) : issues du jeu de démonstration, sens des écarts, extractions refusées.
- [ ] **Test d'absence d'appel réseau des embeddings en CI**. Aujourd'hui il ne tourne qu'en local, sur les vrais poids. Piste notée le 26/09 : un modèle ONNX minuscule en fixture, dans un environnement Linux sans réseau.

### Limites connues (README)

- [ ] **Clauses floues** : un signal « clause ambiguë » rendu par l'extraction, qui mène à la revue humaine au lieu d'une lecture au pire.
- [ ] **Lecture des quantités** : savoir quel nombre de la citation est la quantité de la clause ; normaliser les unités de durée (semaines, jours, durées composées).
- [ ] **Listes de termes** (absences, catégories, tentatives d'instruction) : contournables par paraphrase ; à mesurer sur des formulations nouvelles.
- [ ] **Juge du CRAG** : sa variabilité, dans le sens prudent ; évaluer un modèle plus fort.
- [ ] **Ancrage externe de la tête du journal d'audit**, par un horodatage certifié.

## Décidé et fait

- [x] **Identifiants de contrat** (décision du 27/09) : une seule règle, dans le domaine (`domain/identifiers.py`), appliquée à la création d'un contrat, par la CLI comme par l'interface. Les contrats existants restent lisibles ; les trois du vrai journal suivent la règle.
