# Spec · Décisions de la Cour de cassation au corpus (JUDILIBRE)

Version du 20 octobre 2026. Conçue en séance de brainstorming avec le propriétaire, section par section, chaque section validée. Source de vérité pour ce chantier ; `docs/spec-phase1.md`, `docs/spec-versions-corpus.md` et `docs/spec-profils-sous-traitance.md` le restent pour tout ce que ce document ne modifie pas. Décision d'architecture à écrire : ADR 011.

## Objectif et périmètre

La fiche `fiche-responsabilite-plafonds` renvoie deux fois à « la jurisprudence, hors corpus » : pour l'effet d'une faute lourde sur un plafond conventionnel, et pour l'articulation entre l'article 1171 du code civil et l'article L442-1 du code de commerce. Le but retenu par le propriétaire est de **combler ces renvois** quand une décision de la Cour de cassation les tranche, sans bâtir de chaîne de jurisprudence.

Ce que le chantier doit livrer, et rien de plus :

- un nouveau type de source du corpus, la **décision**, indexée comme un article : retenue par le CRAG, scellée comme référence d'un constat, affichée au dossier avec les mentions qu'exigent les conditions d'utilisation ;
- une seule décision admise : **Cass. com., 13 mai 2026, n° 24-17.137** (publiée au Bulletin, ECLI:FR:CCASS:2026:CO00224), qui tranche l'articulation entre 1171 et L442-1 ;
- un script lancé par le propriétaire qui copie une fois l'extrait depuis l'API JUDILIBRE, puis le relit chaque semaine ; l'ingestion n'appelle jamais l'API ;
- un contrôle de l'explication étendu aux numéros de pourvoi et aux ECLI ;
- la fiche des plafonds mise à jour, le jeu d'évaluation figé amendé et mesuré avant et après.

**Ce que dit l'arrêt** (sommaire, lu sur courdecassation.fr le 20/10/2026) : l'article 1171 du code civil, interprété à la lumière des travaux parlementaires de la loi n° 2018-287 du 20 avril 2018, « ne s'applique pas aux contrats conclus par une personne exerçant des activités de production, de distribution ou de services, excepté si l'application de l'article L. 442-1 du code de commerce à ces contrats est exclue par une autre disposition ». Textes appliqués : article 1171 du code civil, article L. 442-1 du code de commerce.

## Non-objectifs

- Toute autre décision. En particulier, la faute lourde et le plafond conventionnel restent « hors corpus » : les arrêts publiés trouvés (Com., 22 oct. 1996, n° 93-18.632 ; Ch. mixte, 22 avr. 2005, n° 03-14.112, textes appliqués : art. 1150 ; Com., 29 juin 2010, n° 09-11.841, art. 1131, 1134 et 1147) ont été rendus sous les textes d'avant la réforme de 2016, que le corpus ne contient pas. La clause pénale non plus : Com., 11 déc. 2024, n° 23-15.744, rendu sur des prêts bancaires, n'ajoute presque rien à l'article 1231-5.
- Les arrêts inédits, les décisions des cours d'appel et des autres juridictions, les rapprochements de jurisprudence (Com., 26 janv. 2022, n° 20-16.782, cité par l'arrêt, va dans « Renvois non suivis »).
- Le texte intégral d'une décision, son en-tête, l'exposé du litige, les moyens, le dispositif, les moyens annexés, le titrage et les documents associés (rapports, avis, communiqués).
- Un appel à l'API à l'ingestion, dans la CI ou dans le cluster ; une synchronisation automatique ; un secret dans la CI.
- Le profil `sous_traitance` (PR 2 de son chantier, sur une autre branche) : ni ses fichiers, ni ses rattachements, ni son jeu d'évaluation. Voir « Écarts ».
- Le retrait de l'article 1171 du corpus ou de son rattachement : aucune règle ne change.
- La détection d'un revirement de jurisprudence : aucune donnée de l'API ne le signale ; c'est une relecture humaine.

## Constats vérifiés (sources officielles, 20/10/2026)

**API JUDILIBRE** (spécification OpenAPI publique, version 1.2.5, dépôt `Cour-de-cassation/judilibre-search`, commit 058beff du 16/10/2026 ; README du même dépôt).

