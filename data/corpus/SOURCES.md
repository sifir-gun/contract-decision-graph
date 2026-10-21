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
- le règlement (UE) 2019/1150 ;
- CPI, art. L121-7-1 et L131-3-1 à L131-3-3, et les autres articles des livres Ier et III du code (cités par L111-1) ;
- règlement (UE) 1302/2014 : l'appendice J-1 de l'annexe, dont l'index [30] désigne une norme payante, et le point 2.3 de l'annexe (cités par le point 4.2.10.2.1) ;
- règlement (UE) 748/2012 : la sous-partie Q et les points 21.A.90B et 21.A.431B de l'annexe I, l'article 9, paragraphe 4, du règlement, et le règlement (UE) 965/2012 (cités par le point 21.A.307).

Les dérogations de l'art. 49 du RGPD (consentement explicite, exécution d'un contrat, motifs d'intérêt public…) ne sont donc pas couvertes : un contrat qui fonde un transfert sur une dérogation est classé « aucune garantie », donc bloqué. Erreur dans le sens prudent, à lever par un humain.

### Sources écartées (profil sous-traitance)

- C. civ., art. 1344 : il porte sur la mise en demeure de payer ;
- la norme EN 45545-2, désignée par l'appendice J-1 du règlement (UE) 1302/2014 : payante, nommée seulement, dans « Comment le projet l'applique » ;
- la CS-25 de l'EASA : reproduction autorisée sous réserve de citer la source, mais texte en anglais seulement, alors que le corpus est en français ; citée par son nom ;
- le naval : aucune source sectorielle libre, les textes de l'Organisation maritime internationale ne l'étant pas ; la fiche sur la certification le dit.

## Versions successives (ADR 009)

Un article peut avoir plusieurs versions au corpus : un fichier par version, la première nommée par le motif `file` du manifeste, les suivantes déclarées dans `later_versions` et nommées par leur début de validité (`L441-10_2027-01-01.txt`). Les versions s'enchaînent sans trou ni chevauchement (contrôlé à l'ingestion), portent la même référence et le même rattachement. La date d'analyse choisit la version en vigueur ; une version pas encore en vigueur n'est jamais retenue. Une fiche déclare dans son en-tête (`versions_verifiees`) les versions contre lesquelles elle a été relue ; sans déclaration, elle ne couvre que la première version de chaque article cité, et expire à l'entrée en vigueur d'une version qu'elle n'a pas relue.

## Règle de rattachement (J4)

Chaque source déclare les **types de clause qu'elle peut justifier** : dans `manifest.yaml` pour un article, dans l'en-tête `clauses` pour une fiche. Les domaines d'indexation s'en déduisent. La recherche ne rend, pour une clause, que les extraits des sources rattachées à cette clause : le juge du CRAG ne peut plus retenir une source d'une autre clause du même domaine.

Un article est rattaché aux clauses des règles qu'il sert ; un article admis parce qu'un autre le cite est rattaché aux clauses de celui qui le cite, et à celles de chaque fiche qui le paraphrase ; un article qui définit un terme est rattaché aux clauses des règles qui l'emploient. Une fiche est rattachée aux clauses de son sujet. Des tests vérifient que chaque type de clause, de chaque profil, a au moins un article et une fiche, et que chaque article cité par une fiche partage une clause avec elle. Un article qui sert les règles des deux profils est rattaché aux clauses des deux (C. civ., art. 1170, 1171, 1231-3 et 1231-5).

## Récupération

