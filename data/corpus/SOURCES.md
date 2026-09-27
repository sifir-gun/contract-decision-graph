# Sources du corpus RAG

Textes publics uniquement, plus des fiches rédigées pour le projet. Chaque source porte un identifiant (`source_id`), repris dans la table `rag_chunks` et cité par les fiches. **Aucune affirmation juridique d'une fiche sans source listée ici.**

## Règle de périmètre

Un article est admis s'il **sert une règle** (`src/cdg/domain/rules/`), ou s'il est **cité directement** par un article qui sert une règle, ou s'il **définit un terme utilisé par une règle**. Les renvois de second degré ne sont pas suivis. Les renvois non récupérés sont listés ci-dessous (« Renvois non suivis »).

### Renvois non suivis

Conséquence de la règle de périmètre, ne sont pas dans le corpus :
- RGPD, art. 79 (renvoi de second degré, fichier conservé mais non ingéré) ;
- RGPD, art. 34, 35, 43, 47 à 49, 63 et 93 ;
- C. com., art. L441-1, L441-3, L441-4, L441-16 et L441-17 ;
- C. civ., art. 759 ;
- le règlement (UE) 2019/1150.

Les dérogations de l'art. 49 du RGPD (consentement explicite, exécution d'un contrat, motifs d'intérêt public…) ne sont donc pas couvertes : un contrat qui fonde un transfert sur une dérogation est classé « aucune garantie », donc bloqué. Erreur dans le sens prudent, à lever par un humain.

## Règle de rattachement (J4)

Chaque source déclare les **types de clause qu'elle peut justifier** : dans `manifest.yaml` pour un article, dans l'en-tête `clauses` pour une fiche. Les domaines d'indexation s'en déduisent. La recherche ne rend, pour une clause, que les extraits des sources rattachées à cette clause : le juge du CRAG ne peut plus retenir une source d'une autre clause du même domaine.

Un article est rattaché aux clauses des règles qu'il sert ; un article admis parce qu'un autre le cite est rattaché aux clauses de celui qui le cite, et à celles de chaque fiche qui le paraphrase ; un article qui définit un terme est rattaché aux clauses des règles qui l'emploient. Une fiche est rattachée aux clauses de son sujet. Des tests vérifient que chaque type de clause a au moins un article et une fiche, et que chaque article cité par une fiche partage une clause avec elle.

## Récupération

- Manuelle, depuis un navigateur, le **24/09/2026**. EUR-Lex et Légifrance bloquent les requêtes automatiques par une vérification anti-robots, que l'agent n'a pas contournée.
- Un fichier `.txt` UTF-8 par article, texte copié tel qu'affiché. Le nettoyage (métadonnées de version, notes, lignes d'interface) se fait à l'ingestion, par des règles testées (`src/cdg/domain/corpus.py`).

## Licences et mentions (vérifiées le 26/09/2026)

Les textes publics de `raw/` gardent leurs conditions d'origine : ils ne sont pas couverts par la licence du dépôt (AGPL-3.0, fichier `LICENSE`), qui s'applique au code, aux fiches de `fiches/` et aux contrats synthétiques de `data/contracts/`. Conditions lues sur les pages officielles, dans un navigateur, sans vérification anti-robots.

