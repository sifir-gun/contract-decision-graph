# ADR-009 : versions successives des textes du corpus, choisies par la date d'analyse

- **Statut** : accepté, le 16/10/2026 (décisions du propriétaire, spec `docs/spec-versions-corpus.md`, plan `docs/plan-versions-corpus.md`).
- **Portée** : le corpus (`data/corpus/`), son ingestion, la table `rag_chunks`, la recherche (port `Retriever`, adaptateur PostgreSQL), le CRAG, les fiches, le contrôle des échéances et le dossier d'un contrat.

## Contexte

L'article L441-10 du Code de commerce indexé est « en vigueur du 26 avril 2019 au 01 janvier 2027 ». Légifrance publie la version suivante, « en vigueur à partir du 01 janvier 2027 », modifiée par l'ordonnance n° 2026-671 du 27 juillet 2026 (art. 2) ; la comparaison officielle des deux versions ne relève qu'une modification, au 4e alinéa du I : la facture périodique n'est plus définie par le code général des impôts mais par le code des impositions sur les biens et services. Les délais ne changent pas.

Le contrôle des échéances de la CI échouait à partir du 02/11/2026. Remplacer le fichier aurait fait citer jusqu'au 31/12/2026 un texte pas encore en vigueur, sans le signaler : le début de validité (`valid_from`) était stocké mais jamais lu. Garder les deux versions était impossible en l'état :
- un fichier par article au manifeste ;
- clé d'unicité `(domain, content_hash, embedding_model)` : les paragraphes communs à deux versions étaient écartés en silence (`ON CONFLICT DO NOTHING`) ;
- recherche groupée par référence (`DISTINCT ON (reference)`) avant la limite : elle pouvait garder l'extrait de la version périmée et faire passer le domaine à `INSUFFISANT` à tort.

## Décision

1. **Le corpus garde les versions successives d'un article** : le fichier de `file` est la première, `later_versions` du manifeste déclare les suivantes (fichier nommé par son début de validité, url, date de récupération obligatoire). L'ingestion refuse trou, chevauchement, version ouverte avant la dernière et suffixe faux (`check_chain`).
2. **Même référence pour toutes les versions** (« C. com., art. L441-10 ») : la version se déduit de la date d'analyse, scellée avec la décision ; les décisions déjà scellées gardent leur nom ; le format v2 du journal ne change pas.
3. **La date d'analyse choisit la version**, par une seule règle pure (`domain.corpus.drop_superseded`) : pour une référence qui a une version en vigueur à cette date, ses autres versions sont écartées ; sans version en vigueur, toutes restent, et le CRAG les signale.
4. **Recherche datée** : `Retriever.search(…, on)` et la recherche sans filtre de la mesure appliquent la même règle en SQL (`NOT EXISTS` sur la même référence et le même modèle), avant `DISTINCT ON` et `LIMIT`. Un test de parité compare SQL et Python sur un corpus synthétique (deux domaines, quatre dates). `mesure-recherche` juge les versions à la date du jour, écrite dans son résultat.
5. **Clé d'unicité versionnée** (migration `009`) : index unique `(domain, content_hash, embedding_model, valid_from)` en `NULLS NOT DISTINCT`, ancienne contrainte retirée. Une source sans début de validité reste dédoublonnée.
6. **Contrôle du début de validité pour tout le corpus** : une version pas encore en vigueur à la date d'analyse n'est jamais retenue ; elle est signalée quand aucune version de sa référence n'est en vigueur (« référence pas encore en vigueur à la date d'analyse … »). Constat de clôture inchangé si toutes les références pertinentes ont expiré ; « toutes hors de leur période de vigueur » sinon. Rien n'est ajouté au résumé scellé du CRAG (`ClauseRetrieval`) : le constat passe par `RetrievalTrace.findings`, champ existant.
7. **Fiches** : une fiche déclare les versions contre lesquelles elle a été relue (`versions_verifiees`) ; elle vaut sur l'intersection des périodes de ces versions, sinon de la première version de chaque article cité. Une version ajoutée sans relecture la fait expirer à son entrée en vigueur : la défense des échéances reste entière.
8. **Échéances par référence** : fin de la dernière version d'un article ; fin de la période d'une fiche.
9. **Dossier** : le texte de chaque référence est celui de sa version en vigueur à la date d'analyse (`reference_texts(on)`) ; sans date (ancien fil), une référence à plusieurs versions n'a pas de texte, jamais une version choisie au hasard.

## Options écartées

- **Remplacer le fichier sans contrôle** : citation silencieuse d'un texte à venir jusqu'au 31/12/2026.
- **Remplacer le fichier et contrôler le début de validité** : aucune référence en vigueur pour `delai_paiement` jusqu'au 31/12/2026, donc `ESCALADE` de tout constat qui pénalise un délai ; tests et démonstration touchés.
- **Seul le CRAG choisit la version** (recherche non datée, une ligne par version) : les deux versions, presque identiques, se disputent la limite `top_k` et passent devant le juge ; s'il ne garde que la périmée, `INSUFFISANT` à tort.
- **Recherche limitée aux versions en vigueur** : le signal explicite « référence expirée » disparaissait ; on ne retire pas une couche de défense parce qu'une autre (la CI des échéances) semble couvrir le cas.
- **Un nom de référence par version** : convention asymétrique (l'ancienne garde son nom), mesure de la recherche et contrôle des articles cités par l'explication à adapter.

## Conséquences

- Une analyse antidatée avant le début d'une version ne la retient plus et la signale : avant le 20/08/2026, L442-1 et les deux fiches qui la citent ; les articles du Code civil restent retenus. Aucun effet sur le jeu de démonstration ni sur les tests, datés du 25/09/2026.
- `mesure-recherche` ne se compare qu'à date égale de part et d'autre d'un changement de version.
- Le jeu d'évaluation figé est vérifié contre les textes en vigueur le 01/10/2026, date où il a été figé.

## Limites

- **Retour arrière** au-delà de la migration `009` : une image antérieure échoue à l'ingestion (son `ON CONFLICT` à trois colonnes n'a plus d'arbitre : erreur explicite), et sa recherche, qui ignore les dates de début, peut rendre en 2026 l'extrait de la version 2027 sans le signaler. Le crochet d'ingestion du chart ne tourne pas au retour arrière. Conduite à tenir dans `docs/exploitation.md` (« Migrations »).
- La CI ne connaît que les fins de validité déjà annoncées : une version à venir d'un autre article, publiée plus tard, n'est vue que par une relecture de Légifrance (piste au journal : veille des versions à venir).
- La version intermédiaire de L441-10 (ordonnance n° 2025-1247), remplacée avant d'entrer en vigueur, reste hors du corpus.