- Manuelle, depuis un navigateur, le **24/09/2026**. EUR-Lex et Légifrance bloquent les requêtes automatiques par une vérification anti-robots, que l'agent n'a pas contournée.
- Version de L441-10 en vigueur à partir du 01/01/2027 : le **16/10/2026**, lue par l'agent dans un navigateur, page par page, sans contourner la vérification anti-robots, et transcrite à la demande du propriétaire depuis les éléments affichés du bloc de l'article ; empreinte SHA-256 du fichier identique à celle calculée dans la page.
- Points d'annexe EUR-Lex du profil sous-traitance (règlement (UE) 1302/2014, annexe, point 4.2.10.2.1 ; règlement (UE) 748/2012, annexe I, point 21.A.307) : le **20/10/2026**, copiés par l'agent dans un navigateur, depuis la page HTML de la version consolidée en français (`CELEX:02014R1302-20250427`, consolidée au 27/04/2025 ; `CELEX:02012R0748-20260807`, consolidée au 07/08/2026) : texte affiché par la page, de la ligne de titre du point à la ligne qui précède la marque de consolidation suivante (« ▼B », « ▼M17 »).
- Sept articles Légifrance du profil sous-traitance (CPI, art. L111-1, L112-2, L122-4 et L131-3 ; C. civ., art. 1231 et 1231-1 ; C. assur., art. L124-3) : le **21/10/2026**. La vérification anti-robots de Légifrance repère le navigateur piloté par l'agent ; elle n'a pas été contournée. Le propriétaire a enregistré les sept pages d'article dans son propre navigateur le 21/10/2026 ; l'agent en a extrait le bloc de l'article tel qu'affiché (titre, ligne de version, texte modificateur, texte), et la hiérarchie affichée. Le texte est identique à celui de l'API Légifrance (PISTE, `/consult/getArticle`) pour les sept, et au PDF officiel du Code de la propriété intellectuelle du 17/10/2026 pour les quatre du CPI. Versions relues le 20/10/2026 par l'API Légifrance de production (`/consult/getArticleWithIdAndNum`) : une seule en vigueur pour chacun, aucune version à venir. Hiérarchies relues par l'API le 21/10/2026, avec les six du Code civil déjà au corpus : identiques aux pages et au manifeste ; pour L124-3, l'API garde aussi l'intitulé du titre II abrogé le 01/07/2012 (« … de dommages non maritimes ») : l'intitulé retenu est celui en vigueur, affiché par la page.
- Un fichier `.txt` UTF-8 par article, texte copié tel qu'affiché. Le nettoyage (métadonnées de version, notes, lignes d'interface) se fait à l'ingestion, par des règles testées (`src/cdg/domain/corpus.py`).

## Licences et mentions (vérifiées le 26/09/2026, relues le 20/10/2026)

Les textes publics de `raw/` gardent leurs conditions d'origine : ils ne sont pas couverts par la licence du dépôt (AGPL-3.0, fichier `LICENSE`), qui s'applique au code, aux fiches de `fiches/` et aux contrats synthétiques de `data/contracts/`. Conditions lues sur les pages officielles, dans un navigateur, sans vérification anti-robots. Relues le 20/10/2026, à l'ajout des sources du profil sous-traitance : mêmes conditions.