- `GET /decision?id=` rend le « texte intégral et pseudonymisé », découpé en zones par positions de caractères (`introduction`, `expose`, `moyens`, `motivations`, `dispositif`, `annexes`), et des métadonnées : juridiction, chambre, formation, pourvoi(s), ECLI, code NAC, publication (`b` : Bulletin), numéro au bulletin, solution, date de la décision, date de dernière mise à jour (`update_date`), sommaire (`summary`), titrage (`themes`), textes appliqués (`visa`), rapprochements, décision attaquée, dates clés, fichiers associés, `partial`, `particularInterest`, `to_be_deleted`. Sans `resolve_references=true`, juridiction, chambre, formation et publication sont rendues sous forme de clés (`cc`, `comm`, `fs`, `b`).
- `to_be_deleted: true` : « la décision correspondante doit être supprimée des archives ou des bases de données du réutilisateur ».
- Accès : compte PISTE, CGU acceptées pour JUDILIBRE, application raccordée ; en-tête `KeyId` (README) ; racines `https://sandbox-api.piste.gouv.fr/cassation/judilibre/v1.0` et `https://api.piste.gouv.fr/cassation/judilibre/v1.0`. Les CGU parlent d'une authentification OAuth 2.0 à chaque requête : le script utilise l'en-tête `KeyId` documenté par le README ; si PISTE le refuse, arrêt et signalement, sans contournement.
- API en bêta : contenu, complétude, fraîcheur et disponibilité non garantis ; quotas. « Les données disponibles via l'API sont celles de la version du site de la Cour de cassation. »

**Conditions d'utilisation** (« CGU_open_data_V8 », version du 6 janvier 2022, sur PISTE et sur courdecassation.fr ; PDF de PISTE, SHA-256 `73f99f811475a0f9c7eb3e18ff431c37a41672e4c549fce64415eae71a713554`).

- Licence Ouverte 2.0, complétée par les CGU, qui priment.
- Ne pas altérer ni dénaturer ; pour chaque décision réutilisée, mentionner la juridiction, la formation, le siège et la date du prononcé, « ces informations ne pouvant être détachées de la décision elle-même ».
- Mentionner que les données proviennent de la base de données Open Data tenue par la Cour de cassation, et la date de leur dernière mise à jour ; tenir ses données à jour ; « Il est recommandé au réutilisateur de ne pas excéder un délai de 72 (soixante-douze) heures entre deux mises à jour de sa base de données ».
- Le réutilisateur est responsable de son traitement (RGPD) ; il ne modifie jamais la pseudonymisation ; il transmet à la Cour toute demande de correction d'un défaut d'occultation.
- Interdiction de toute réutilisation des données d'identité des magistrats et des membres du greffe qui évaluerait, analyserait, comparerait ou prédirait leurs pratiques (L111-13 du code de l'organisation judiciaire, sanctions pénales).
- Clés d'authentification strictement réservées à l'application, jamais publiées.

**Données personnelles.** Sont occultés le nom et les prénoms des personnes physiques parties ou tiers (« [X] », « [Adresse 1] »). Restent en clair, dans l'arrêt du 13 mai 2026 : le président, les conseillers, l'avocat général, les greffières et les avocats aux Conseils (une quinzaine de noms), et les sociétés parties. Le sommaire et la « Réponse de la Cour » (§ 6 à 12) ne contiennent aucun nom de personne physique.

## Règle de périmètre des décisions

Ajoutée à `SOURCES.md`. Une décision est admise si :

1. elle est rendue par la Cour de cassation et publiée au Bulletin ;
2. ses textes appliqués comprennent au moins un article admis du corpus dont une version contenue dans le corpus était en vigueur à la date de la décision ;
3. elle tranche la question que pose une règle du projet ; le motif est écrit dans `SOURCES.md`, comme pour un article.

Pour l'arrêt du 13 mai 2026 : la condition 2 est remplie par l'article 1171 (version du corpus en vigueur depuis le 01/10/2018). L442-1 ne compte pas : la version du corpus est en vigueur depuis le 20/08/2026, après l'arrêt ; l'arrêt le cite dans son état d'alors, et le libellé « exerçant des activités de production, de distribution ou de services » figure à l'identique dans la version du corpus.

