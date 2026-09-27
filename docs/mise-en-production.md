# Mise en production : liste de contrôle

- **Statut** : proposition du 27/09/2026. Périmètre retenu par le propriétaire, pour le moment : le déploiement (phase 2 de la feuille de route) et ce qu'il faut pour tenir dans la durée (phase 3). Rien n'est commencé.
- **Hors de cette liste, pour le moment** : l'authentification, le chiffrement des échanges et les rôles ; le traitement de vrais contrats et de leurs données personnelles (RGPD) ; la validation des règles et des fiches par un juriste ; un coffre de secrets.
- **Rappel de l'ADR 004** : l'interface n'est exposée sur aucun réseau avant l'authentification.

Chaque point dit d'où il vient et à quoi on reconnaît qu'il est fait.

## Déploiement (phase 2)

### API et hébergement

- [ ] **API FastAPI** : un adaptateur entrant de plus, sur le service des contrats, comme la CLI et l'interface web (ADR 004). *Fait quand* chaque action de l'API appelle la même méthode du service que sa commande, vérifié par `tests/test_parite.py`.
- [ ] **Déploiement sur Kubernetes** (k3s, Helm), selon la feuille de route. *Fait quand* l'application se déploie depuis le chart, sans étape manuelle, et que les migrations et l'indexation du corpus tournent comme tâches de déploiement (voir plus bas).

### Plusieurs copies de l'application

Le verrou du service (analyse, décision humaine, expiration) ne vaut que dans un processus (ADR 004, « Accès concurrents »).

- [ ] **Décider** : un seul réplica, où le verrou suffit ; ou plusieurs réplicas, avec une garantie en base.
- [ ] **Si plusieurs réplicas** : rendre atomique en base la création d'un thread. Aujourd'hui, `run_contract` vérifie qu'un thread n'existe pas, puis le crée, sans verrou entre deux processus. *Fait quand* un test à deux processus lance deux analyses du même contrat et n'en obtient qu'une, la seconde refusée clairement, comme `tests/test_concurrence.py` le fait pour deux threads.
- Déjà en place, en base : le verrou consultatif et les index uniques du journal d'audit, qui empêchent un double scellement et une fourche de la chaîne.

### PostgreSQL de production

- [ ] **Sauvegardes** planifiées, et une **restauration testée**. *Fait quand* une base restaurée passe `verify --expect-head` contre la tête conservée ailleurs.
- [ ] **Tête du journal d'audit conservée hors de la base** après chaque scellement, et comparée régulièrement par `verify --expect-head`. La chaîne seule ne voit pas la suppression des derniers enregistrements (README, limites connues).
- [ ] **Base de test séparée** de la base qui contient le vrai journal (déjà prévue en phase 3, spec).
- [ ] **Migrations** (`setup-db`) lancées comme tâche de déploiement, avant l'application, avec des identifiants administrateur distincts de ceux d'`app_role`.

### Modèle d'embedding et corpus

- [ ] **Poids du modèle d'embedding** (environ 2,2 Go) livrés avec l'application, dans l'image ou sur un volume, sans téléchargement au démarrage. *Fait quand* l'application démarre sans réseau vers Hugging Face.
- [ ] **Indexation du corpus** (`ingest`) comme tâche de déploiement, rejouable, identifiants administrateur.

## Tenir dans la durée (phase 3)

### Suivi

- [ ] **Observabilité** : Langfuse auto-hébergé et logs structurés (feuille de route). Comme le journal d'accès aujourd'hui, jamais le texte d'un contrat.
- [ ] **Alertes** : analyse en échec, `verify` en échec, erreurs répétées du fournisseur LLM.
- [ ] **Coûts et quotas de Mistral** suivis. La consommation par nœud (tokens, latence) est déjà dans l'état de chaque contrat.

### Mise à jour du corpus

- [ ] **Processus de mise à jour** des textes publics et des fiches, avec leurs dates de validité : récupération, nettoyage, fiches revues, `ingest`, série de contrôle.
- [ ] **Première échéance** : la version de l'article L441-10 du corpus et la fiche qui la reprend cessent d'être valides le 01/01/2027 (journal, mode démonstration).
- [ ] **Alerte avant l'échéance** d'une version du corpus.

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
