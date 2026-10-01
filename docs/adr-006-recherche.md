# ADR-006 : qualité de la recherche dans le corpus

- **Statut** : en cours, depuis le 01/10/2026 (chantier « qualité de la recherche », PR 1 : évaluation, branche `evaluation-recherche`).
- **Portée** : la recherche du CRAG dans le corpus (`rag_chunks`, pgvector), son évaluation, et les améliorations mesurées que retient ou écarte ce chantier. Inspiré de l'article d'Anthropic « Introducing Contextual Retrieval » (septembre 2024).

## Contexte

Le CRAG justifie chaque constat d'une règle par une référence du corpus : une recherche par clause qui porte un constat, filtrée par domaine et par le rattachement déclaré de chaque source (J4), puis un juge de pertinence (modèle léger), une réécriture au besoin, et les références retenues, datées et scellées. Jusqu'ici, rien ne mesurait la recherche seule : les séries réelles mesurent la décision, au bout de la chaîne.

Périmètre figé : deux PR au plus. La première mesure sans rien changer ; la seconde n'a lieu que si la mesure laisse une marge de progrès, et chaque technique n'est gardée que si la mesure progresse. Toute idée nouvelle va au journal comme piste.

## Le jeu d'évaluation

`data/evaluation/recherche.yaml` : une entrée par requête du CRAG que donnent les constats du jeu de démonstration (24 constats portés par une clause, 21 requêtes distinctes), avec les références qui justifient vraiment le constat. Seule la première requête d'une clause est mesurée : elle est écrite par le code (`crag.clause_query`) ; la réécriture appelle le LLM.

- **Pas de circularité.** La recherche filtre déjà par le rattachement déclaré (manifeste, en-tête des fiches, `SOURCES.md`). Les références attendues ne sont donc jamais tirées de ce rattachement, mais de la lecture du texte de chaque article ou fiche. Critère : une référence est attendue quand son texte énonce la règle qui porte sur l'objet de la clause et sur la situation que décrit le constat ; une définition, une sanction ou une procédure voisine ne l'est pas. Une fiche est jugée sur son corps, comme un article.
- **Justifié mot pour mot.** Chaque référence attendue porte un extrait de son texte, vérifié par un test, et son type (article de loi ou fiche) ; les références proches écartées sont motivées (pièges lexicaux : « pénalités logistiques » ou « pénalités de retard de paiement » pour les pénalités d'exécution, clauses contractuelles types de l'article 28 du RGPD pour un transfert).
- **Deux écarts avec le rattachement déclaré** :
  - l'article 1211 du code civil est attendu pour une durée d'engagement non chiffrée (elle renvoie au régime du contrat à durée indéterminée), alors qu'il n'est déclaré que pour le préavis ;
  - l'article 28 du RGPD est attendu pour une localisation des données non précisée (contrat 02) : le prestataire y traite des données pour le compte de l'acheteur, donc en sous-traitant, et le point 3 a) impose des instructions documentées pour les transferts ; il n'est déclaré que pour l'accord de traitement.

  Avec le filtre, le rappel de ces deux requêtes plafonne donc à 2/3 : la mesure le montre, elle ne le corrige pas.
- **Établi et validé par des non-juristes, comme les fiches.** Le jeu a été proposé par l'agent de développement, puis relu et validé le 01/10/2026 par le propriétaire du projet, avant toute mesure ; il est figé depuis. C'est un jeu de régression pour la recherche, pas une expertise juridique : il dit si une technique rapproche les textes qu'un lecteur attentif rattache à un constat, pas si ces textes suffisent en droit. Le modifier change la mesure : toute modification est motivée au journal, chiffres avant et après.
- **Petit, donc grossier.** 21 requêtes : une requête vaut environ 5 points de rappel moyen. Les résultats sont toujours donnés requête par requête, en plus des moyennes.