**Rattachement.** Déclaré au manifeste. Chaque clause déclarée doit être rattachée à au moins un article admis des textes appliqués de la décision (testé). L'arrêt du 13 mai 2026 est rattaché à `responsabilite_acheteur` et à `responsabilite_fournisseur`, choix du propriétaire (cohérence avec le rattachement de 1171 et de L442-1) ; pas à `preavis_resiliation`, auquel L442-1 n'est rattaché que pour la rupture brutale, que l'arrêt ne traite pas. La mesure dira s'il repousse un article attendu pour le plafond du fournisseur (« Mesure de la recherche », règle d'arrêt).

**Article 1171.** Il garde sa place au corpus et son rattachement : l'arrêt dit quand il s'applique, la fiche l'explique, et aucune règle ne change.

## Extrait conservé et garde sur les données personnelles

- Seuls le sommaire (`summary`) et la zone `motivations` sont conservés, mot pour mot, découpés selon les positions que rend l'API.
- **Garde, testée** : l'extrait est refusé, par une erreur qui nomme la décision, le motif et sa position (jamais le texte), s'il contient une marque d'occultation (`[` ou `]`), une civilité suivie d'un mot à majuscule initiale (« M. », « MM. », « Mme », « Mmes », « Mlle », « Mlles », « Me »), ou une forme de société d'avocats (« SCP », « SELARL », « SELAS »).
- Le corpus ne contient alors aucune donnée personnelle issue de JUDILIBRE : seules restent nommées des personnes morales.

## Récupération : copie une fois, relecture chaque semaine

L'ingestion (`ingest`) ne lit que les fichiers du dépôt, comme aujourd'hui : ni réseau, ni secret, ni changement dans le cluster ni dans la CI.

**Script `scripts/judilibre.py`**, lancé par le propriétaire (commande réseau), bibliothèque standard seulement (`urllib.request`, `json`) et `python-dotenv`, déjà dépendance du projet. Clé `KeyId` de l'application PISTE de production dans `.env`, variable `JUDILIBRE_KEY_ID`, jamais commitée ; `.env.example` porte la variable vide. Racine de production.

- `copier --id <identifiant>` : `GET /decision?id=…`, sans `resolve_references` (clés). Refuse, par une erreur explicite : une clé absente, avant tout appel ; une réponse HTTP en erreur (code rendu, jamais le corps) ; une décision dont la juridiction n'est pas `cc`, non publiée au Bulletin (`b` absent de `publication`), partielle (`partial`) ou marquée `to_be_deleted` ; une décision à plusieurs pourvois (`numbers`), dont le nom de fichier ne serait pas défini ; un sommaire ou une zone `motivations` absents ou vides ; un extrait que la garde refuse. Écrit `data/corpus/raw/judilibre/<pourvoi>.txt` (format ci-dessous) et affiche l'entrée à ajouter au manifeste. La réponse de l'API, qui contient des noms, n'est jamais écrite sur disque.
- `verifier` : pour chaque décision du manifeste, nouvel appel ; erreur explicite « décision retirée de la base Open Data : la retirer du corpus » sur une réponse 404 ou `to_be_deleted` ; « décision modifiée le … : relancer copier, relire la fiche » si le fichier reconstruit diffère du fichier commité ; erreur réseau explicite. Sinon, affiche la date du jour, que le propriétaire reporte dans `verifiee_le` au manifeste. Ne modifie jamais un fichier.
- Tests sur des réponses fictives (fixtures JSON synthétiques, aucun nom réel), appel réseau remplacé par une doublure.

À confirmer au premier appel de `copier`, sinon arrêt et signalement : que l'identifiant de la page courdecassation.fr (`6a043ef6cdc6046d4791a00d`) est celui de l'API ; que la zone `motivations` couvre la « Réponse de la Cour » (§ 6 à 12) et rien de l'en-tête ni des moyens.

**Format du fichier** (libellés fixes, une valeur par ligne, puis deux parties) :

```text
Décision n° 24-17.137
Juridiction : Cour de cassation
Siège : Paris
Chambre : chambre commerciale, financière et économique
Formation : formation de section
Date : 13 mai 2026
ECLI : ECLI:FR:CCASS:2026:CO00224
Publication : Bulletin
Textes appliqués : Article 1171 du code civil. | Article L. 442-1 du code de commerce.
Identifiant JUDILIBRE : 6a043ef6cdc6046d4791a00d
Dernière mise à jour : AAAA-MM-JJ
Source : base de données Open Data tenue par la Cour de cassation (JUDILIBRE)

Sommaire
…

Motivation
…
```

Libellés de chambre et de formation tirés d'une table du code (aujourd'hui `comm` et `fs` seulement, libellés de la recherche Judilibre) ; une clé inconnue est une erreur explicite. Le siège de la Cour de cassation est Paris (adresse des CGU, art. VIII).