- **EUR-Lex** (RGPD). Avis juridique d'EUR-Lex, « Copyright notice » : « © European Union, 1998-2026 » ; la politique de réutilisation de la Commission repose sur la Décision 2011/833/UE ; sauf mention contraire, les documents juridiques publiés dans EUR-Lex sont réutilisables, à des fins commerciales ou non ; les textes consolidés et le contenu éditorial sont sous licence Creative Commons Attribution 4.0 : citer la source et indiquer les modifications.
  - **Mention** : © Union européenne, https://eur-lex.europa.eu, 1998-2026.
  - **Modifications** : texte copié tel qu'affiché, puis nettoyé à l'ingestion (métadonnées de version, notes, lignes d'interface : `src/cdg/domain/corpus.py`) et découpé en extraits ; les fiches en paraphrasent des passages.
- **Légifrance** (Code civil, Code de commerce, Code monétaire et financier). Pied de page du site : « Sauf mention contraire, tous les contenus de ce site sont sous licence etalab-2.0 » (Licence Ouverte 2.0). Elle exige de mentionner la paternité : la source, au moins le nom du concédant, et la date de la dernière mise à jour de l'information réutilisée, sans suggérer de caution officielle.
  - **Mention** : source Légifrance, https://www.legifrance.gouv.fr (Secrétariat général du Gouvernement, direction de l'information légale et administrative).
  - **Date de la dernière mise à jour** : la ligne « Version en vigueur… » de Légifrance, conservée dans chaque fichier de `raw/` (métadonnée de version à l'ingestion) ; date de récupération dans le tableau ci-dessous.
  - **Aucune caution** : le projet est indépendant ; ni Légifrance, ni l'État, ni l'Union européenne ne l'approuvent.

## Textes publics

| source_id | Article | Fichier (`data/corpus/raw/`) | Périmètre | Clauses rattachées | Récupéré le |
| --- | --- | --- | --- | --- | --- |
| `rgpd` | art. 4 | `rgpd/art-4.txt` | Définit des termes utilisés par la règle conformité (« données à caractère personnel », « sous-traitant ») | `donnees_personnelles`, `accord_traitement_donnees`, `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 28 | `rgpd/art-28.txt` | Sert la règle conformité : accord de traitement (sous-traitant) | `accord_traitement_donnees` | 24/09/2026 |
| `rgpd` | art. 32 | `rgpd/art-32.txt` | Cité par l'art. 28 | `accord_traitement_donnees` | 24/09/2026 |
| `rgpd` | art. 33 | `rgpd/art-33.txt` | Cité par l'art. 28 (« articles 32 à 36 ») | `accord_traitement_donnees` | 24/09/2026 |
| `rgpd` | art. 36 | `rgpd/art-36.txt` | Cité par l'art. 28 (« articles 32 à 36 ») | `accord_traitement_donnees` | 24/09/2026 |
| `rgpd` | art. 40 | `rgpd/art-40.txt` | Cité par les art. 28 et 46 (codes de conduite) | `accord_traitement_donnees`, `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 42 | `rgpd/art-42.txt` | Cité par les art. 28 et 46 (certification) | `accord_traitement_donnees`, `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 44 | `rgpd/art-44.txt` | Sert la règle transfert hors UE | `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 45 | `rgpd/art-45.txt` | Sert la règle transfert hors UE (décision d'adéquation) | `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 46 | `rgpd/art-46.txt` | Sert la règle transfert hors UE (garanties appropriées) | `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 79 | `rgpd/art-79.txt` | **Hors périmètre, non ingéré** : cité seulement par l'art. 82, lui-même cité par l'art. 28 (second degré) | — | 24/09/2026 |
| `rgpd` | art. 82 | `rgpd/art-82.txt` | Cité par l'art. 28 | `accord_traitement_donnees` | 24/09/2026 |
| `rgpd` | art. 83 | `rgpd/art-83.txt` | Cité par l'art. 28 ; paraphrasé aussi par la fiche transferts (sanctions) | `accord_traitement_donnees`, `transfert_hors_ue` | 24/09/2026 |
| `rgpd` | art. 84 | `rgpd/art-84.txt` | Cité par l'art. 28 | `accord_traitement_donnees` | 24/09/2026 |
| `code-commerce` | L441-10 | `code-commerce/L441-10.txt` | Sert la règle financière : délai de paiement par l'acheteur (délai supplétif, délais maximaux). **Version en vigueur jusqu'au 01/01/2027** | `delai_paiement` | 24/09/2026 |
| `code-commerce` | L442-1 | `code-commerce/L442-1.txt` | Sert les règles opérationnelle (préavis, rupture brutale) et juridique (déséquilibre significatif) | `responsabilite_acheteur`, `responsabilite_fournisseur`, `preavis_resiliation` | 24/09/2026 |
| `code-civil` | 1170 | `code-civil/1170.txt` | Sert la règle juridique : clause privant de sa substance l'obligation essentielle | `responsabilite_acheteur`, `responsabilite_fournisseur` | 24/09/2026 |
| `code-civil` | 1171 | `code-civil/1171.txt` | Sert la règle juridique : déséquilibre significatif | `responsabilite_acheteur`, `responsabilite_fournisseur` | 24/09/2026 |
| `code-civil` | 1231-3 | `code-civil/1231-3.txt` | Sert la règle juridique : dommages prévisibles, plafonds de responsabilité | `responsabilite_acheteur`, `responsabilite_fournisseur` | 24/09/2026 |
| `code-civil` | 1231-5 | `code-civil/1231-5.txt` | Sert la règle financière : pénalités d'exécution dues par le fournisseur (clause pénale) | `penalites_execution` | 25/09/2026 |
| `code-civil` | 1210 | `code-civil/1210.txt` | Sert la règle opérationnelle : durée d'engagement (engagements perpétuels) | `duree_engagement` | 24/09/2026 |
| `code-civil` | 1211 | `code-civil/1211.txt` | Sert la règle opérationnelle : préavis de résiliation | `preavis_resiliation` | 24/09/2026 |
| `code-monetaire-financier` | L112-2 | `code-monetaire-financier/L112-2.txt` | Sert la règle financière : révision de prix (indexation) | `revision_prix` | 24/09/2026 |

URL officielles :
- `rgpd` : https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32016R0679
- `code-commerce` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000005634379/
- `code-civil` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006070721/
- `code-monetaire-financier` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006072026/

## Fiches de référence

Rédigées pour ce projet, dans `fiches/`. Chaque fiche commence par « Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique » et comporte deux sections : « Ce que dit le texte », uniquement des paraphrases fidèles, chacune avec l'article qu'elle paraphrase (`source_id`, `art.`) ; « Comment le projet l'applique », les règles et seuils du projet, présentés comme des choix de politique d'achat. Des tests vérifient la structure, et que chaque citation désigne un article admis ci-dessus. Paraphrases vérifiées mot à mot contre les fichiers bruts le 25/09/2026.

| source_id | Fiche | Clauses rattachées | Articles cités |
| --- | --- | --- | --- |
| `fiche-sous-traitance-rgpd` | `fiches/sous-traitance-rgpd.md` | `donnees_personnelles`, `accord_traitement_donnees` | RGPD 4, 28, 32, 33, 36, 40, 42, 82, 83, 84 |
| `fiche-transferts-hors-ue` | `fiches/transferts-hors-ue.md` | `transfert_hors_ue` | RGPD 40, 42, 44, 45, 46, 83 |
| `fiche-responsabilite-plafonds` | `fiches/responsabilite-plafonds.md` | `responsabilite_acheteur`, `responsabilite_fournisseur` | C. civ. 1170, 1171, 1231-3 ; C. com. L442-1 |
| `fiche-penalites-execution` | `fiches/penalites-execution.md` | `penalites_execution` | C. civ. 1231-5 |
| `fiche-delais-paiement` | `fiches/delais-paiement.md` | `delai_paiement` | C. com. L441-10 |
| `fiche-revision-prix` | `fiches/revision-prix.md` | `revision_prix` | C. mon. fin. L112-2 |
| `fiche-duree-preavis` | `fiches/duree-preavis.md` | `duree_engagement`, `preavis_resiliation` | C. civ. 1210, 1211 ; C. com. L442-1 |