- **EUR-Lex** (RGPD ; règlements (UE) 1302/2014 et 748/2012, versions consolidées). Avis juridique d'EUR-Lex, « Copyright notice » : « © European Union, 1998-2026 » ; la politique de réutilisation de la Commission repose sur la Décision 2011/833/UE ; sauf mention contraire, les documents juridiques publiés dans EUR-Lex sont réutilisables, à des fins commerciales ou non ; les textes consolidés et le contenu éditorial sont sous licence Creative Commons Attribution 4.0 : citer la source et indiquer les modifications.
  - **Mention** : © Union européenne, https://eur-lex.europa.eu, 1998-2026.
  - **Modifications** : texte copié tel qu'affiché, puis nettoyé à l'ingestion (métadonnées de version, notes, lignes d'interface : `src/cdg/domain/corpus.py`) et découpé en extraits ; les fiches en paraphrasent des passages.
  - **Modifications des points d'annexe** : seul le point qui sert une règle est copié ; ses marques de consolidation seules sur leur ligne (« ▼M12 », « ▼M7 ») sont retirées à l'ingestion, son numéro et son intitulé lus sur sa première ligne.
  - **Version** : date de la version consolidée copiée, dans le tableau ci-dessous et au manifeste.
- **Légifrance** (Code civil, Code de commerce, Code monétaire et financier, Code de la propriété intellectuelle, Code des assurances). Pied de page du site : « Sauf mention contraire, tous les contenus de ce site sont sous licence etalab-2.0 » (Licence Ouverte 2.0). Elle exige de mentionner la paternité : la source, au moins le nom du concédant, et la date de la dernière mise à jour de l'information réutilisée, sans suggérer de caution officielle.
  - **Mention** : source Légifrance, https://www.legifrance.gouv.fr (Secrétariat général du Gouvernement, direction de l'information légale et administrative).
  - **Date de la dernière mise à jour** : la ligne « Version en vigueur… » de Légifrance, conservée dans chaque fichier de `raw/` (métadonnée de version à l'ingestion) ; date de récupération dans le tableau ci-dessous.
  - **Aucune caution** : le projet est indépendant ; ni Légifrance, ni l'État, ni l'Union européenne ne l'approuvent.
- **EASA** (CS-25) : citée par son nom seulement, dans une fiche ; aucun texte au corpus.

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
| `code-commerce` | L441-10 | `code-commerce/L441-10.txt` | Sert la règle financière : délai de paiement par l'acheteur (délai supplétif, délais maximaux). **Version en vigueur du 26/04/2019 au 01/01/2027** | `delai_paiement` | 24/09/2026 |
| `code-commerce` | L441-10 | `code-commerce/L441-10_2027-01-01.txt` | Version suivante (ordonnance n° 2026-671 du 27 juillet 2026, art. 2) : seul le 4e alinéa du I change, la facture périodique relevant du code des impositions sur les biens et services. **Version en vigueur à partir du 01/01/2027** | `delai_paiement` | 16/10/2026 |
| `code-commerce` | L442-1 | `code-commerce/L442-1.txt` | Sert les règles opérationnelle (préavis, rupture brutale) et juridique (déséquilibre significatif) | `responsabilite_acheteur`, `responsabilite_fournisseur`, `preavis_resiliation` | 24/09/2026 |
| `code-civil` | 1170 | `code-civil/1170.txt` | Sert la règle juridique : clause privant de sa substance l'obligation essentielle ; profils fournisseur et sous-traitance | `responsabilite_acheteur`, `responsabilite_fournisseur`, `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` | 24/09/2026 |
| `code-civil` | 1171 | `code-civil/1171.txt` | Sert la règle juridique : déséquilibre significatif ; profils fournisseur et sous-traitance | `responsabilite_acheteur`, `responsabilite_fournisseur`, `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` | 24/09/2026 |
| `code-civil` | 1231-3 | `code-civil/1231-3.txt` | Sert la règle juridique : dommages prévisibles, plafonds de responsabilité ; profils fournisseur et sous-traitance | `responsabilite_acheteur`, `responsabilite_fournisseur`, `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` | 24/09/2026 |
| `code-civil` | 1231-5 | `code-civil/1231-5.txt` | Sert la règle financière : pénalités d'exécution dues par le fournisseur, pénalités de retard d'exécution dues par le sous-traitant (clause pénale) | `penalites_execution`, `penalites_retard_execution` | 25/09/2026 |
| `code-civil` | 1210 | `code-civil/1210.txt` | Sert la règle opérationnelle : durée d'engagement (engagements perpétuels) | `duree_engagement` | 24/09/2026 |
| `code-civil` | 1211 | `code-civil/1211.txt` | Sert la règle opérationnelle : préavis de résiliation | `preavis_resiliation` | 24/09/2026 |
| `code-monetaire-financier` | L112-2 | `code-monetaire-financier/L112-2.txt` | Sert la règle financière : révision de prix (indexation) | `revision_prix` | 24/09/2026 |
| `code-civil` | 1231 | `code-civil/1231.txt` | Sert la règle opérationnelle de la sous-traitance : délai d'exécution (dommages et intérêts après mise en demeure). **Version en vigueur depuis le 01/10/2016** | `delai_execution` | 21/10/2026 |
| `code-civil` | 1231-1 | `code-civil/1231-1.txt` | Sert la règle opérationnelle de la sous-traitance : délai d'exécution (dommages et intérêts à raison du retard dans l'exécution). **Version en vigueur depuis le 01/10/2016** | `delai_execution` | 21/10/2026 |
| `code-propriete-intellectuelle` | L111-1 | `code-propriete-intellectuelle/L111-1.txt` | Sert la règle juridique de la sous-traitance : protection des dessins et documents remis (droit de l'auteur du seul fait de la création ; contrat de louage d'ouvrage sans dérogation). **Version en vigueur depuis le 27/12/2020** | `protection_design` | 21/10/2026 |
| `code-propriete-intellectuelle` | L112-2 | `code-propriete-intellectuelle/L112-2.txt` | Sert la règle juridique de la sous-traitance : œuvres de l'esprit (dessin, architecture, arts appliqués, plans). **Version en vigueur depuis le 11/05/1994** | `protection_design` | 21/10/2026 |
| `code-propriete-intellectuelle` | L122-4 | `code-propriete-intellectuelle/L122-4.txt` | Sert la règle juridique de la sous-traitance : reproduction ou adaptation sans consentement de l'auteur, illicite. **Version en vigueur depuis le 03/07/1992** | `protection_design` | 21/10/2026 |
| `code-propriete-intellectuelle` | L131-3 | `code-propriete-intellectuelle/L131-3.txt` | Sert la règle juridique de la sous-traitance : transmission des droits (mention distincte, domaine d'exploitation délimité). **Version en vigueur depuis le 03/07/1992** | `protection_design` | 21/10/2026 |
| `code-assurances` | L124-3 | `code-assurances/L124-3.txt` | Sert la règle conformité de la sous-traitance : assurance du sous-traitant (action directe du tiers lésé contre l'assureur de responsabilité civile). **Version en vigueur depuis le 19/12/2007** | `assurance_sous_traitant` | 21/10/2026 |
| `sti-materiel-roulant` | annexe, point 4.2.10.2.1 | `sti-materiel-roulant/point-4.2.10.2.1.txt` | Sert la règle conformité de la sous-traitance : obligation de certification (matériaux du matériel roulant ferroviaire, comportement au feu, rapports d'essai). **Version consolidée au 27/04/2025** (`CELEX:02014R1302-20250427`) | `obligation_certification` | 20/10/2026 |
| `certification-navigabilite` | annexe I, point 21.A.307 | `certification-navigabilite/point-21.A.307.txt` | Sert la règle conformité de la sous-traitance : obligation de certification (pièce installée dans un produit certifié de type, certificat d'autorisation de mise en service). **Version consolidée au 07/08/2026** (`CELEX:02012R0748-20260807`) | `obligation_certification` | 20/10/2026 |

URL officielles :
- `rgpd` : https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32016R0679
- `code-commerce` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000005634379/
- `code-civil` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006070721/
- `code-monetaire-financier` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006072026/
- `code-propriete-intellectuelle` et `code-assurances`, et les articles 1231 et 1231-1 du Code civil : la page de chaque article, au manifeste (`hierarchy`, `https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI…`)
- `sti-materiel-roulant` : https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:02014R1302-20250427
- `certification-navigabilite` : https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:02012R0748-20260807

## Fiches de référence

Rédigées pour ce projet, dans `fiches/`. Chaque fiche commence par « Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique » et comporte deux sections : « Ce que dit le texte », uniquement des paraphrases fidèles, chacune avec l'article qu'elle paraphrase (`source_id`, `art.`) ou le point d'annexe (`source_id`, `point`) ; « Comment le projet l'applique », les règles et seuils du projet, présentés comme des choix de politique d'achat. Des tests vérifient la structure, et que chaque citation désigne un article admis ci-dessus. Paraphrases vérifiées mot à mot contre les fichiers bruts le 25/09/2026 ; celles de la fiche des délais de paiement, contre les deux versions de L441-10, le 16/10/2026.

Les six fiches du profil sous-traitance lui sont propres : leurs choix de « Comment le projet l'applique » sont ceux de la politique du donneur d'ordre, comme ceux des fiches du fournisseur sont ceux de la politique d'achat. Elles ne chiffrent aucun seuil (« sous le minimum réglé dans la configuration ») : les seuils du profil sont des valeurs de démonstration, et le texte du constat, écrit par le code, porte la valeur. Leurs paraphrases ont été vérifiées mot à mot contre les fichiers bruts le 21/10/2026, y compris celles des articles du Code civil déjà paraphrasés par les fiches du fournisseur (1170, 1171, 1231-3, 1231-5).

| source_id | Fiche | Clauses rattachées | Articles cités |
| --- | --- | --- | --- |
| `fiche-sous-traitance-rgpd` | `fiches/sous-traitance-rgpd.md` | `donnees_personnelles`, `accord_traitement_donnees` | RGPD 4, 28, 32, 33, 36, 40, 42, 82, 83, 84 |
| `fiche-transferts-hors-ue` | `fiches/transferts-hors-ue.md` | `transfert_hors_ue` | RGPD 40, 42, 44, 45, 46, 83 |
| `fiche-responsabilite-plafonds` | `fiches/responsabilite-plafonds.md` | `responsabilite_acheteur`, `responsabilite_fournisseur` | C. civ. 1170, 1171, 1231-3 ; C. com. L442-1 |
| `fiche-penalites-execution` | `fiches/penalites-execution.md` | `penalites_execution` | C. civ. 1231-5 |
| `fiche-delais-paiement` | `fiches/delais-paiement.md` | `delai_paiement` | C. com. L441-10 |
| `fiche-revision-prix` | `fiches/revision-prix.md` | `revision_prix` | C. mon. fin. L112-2 |
| `fiche-duree-preavis` | `fiches/duree-preavis.md` | `duree_engagement`, `preavis_resiliation` | C. civ. 1210, 1211 ; C. com. L442-1 |
| `fiche-protection-design` | `fiches/protection-design.md` | `protection_design` | CPI L111-1, L112-2, L122-4, L131-3 |
| `fiche-certification-preuves` | `fiches/certification-preuves.md` | `obligation_certification` | Règl. (UE) 1302/2014, annexe, point 4.2.10.2.1 ; Règl. (UE) 748/2012, annexe I, point 21.A.307 |
| `fiche-assurance-sous-traitant` | `fiches/assurance-sous-traitant.md` | `assurance_sous_traitant` | C. assur. L124-3 |
| `fiche-delais-execution` | `fiches/delais-execution.md` | `delai_execution` | C. civ. 1231, 1231-1 |
| `fiche-responsabilite-sous-traitance` | `fiches/responsabilite-sous-traitance.md` | `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` | C. civ. 1170, 1171, 1231-3 |
| `fiche-penalites-retard-execution` | `fiches/penalites-retard-execution.md` | `penalites_retard_execution` | C. civ. 1231-5 |