**Échéance de relecture.** Nouvelle option facultative de `scripts/echeances_corpus.py`, `--relecture-jours N` ; `--jours` reste obligatoire. Une seule commande, dans le job `audit` de la CI et dans `check.sh` : `uv run --no-sync python scripts/echeances_corpus.py --jours 60 --relecture-jours 7` (`tests/test_ci.py` compare les lignes entières ; la constante `COMMAND` de `tests/test_echeances_corpus.py` suit). Sans l'option, aucune relecture n'est contrôlée. Avec elle : code 1, avec la décision et sa date, si `verifiee_le` manque, a plus de N jours à la date de référence (`--date`, défaut : aujourd'hui), ou est postérieure à cette date ; le résultat JSON gagne une liste `relectures`. Le déclenchement planifié du lundi le voit aussi. Les tests du contrôle utilisent un manifeste de test et des dates fixes, jamais le vrai `verifiee_le` comparé à la date du jour : seul le job `audit` dépend de la date réelle. Délai choisi par le propriétaire ; écart à la recommandation de 72 h des CGU (« Écarts »).

**Retrait.** Décision retirée ou marquée `to_be_deleted` : suppression du fichier, de l'entrée du manifeste, de la citation de la fiche et de l'attente du jeu d'évaluation, dans une PR. Les décisions scellées gardent le nom de la référence ; le dossier affiche « Texte de la référence absente du corpus » (comportement actuel). Limite : l'historique git garde l'extrait, sans donnée personnelle.

**Rectification par la Cour.** Le fichier est remplacé par `copier` : ce n'est pas une nouvelle version au sens de l'ADR 009 (le droit ne change pas) ; `ingest` réindexe l'extrait par l'empreinte du texte embarqué, comme aujourd'hui.

## Entrée dans le corpus

- **Manifeste** : nouvelle source `judilibre` (`format: judilibre`, `title`, `directory: judilibre`, `file: "{decision}.txt"`), avec une clé `decisions` à côté de `articles` :

  ```yaml
  judilibre:
    format: judilibre
    title: Décisions de la Cour de cassation (base Open Data, JUDILIBRE)
    directory: judilibre
    file: "{decision}.txt"
    decisions:
      "24-17.137":
        id: 6a043ef6cdc6046d4791a00d
        clauses: [responsabilite_acheteur, responsabilite_fournisseur]
        retrieved_at: AAAA-MM-JJ
        verifiee_le: AAAA-MM-JJ
  ```

  La source `judilibre` n'a pas de clé `articles` : `Manifest` (`__post_init__`, `files`, `admits`, `excluded_files`) lit `articles` avec une valeur vide par défaut, et une méthode distincte rend les fichiers et les entrées des décisions ; `files()` ne rend jamais un fichier de décision (sinon `ingestion.articles()` chercherait un parseur `judilibre`). La règle « tout fichier de `raw/` figure au manifeste, ingéré ou exclu » reste vraie et couvre les décisions.
- **Lecture** : `parse_judilibre`, fonction pure de `domain/corpus.py`, testée sur le vrai fichier ; un dataclass `Decision`, distinct d'`Article`, lu par une fonction d'ingestion distincte d'`ingestion.articles()`, qui ne rend toujours que des articles. Erreur qui nomme le fichier si un libellé manque, si le pourvoi diffère de la clé du manifeste, si une chambre ou une formation est inconnue, si aucun texte appliqué n'est un article admis en vigueur à la date de la décision (condition 2), ou si une clause déclarée n'est rattachée à aucun article admis des textes appliqués. Un texte appliqué que la table des codes ne reconnaît pas reste dans l'en-tête sans compter pour le périmètre.
- **Nom de la référence**, écrit par le code : « Cass. com., 13 mai 2026, n° 24-17.137 » (abréviation de la chambre, date en français, pourvoi). Même nom dans `rag_chunks`, le CRAG, le journal et le dossier.
- **Date** : début de validité à la date de la décision, aucune fin ; aucune version suivante. Une analyse datée d'avant le 13/05/2026 ne la retient pas et la signale (ADR 009, inchangé). `source_validities` rend chaque décision, sans fin (`None`) : elle n'expire jamais au sens des échéances ; sa relecture est contrôlée à part.
- **Extraits** : `chunk_max_words` (300), en-tête embarqué réduit à la référence (ADR 006), garde des 512 tokens ; texte = sommaire puis motivation. **Aucune migration** : `article`, `amendment` et `note` restent vides.
- **Textes du dossier** : `reference_texts(on)` rend, pour une décision, `source`, `texte` (l'extrait) et une clé nouvelle `mentions`, une chaîne d'une ligne écrite par le code depuis l'en-tête du fichier : Cour de cassation (Paris), chambre, formation, date, pourvoi, ECLI, « base de données Open Data tenue par la Cour de cassation », date de dernière mise à jour, « extrait : sommaire et motivation ». La clé n'existe que pour une décision : les entrées des articles et des fiches ne changent pas (`{source, texte}`), et le type reste `dict[str, dict[str, str]]` ; les lecteurs utilisent `.get("mentions")`.
- **Fiche `responsabilite-plafonds`** :
  - format des citations étendu : `(source : judilibre, n° 24-17.137)` ; `corpus.citations` rend aussi ces citations, et les tests des fiches (paraphrase sourcée, source admise, clause commune) acceptent une décision admise ;
  - la décision entre dans la période de validité de la fiche : `fiche_window` reçoit pour elle une seule période, de la date de la décision sans fin ; `versions_verifiees` ne peut pas nommer une décision (erreur qui nomme la fiche) ;
  - « Ce que dit le texte » : paraphrase du sommaire, sourcée ;
  - « Comment le projet l'applique » : la ligne sur l'articulation devient « Entre personnes exerçant des activités de production, de distribution ou de services, le déséquilibre significatif s'examine au regard de l'article L442-1, et non de l'article 1171 (source : judilibre, n° 24-17.137 ; code-commerce, art. L442-1) ; le projet ne recherche pas si une autre disposition exclut l'application de L442-1. » La ligne sur la faute lourde ne change pas ;
  - période de validité inchangée : l'arrêt (13/05/2026) précède la version de L442-1 que la fiche cite déjà (20/08/2026).
- **`SOURCES.md`** : règle de périmètre des décisions ; renvoi non suivi (Com., 26 janv. 2022, n° 20-16.782) ; section de licence JUDILIBRE (Licence Ouverte 2.0 et CGU V8 ; mention de la source et de la date de dernière mise à jour ; modification : extrait, sommaire et motivation ; aucune caution de la Cour) ; tableau « Décisions » (décision, fichier, périmètre, clauses rattachées, récupérée le), dont les rattachements sont comparés au manifeste par le test qui le fait déjà pour les articles ; la date de relecture ne vit qu'au manifeste (`verifiee_le`) ; fiche des plafonds : décision citée.

## Usage dans l'analyse

- **CRAG et justification : code inchangé.** Recherche filtrée par domaine et rattachement, juge de pertinence, `generate` ; l'arrêt retenu est scellé comme toute référence et compte comme référence en vigueur (`domain/justification.py`). Un extrait par référence parmi les quatre premiers (`crag.search.distinct_references`). Prompt du juge inchangé.
- **Démonstration** : `DeclaredCrag` retient toutes les sources déclarées ; l'arrêt apparaît dans chaque constat de responsabilité du jeu fournisseur, sans code nouveau. Tests du web en démonstration mis à jour s'ils figent des références.
- **Dossier** : l'écran affiche `mentions` sous le texte de la référence, échappées comme tout texte ; `show` les rend dans son JSON.
- **Serveur MCP** : `ReferenceView` gagne `mentions` (donnée du corpus écrite par le code, en liste blanche ; `None` pour un article ou une fiche) ; le texte des références n'est toujours pas rendu. `tests/test_parite.py` inchangé dans son principe.
- **Contrôle de l'explication** (`domain/explanation.py`, PR A) : une fonction relève dans un texte les numéros de pourvoi (`\b\d{2}-\d{2}\.\d{3}\b`) et les ECLI (`ECLI:` suivi de ses quatre champs). Un numéro de pourvoi n'est citable que s'il figure dans le nom d'une référence retenue pour la clause du constat, ou dans le texte du constat ; sinon, refus. **Tout ECLI est refusé** : les noms des références retenues n'en portent pas, et le domaine ne lit pas le corpus ; `FindingToExplain` et `ExplanationRequest` ne changent pas. Le gabarit passe ce contrôle ; le prompt dit déjà « Ne cite aucun article ni aucune référence absents des références du constat ». Un faux positif mène au gabarit, dans le sens prudent. Limite : un arrêt nommé sans numéro (« l'arrêt Chronopost ») n'est pas détecté.
- **Journal d'audit, rejeu, traces : inchangés.** Format v3 ; une référence scellée est un nom ; le rejeu repart des références scellées, sans corpus ; aucun attribut de trace nouveau.

## Mesure de la recherche et jeu figé

- **Mesure de référence d'abord**, avant toute modification du corpus : `mesure-recherche --profil fournisseur`, résultat au journal.
- **Date des mesures.** `mesure-recherche` juge les versions à la date du jour, sans option de date (`cli.py`), et le chantier n'en ajoute pas. Les trois mesures se font toutes avant le 01/01/2027 : entre le 20/08/2026 (version de L442-1) et le 31/12/2026 (fin de la première version de L441-10), aucune version du corpus ne change, et l'arrêt (13/05/2026) est retenu à toute date de cette période ; elles se comparent donc comme à date égale (ADR 009). La date de chaque mesure est écrite dans son résultat ; une mesure au 01/01/2027 ou après : arrêt et signalement.
- **Jeu figé amendé** (ADR 006 : modification motivée au journal, chiffres avant et après) :
  - type `decision` ajouté à `type` (`article`, `fiche`, `decision`) ; la citation de chaque référence attendue est vérifiée mot pour mot dans son texte (`tests/test_evaluation_recherche.py`) ;
  - `responsabilite_acheteur` (demo-06) : l'arrêt est **attendu** ; justification : il énonce quel texte régit le déséquilibre significatif entre professionnels, situation du constat ; citation : « ne s'applique pas aux contrats conclus par une personne exerçant des activités de production, de distribution ou de services ». L'article 1171 **reste attendu** : le jeu juge le texte d'une source, pas son applicabilité en droit (ADR 006) ;
  - `responsabilite_fournisseur` (trois requêtes) : l'arrêt est **écarté**, même motif que l'article 1171, ajouté à `plafond_fournisseur_ecartees` ;
  - requêtes et contrats inchangés (21 requêtes) : `EvaluationSetOutdated` non touché.
- **Portées** : « toutes » compte l'arrêt ; « articles seuls » ne compte que les articles : une décision parmi les k premiers y occupe son rang sans compter, comme une fiche. **Changement de code** : `evaluation._mode` et `evaluation.measure` n'excluent aujourd'hui que les fiches ; ils excluront toute référence qui n'est pas un article (fiches et décisions), test `test_articles_seuls_les_fiches_occupent_leur_rang_sans_compter` étendu aux décisions. Fait avant la mesure 2.
- **Trois mesures**, requête par requête, au journal :
  1. avant : corpus actuel, jeu actuel ;
  2. **arrêt seul** : corpus avec l'arrêt, fiche encore inchangée, jeu non amendé (effet du seul concurrent nouveau) ;
  3. corpus avec l'arrêt et la fiche modifiée, jeu amendé : nouvelle mesure de référence.
- **Règle d'arrêt** : si la mesure 2 ou la mesure 3 fait perdre, par rapport à la mesure 1, à l'une des quatre requêtes de responsabilité un article attendu parmi les quatre premiers, avec le filtre, arrêt avant tout push et chiffres apportés au propriétaire, qui choisit de garder les deux rattachements ou de revenir à `responsabilite_acheteur` seul (mesure 2), ou de reprendre la fiche (mesure 3). Aucun repli automatique.

## Erreurs

Aucun repli silencieux ; chaque cas est testé.

- `copier` : clé absente ; réponse HTTP en erreur ; juridiction autre que `cc` ; décision non publiée au Bulletin, partielle ou marquée `to_be_deleted` ; sommaire ou zone `motivations` absents ; extrait refusé par la garde.
- `verifier` : décision retirée ; décision modifiée ; erreur réseau ; aucun fichier modifié.
- Ingestion : fichier absent ou non déclaré ; libellé d'en-tête absent ; pourvoi différent de la clé ; chambre ou formation inconnue ; condition 2 non remplie ; clause rattachée à aucun article admis des textes appliqués.
- Échéances, avec `--relecture-jours` : `verifiee_le` absente, de plus de N jours, ou postérieure à la date de référence : code 1, décision et date nommées.
- Manifeste : `versions_verifiees` d'une fiche qui nomme une décision : erreur qui nomme la fiche.
- Fiche : décision citée non admise, ou sans clause commune avec la fiche : test en échec.
- Explication : pourvoi ou ECLI non retenu : refus, nouveaux essais, puis gabarit.
- Dossier : décision retirée depuis le scellement : « Texte de la référence absente du corpus ».

## Critères d'acceptation

Chacun est une vérification observable ; la PR où il devient vérifiable est indiquée entre crochets.

1. [A, B] `./scripts/check.sh` se termine par « check.sh : SUCCÈS » ; couverture au moins égale au seuil `fail_under` (98 %).
2. [A] Pour demo-06, une explication qui cite « n° 20-16.782 » est refusée ; [B] une explication qui cite l'arrêt retenu est acceptée.
3. [B] Après `ingest`, l'index contient « Cass. com., 13 mai 2026, n° 24-17.137 » dans le domaine juridique, rattaché à `responsabilite_acheteur` et `responsabilite_fournisseur` ; `mesure-recherche --profil fournisseur` passe.
4. [B] En `web --demo`, le constat « responsabilité de l'acheteur illimitée » de demo-06 cite l'arrêt, et le dossier affiche ses mentions (Cour de cassation, Paris, chambre, formation, date, pourvoi, ECLI, source, date de dernière mise à jour).
5. [B] Les treize contrats du jeu rendent les mêmes décisions qu'avant (`tests/test_demo.py`).
6. [B] Le vrai journal se vérifie et se rejoue à l'identique (`uv run pytest -m journal_reel --journal-reel`).
7. [B] `data/corpus/raw/judilibre/24-17.137.txt` ne contient ni `[`, ni `]`, ni civilité suivie d'un nom, ni forme de société d'avocats (test).
8. [B] Sur un manifeste de test, `echeances_corpus.py --jours 60 --relecture-jours 7 --date 2026-11-09` échoue avec `verifiee_le: 2026-11-01` (8 jours) et passe avec `verifiee_le: 2026-11-02` (7 jours) ; la ligne de la CI et celle de `check.sh` sont identiques (`tests/test_ci.py`).
9. [B] Les trois mesures sont au journal, requête par requête, datées d'avant le 01/01/2027, et la règle d'arrêt est appliquée.
10. [B] `SOURCES.md` porte la règle de périmètre, la licence, les mentions et les dates ; la fiche ne contient plus « L'articulation entre l'article 1171 du code civil et l'article L442-1 du code de commerce relève de la jurisprudence, hors corpus ».
11. [B] L'outil MCP `consulter` rend `mentions` pour l'arrêt ; `tests/test_parite.py` passe.
12. [B] Aucune réponse de l'API, aucune clé, aucun nom de magistrat, de greffier ou d'avocat dans le dépôt (relecture de la PR et test du critère 7).

## Découpage

Une branche par PR, chacune verte avec `check.sh` avant d'être poussée par le propriétaire.

1. **PR A, contrôle de l'explication.** Pourvois et ECLI dans `domain/explanation.py`, tests ; `CLAUDE.md` (contrôles de l'explication) ; journal. Indépendante : sans décision au corpus, tout arrêt numéroté cité est refusé.
2. **PR B, source « décision »**, dans cet ordre :
   1. mesure de référence, au journal ;
   2. `scripts/judilibre.py` et ses tests sur réponses fictives ; `.env.example` ;
   3. le propriétaire lance `copier` (commande réseau) ; l'extrait est relu, puis commité ;
   4. lecture, manifeste, ingestion, mentions au dossier et au MCP, échéance de relecture (CI et `check.sh`), portée « articles seuls » de la mesure ;
   5. `ingest`, puis mesure 2 (arrêt seul, fiche inchangée, jeu non amendé), règle d'arrêt ;
   6. fiche et `SOURCES.md`, jeu amendé ; `ingest`, puis mesure 3, règle d'arrêt ;
   7. ADR 011, `CLAUDE.md` (sources du corpus, commandes, `.env`), `docs/exploitation.md` (relecture hebdomadaire : `verifier`, puis `verifiee_le`, puis PR).

