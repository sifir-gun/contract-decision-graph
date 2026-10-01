# ADR-006 : qualité de la recherche dans le corpus

- **Statut** : en cours, depuis le 01/10/2026 (chantier « qualité de la recherche » : PR 1, évaluation, fusionnée le 01/10 ; PR 2, améliorations mesurées, branche `recherche-amelioree`).
- **Portée** : la recherche du CRAG dans le corpus (`rag_chunks`, pgvector), son évaluation, et les améliorations mesurées que retient ou écarte ce chantier. Inspiré de l'article d'Anthropic « Introducing Contextual Retrieval » (septembre 2024, https://www.anthropic.com/engineering/contextual-retrieval).

## Résultat clé : les fiches repoussent les articles hors des quatre premiers

Mesure de référence du 01/10 (détail plus bas) : **la fiche du projet sort au rang 1 pour les 21 requêtes**, avec et sans filtre. Écrite dans le vocabulaire même des requêtes, et souvent en deux extraits, elle occupe à elle seule les premiers rangs, puis les extraits multiples d'un long article prennent les suivants. **Au rang du CRAG (k = 4, avec filtre), le juge ne voit l'article de loi attendu que dans 62,7 % des cas** : pour les plafonds de responsabilité, jamais les articles 1170 ni 1231-3 du code civil, classés 5e à 8e. Le CRAG retient alors la fiche, paraphrase du projet, et jamais le texte qu'elle paraphrase. Les références attendues sont pourtant presque toutes dans le corpus proche (rappel de 96,8 % à k = 20) : elles sont trop bas, pas absentes. Ce constat ordonne la PR 2 : sa cause directe, plusieurs extraits d'une même référence parmi les quatre premiers, est traitée en premier.

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

## La mesure

`uv run python -m cdg.cli mesure-recherche [--k 1,2,4,8,20]` : sans LLM, avec le modèle d'embedding local et le corpus indexé ; commande d'administration, comme `ingest`, sans écran web (elle ne touche pas au service des contrats).

- **Au niveau de la référence**, puisque le CRAG retient des références et non des extraits. R_k : références distinctes des k premiers extraits rendus ; rappel@k = part des références attendues dans R_k ; précision@k = part de R_k qui est attendue ; taux d'échec = 1 − rappel, la mesure de l'article d'Anthropic. Fonctions pures (`domain/evaluation.py`).
- **Deux modes.** Avec le filtre du CRAG (domaine et rattachement déclaré), et sans filtre, dans tout le corpus du modèle, chaque extrait une fois (port `CorpusSearch`, réservé à la mesure ; le CRAG passe toujours par la recherche filtrée). Le mode sans filtre est celui qui départage les techniques : avec le filtre, 9 requêtes sur 21 n'ont pour candidates que leurs références attendues.
- **Deux portées** : toutes les références, et les articles de loi seuls (une fiche parmi les k premiers occupe son rang sans compter).
- **Entrées vérifiées avant de mesurer** : le jeu doit couvrir exactement les requêtes des constats du jeu de démonstration, et le corpus indexé doit être celui des fichiers, extrait par extrait (référence et texte). Sinon, erreur explicite : jamais une mesure sur un index périmé.
- **Ce qu'elle ne mesure pas** : la réécriture (LLM), le juge de pertinence (LLM) et donc les références finalement retenues. Une référence absente des k premiers ne peut pas être retenue ; une référence présente peut être écartée par le juge.

## Mesure de référence (01/10/2026, commit f1b4fff)

Modèle `intfloat/multilingual-e5-large`, 64 extraits, 21 requêtes. Rappel / précision moyens, en % ; détail requête par requête dans le journal (01/10).

| mode | portée | k = 4 (rang du CRAG) | k = 20 (rang de l'article) |
|---|---|---|---|
| avec filtre | toutes | 75,4 / 90,5 | 96,8 / 77,7 |
| avec filtre | articles seuls | 62,7 / 76,2 | 95,2 / 73,9 |
| sans filtre | toutes | 57,5 / 55,6 | 94,0 / 20,3 |
| sans filtre | articles seuls | 30,2 / 31,0 | 91,3 / 19,2 |

- **La fiche sort au rang 1 pour les 21 requêtes**, avec et sans filtre : écrite dans le vocabulaire des requêtes, ses extraits passent avant les articles.
- **Au rang du CRAG, le juge ne voit l'article attendu que dans 62,7 % des cas** ; pour les plafonds de responsabilité, jamais 1170 ni 1231-3 (rangs 5 à 8 avec filtre).
- **Les articles courts sont les plus mal classés** : 1170 et 1171, une ou deux phrases, ne sortent jamais dans les 20 premiers sans filtre.
- **Marge de progrès** : sans filtre, les références attendues sont presque toutes dans les 20 premiers (94,0 %), mais seulement 57,5 % dans les 4 premiers (30,2 % pour les articles). La condition de la PR 2 est remplie.

## Pourquoi un RAG pour un petit corpus ?

L'article d'Anthropic le dit lui-même : sous 200 000 tokens, mettre toute la base dans le prompt, avec la mise en cache, est souvent plus simple et suffisant. Notre corpus fait environ 9 900 mots, soit près de 15 000 tokens (environ 1,5 token par mot en français) : il tiendrait sans peine dans le prompt. La réponse honnête tient en quatre points.

- **Ce que ferait perdre le corpus entier dans le prompt.** Le CRAG ne rédige pas une réponse : il justifie chaque constat d'une règle par une référence, et cette justification est contrôlée par du code, avant tout LLM et après. La recherche est filtrée par le rattachement déclaré : une source ne peut justifier que les clauses pour lesquelles elle est déclarée. Chaque référence retenue est datée, et une version expirée à la date d'analyse est signalée et jamais retenue. Les requêtes, les passes et les références retenues sont scellées dans le journal d'audit, et le rejeu repart des références scellées, sans corpus ni LLM. Avec tout le corpus dans le prompt, le modèle choisirait librement parmi tous les textes ; on pourrait encore vérifier après coup que la référence citée est rattachée et en vigueur, mais la trace de ce qui a été cherché, et pour quelle clause, disparaîtrait.
- **Ce que le corpus entier ferait gagner.** À cette taille, le coût n'est pas un argument contre lui : avec la mise en cache, 15 000 tokens par contrat coûtent peu. Et il ne manquerait aucune référence au modèle, alors qu'une recherche au rang 4 en manque encore (mesure ci-dessus) : c'est le prix de la recherche, que ce chantier mesure avant de chercher à le réduire.
- **Pour quoi l'architecture est faite.** Les corpus d'un client (politiques d'achat internes, bibliothèques de clauses, jurisprudence, contrats-cadres) sont d'un autre ordre de grandeur que nos 29 sources. C'est à cette échelle que la recherche, son filtre et sa mesure servent ; le petit corpus du projet sert à les éprouver.
- **Les chiffres de l'article ne se transposent pas.** Ils portent sur des corpus en anglais (code, fiction, articles scientifiques), avec d'autres modèles d'embedding (Gemini, Voyage), au rang 20 ; les nôtres, sur des textes juridiques français, un modèle local, au rang 4 du CRAG. Seule notre mesure dit si une technique aide ici.