## Dépendances vérifiées

- **Aucune nouvelle bibliothèque** : `urllib.request` et `json` (bibliothèque standard, Python 3.12) ; `python-dotenv`, déjà dans `pyproject.toml` (`>=1.2.3`). `pyproject.toml` et `uv.lock` inchangés.
- **API JUDILIBRE** : spécification OpenAPI 1.2.5 (`public/JUDILIBRE-public-swagger.json`, commit 058beff du 16/10/2026) et README du dépôt `Cour-de-cassation/judilibre-search`, lus le 20/10/2026. Version bêta : toute différence au premier appel (identifiant, zones, authentification) est signalée avant de contourner quoi que ce soit.
- **Conditions d'utilisation** : « CGU_open_data_V8 », version du 6 janvier 2022, lues le 20/10/2026 sur PISTE (PDF) et sur courdecassation.fr (même date de mise à jour).
- **Décision** : page courdecassation.fr de l'arrêt n° 24-17.137 (`/decision/6a043ef6cdc6046d4791a00d`), lue le 20/10/2026 dans un navigateur, sans vérification anti-robots ; libellés de chambre et de formation lus dans le formulaire de la recherche Judilibre le même jour.
- **Code confronté le 20/10/2026** : `domain/corpus.py`, `application/ingestion.py`, `application/crag.py`, `application/evaluation.py`, `application/service.py`, `domain/explanation.py`, `adapters/demo/references.py`, `adapters/mcp/presentation.py`, `adapters/web/presentation.py`, `scripts/echeances_corpus.py`, `data/evaluation/recherche.yaml`.

## Écarts avec l'existant

À valider par le propriétaire avant tout code, puis consignés dans l'ADR 011 :

- **Périmètre figé depuis la v1.1** (02/10/2026) : rouvert pour ce chantier, à la demande écrite du propriétaire.
- **Nouveau type de source** : le corpus ne contenait que des articles et des fiches ; règle de périmètre, rattachement, format des citations des fiches, `reference_texts` (champ `mentions`) et `ReferenceView` du serveur MCP changent.
- **Contrôle de l'explication plus strict** : une explication qui citerait aujourd'hui un numéro de pourvoi ou un ECLI serait refusée.
- **Relecture tous les 7 jours, contre 72 h recommandées par les CGU** : une recommandation, pas une obligation ; une relecture à la main, contrôlée par la CI, ne peut pas tenir 72 h.
- **Fusions bloquées** : le job `audit (pip-audit)`, exigé par la protection de `main`, échoue dès que la relecture a plus de 7 jours ; toute PR, Dependabot comprises, attend une relance de `verifier`.
- **Jeu figé de l'ADR 006 amendé** : une référence attendue et trois références écartées ajoutées, chiffres avant et après au journal.
- **Extrait plutôt que décision entière** : lecture des CGU (ne pas altérer ni dénaturer ; Licence Ouverte 2.0 : extraire est permis) ; extrait mot pour mot, mentions jointes, lien vers la décision complète. C'est une interprétation de non-juristes ; le propriétaire peut interroger la Cour à l'adresse de contact de l'API que donnent les CGU (art. XI.A). Cette adresse n'est recopiée ni dans le journal, ni dans le README, ni dans les fichiers que vérifie `tests/test_donnees_fictives.py`.
- **Profil sous-traitance, à trancher dans son chantier** : sa spec étend 1171 à `responsabilite_donneur_ordre` et `responsabilite_sous_traitant`, alors que l'arrêt écarte 1171 entre professionnels ; et l'arrêt pourrait y être rattaché après la fusion de la PR 2. Rien n'est modifié ici.
