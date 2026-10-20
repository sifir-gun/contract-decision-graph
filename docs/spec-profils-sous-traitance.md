# Spec · Profils de contrat, profil sous-traitance

Version du 16 octobre 2026, révisée le même jour après une relecture indépendante (ordre des PR, schéma d'extraction et tables du CRAG par profil, vrai contrat écarté de la démonstration). Conçue en séance de brainstorming avec le propriétaire, section par section, chaque section validée. Source de vérité pour ce chantier ; `docs/spec-phase1.md` le reste pour tout ce que ce document ne modifie pas. Décision d'architecture : [ADR 010](adr-010-profils-de-contrat.md).

**Amendement du 20 octobre 2026**, avant le plan de la PR 2, validé section par section par le propriétaire : le corpus suit les règles des versions successives ([ADR 009](adr-009-versions-du-corpus.md), [spec](spec-versions-corpus.md), PR #62, fusionnée après l'écriture de cette spec) ; deux fiches propres au profil au lieu de l'élargissement des fiches existantes ; format des points d'annexe ; homonymes comparés par source dans le contrôle de l'explication ; port d'explication par profil ; serveur factice de Mistral ; sept contrats avancés en PR 2 ; notes de la PR 1 (`docs/journal.md`, « Notés pour la PR 2 »). Les écarts qu'il introduit sont listés à la fin (« Écarts avec l'existant »).

## Objectif et périmètre

Le système analyse aujourd'hui un seul genre de contrat : le contrat fournisseur d'une PME ou d'une ETI, sur dix types de clauses fixés dans le code. Ce chantier introduit la notion de **profil de contrat**, puis un second profil, la **sous-traitance**, pour servir un cabinet d'assistance à maîtrise d'ouvrage qui coordonne l'aménagement intérieur de transports de luxe (aviation d'affaires, ferroviaire, naval) et gère de nombreux contrats avec des corps de métier, des artisans d'art et des industriels.

Ce que le chantier doit livrer, et rien de plus :

- un profil `fournisseur` explicite, qui reprend à l'identique le comportement actuel ;
- un profil `sous_traitance`, avec ses clauses, ses règles, ses réglages, ses prompts, ses tables du CRAG et ses sources ;
- le choix du profil à chaque analyse, obligatoire, aux trois portes (CLI, interface web, serveur MCP), scellé dans le journal et rejouable ;
- une démonstration présentée sur le poste du propriétaire : sept contrats de sous-traitance fictifs, dans le mode démonstration de l'interface web, et une série réelle sur le modèle Mistral.

**Point de vue.** L'outil défend le **donneur d'ordre**, celui qui engage le sous-traitant : le maître d'ouvrage ou le cabinet lui-même, selon le contrat. Qui signe n'est pas connu : c'est la première question à poser au cabinet (voir « Questions pour le cabinet »).

**Souveraineté.** Inchangée : embeddings locaux, fournisseur LLM européen par défaut (Mistral). La démonstration ne traite **que des contrats fictifs** : un vrai contrat du cabinet n'entre ni dans le dépôt, ni dans le journal du poste du propriétaire (« Aucun contrat réel », CLAUDE.md). L'analyse de leurs vrais contrats relève de leur propre instance, en production : chantier à part, hors de cette spec.

**Données.** Contrats fictifs seulement, dans le dépôt public : aucune partie réelle, aucun nom de client, de chantier, d'architecte ou de projet. Les réglages propres au cabinet (seuils, montants, sévérités) vivront dans sa propre configuration, jamais dans le dépôt.

## Non-objectifs

- L'analyse d'un vrai contrat du cabinet, pendant la démonstration comme sur le poste du propriétaire.
- Les contrats d'architectes et les polices d'assurance comme familles de contrats : ce seront deux profils de plus, plus tard. L'assurance n'est ici vérifiée que comme une clause du contrat de sous-traitance.
- Les contrats en anglais : ils restent rejetés par `validate_input`, comme aujourd'hui.
- La comparaison de plusieurs offres, la cohérence entre plusieurs contrats (contrat principal et sous-contrats), le suivi d'exécution.
- La loi n° 75-1334 du 31 décembre 1975 relative à la sous-traitance (acceptation du sous-traitant, garanties de paiement) : elle ne s'applique qu'à une vraie sous-traitance, ce qu'on ignore.
- Le contenu technique des normes : l'outil vérifie ce que le contrat **exige** (une norme nommée, des preuves à remettre), jamais qu'un matériau est conforme, ni que la norme citée est la bonne pour le secteur. Il ne prétend jamais qu'une pièce est certifiée.
- Les normes payantes et les textes non libres : ni EN 45545-2 (CEN, vendue par les organismes de normalisation), ni les textes de l'Organisation maritime internationale n'entrent dans le corpus.
- Un modèle LLM local, un hébergement, une installation chez le cabinet ; une nouvelle animation du terminal (`scripts/demo_terminal.sh`).
- Des poids, des seuils de décision ou une politique d'arbitrage propres à chaque profil : communs aux deux profils au départ.
- Une empreinte de configuration par profil : l'empreinte couvre tout le fichier, comme aujourd'hui (conséquence acceptée, ADR 010).

## Le profil

### Définition

`Profile = Literal["fournisseur", "sous_traitance"]` (en PR 1, `Literal["fournisseur"]` seul). Un profil est un ensemble nommé, en trois parties.

**Constantes du domaine**, dans un nouveau module `src/cdg/domain/profiles.py`, qui n'importe ni `domain.config` ni `domain.rules` (pas de cycle config → profils → règles → config) :

- ses types de clauses (ce que sont aujourd'hui `models.REQUIRED_KINDS`) et leur partition entre les **quatre domaines existants** (aujourd'hui `models.DOMAIN_KINDS`) ;
- l'unité de chaque type chiffré (aujourd'hui `verification.VALUE_UNITS`) et les catégories admises de chaque type catégoriel ;
- les noms des parties dans les constats (« acheteur » et « fournisseur », « donneur d'ordre » et « sous-traitant »).

**Règles**, dans `src/cdg/domain/rules/` : la fonction de chaque domaine pour chaque profil, choisie par une table `profil → domaine → fonction` dans `rules/__init__.py`. Les règles communes (plafond de responsabilité, pénalités plafonnées, délai de paiement, révision de prix) sont partagées et prennent les noms des parties du profil.

**Application**, dans `src/cdg/application/` :

- le libellé de chaque domaine pour le CRAG (aujourd'hui `crag.DOMAIN_LABELS`), le sujet, l'unité et le sens d'une valeur nulle de chaque type (`crag._SUBJECTS`), les libellés des catégories (`crag._CATEGORY_LABELS`) : **une table par profil**, car un type commun n'a pas le même sujet d'un profil à l'autre (« délai de paiement par l'acheteur », « par le donneur d'ordre ») ;
- le prompt d'extraction et le prompt d'explication de chaque profil (`prompts/`). Le profil `fournisseur` garde `extraction_system.md` et `explain_system.md` ; le profil `sous_traitance` a les siens ;
- le **schéma de sortie de l'extraction de chaque profil** : `ExtractedClause.kind` est le `Literal` des types du profil, `category` celui de ses catégories, passés à `LLMProvider.structured`. Un type ou une catégorie hors du profil échoue à la validation du schéma, et suit le chemin actuel d'un échec de sortie du LLM.

Le profil `fournisseur` reprend à l'identique les constantes, les règles, les tables, les prompts et le schéma actuels, déplacés sans changement de comportement : mêmes clauses, mêmes constats, mêmes requêtes du CRAG, donc `data/evaluation/recherche.yaml` inchangé (`EvaluationSetOutdated` sinon).

**Invariant.** Un type de clause présent dans deux profils (`revision_prix`, `delai_paiement`) appartient au même domaine dans les deux : le corpus déduit le domaine d'indexation d'une clause par une table unique (`corpus.by_domain`), qui accepte l'union des types de tous les profils. Un test le vérifie.

### Configuration

Dans `config/decision.yaml` :

- les sections actuelles (`rules`, `extraction.absence_terms`, `extraction.category_terms`) restent à leur place et **sont** le profil fournisseur : une configuration archivée se relit sans changement ;
- une nouvelle section `sous_traitance`, avec `rules`, `absence_terms` et `category_terms` du profil, ajoutée en PR 2. Règle de l'ADR 006 : absente par défaut dans le modèle Pydantic, pour relire l'archive (aucune analyse sous-traitance n'était possible avant elle), mais exigée par `load_config` dans le fichier du projet, à toute profondeur ;
- poids, seuils, marge, budget, politique d'arbitrage, contrôle de l'entrée, réglages du CRAG et de l'explication : communs.

### Choix du profil

Obligatoire, sans valeur par défaut : une analyse sous le mauvais profil ne chercherait pas les bonnes clauses.

- **CLI** : `run --profil fournisseur|sous_traitance`. `relaunch` reprend le profil du contrat d'origine, lu dans son état, et le scelle de nouveau. `mesure-recherche --profil` aussi, obligatoire, chaque profil ayant son jeu d'évaluation.
- **Interface web** : une liste dans le formulaire d'analyse, sans option présélectionnée ; le profil s'affiche dans la liste des contrats, le dossier et l'historique.
- **Serveur MCP** : argument `profil` obligatoire de l'outil `analyser_contrat` ; le profil figure dans la liste blanche des réponses (`adapters/mcp/presentation.py`), qui accepte les types de clauses de tous les profils.
- **Saisie commune** : `application/saisie.py` valide le profil pour le web et le serveur MCP. `tests/test_parite.py` vérifie la même méthode du service aux trois portes.

### Profil dans l'état, les ports et le journal

- `run_contract` place le profil dans le contexte d'analyse, avec l'empreinte de la configuration, avant tout nœud : il est dans le premier checkpoint. Nouvelle clé d'état `profile`, sans réducteur, jamais modifiée par un nœud.
- **Signatures qui portent le profil** : les ports `Extractor` et `Crag` (`application/deps.py`) reçoivent le profil de l'analyse ; `AnalystInput` (`application/state.py`), l'état privé que chaque `Send` remet à un analyste, porte le profil. `extract_clauses`, `verify_extraction` et `analyst` le lisent ; `decision_gate` n'en a pas besoin (poids et seuils communs). Adaptateurs et doublures suivent (`tests/test_ports.py`).
- **Port d'explication (PR 2, amendement du 20/10/2026).** Le port `Explainer` (`__call__(request, feedback)`) ne reçoit pas le profil et ne peut pas choisir le prompt du profil, qui commence aujourd'hui par « Tu expliques les constats d'une analyse de contrat fournisseur ». Il reçoit le profil de l'analyse, comme `Extractor` et `Crag` ; le nœud `explain` le lit par `profiles.of_state(state)`. `LLMExplainer` lit les prompts de tous les profils à sa construction, par une table `PROMPT_FILES` comme l'extraction : un fichier manquant échoue au démarrage. Le schéma `Draft` et les contrôles de `domain/explanation.py` restent communs. Le chemin sans LLM (`TemplateOnly` : `expire`, `resume` sans clé) ne change pas. Adaptateurs et doublures suivent ; les tests du contenu du prompt d'explication sont paramétrés par profil. Écarté : un champ `profile` dans `ExplanationRequest`, qui garderait la signature du port mais ferait passer le profil par une autre voie que pour les deux autres ports.
- **Format v3 du journal.** Le profil entre dans la partie décision de l'enregistrement (`DecisionRecord`), donc dans `decision_hash`. Les enregistrements v2 sont relus par des modèles figés (`AuditRecordV2`, `DecisionRecordV2`), comme les v1 aujourd'hui ; v1 et v2 restent vérifiables, et sont relus comme profil `fournisseur`, le seul qui existait alors.
- **Rejeu.** Un enregistrement v3 est rejoué sous son profil et sa configuration archivée ; v1 et v2 sous le profil `fournisseur`. Sens fidèle et réévaluation inchangés (ADR 005).
- **Analyse interrompue.** Le profil est dans son checkpoint. Une analyse commencée avant la PR 1 n'a pas de clé `profile` : elle est relue comme `fournisseur`, seul profil possible alors. En PR 1 la configuration ne change pas : elle reprend normalement, sans escalade (le constat de code modifié s'affiche au réviseur, comme aujourd'hui). À partir de la PR 2, la section `sous_traitance` change l'empreinte de la configuration : une analyse commencée avant part en ESCALADE, selon la règle existante.
- **Traces.** Le nom du profil devient un attribut autorisé (`adapters/otel/attributes.py`, liste blanche) ; aucun texte. En PR 1, seules l'analyse et la relance le portent, les opérations qui le connaissent à l'ouverture. En PR 2, la revue et la reprise le portent aussi : `cdg.profil` est lu dans le statut final du moteur, qui contient déjà `profil` (`thread_status`) ; le test d'absence de texte de contrat sur les 13 contrats du jeu reste vert. L'expiration reste sans profil : une seule trace pour plusieurs contrats, qui peuvent être de profils différents (limite documentée).

## Clauses et règles du profil `sous_traitance`

Neuf types de clauses. Chaque seuil vit dans `decision.yaml`, section `sous_traitance.rules` ; les valeurs de départ sont des valeurs de démonstration, à valider avec le cabinet.

| Domaine | Type | Valeur ou catégorie | Règle |
| --- | --- | --- | --- |
| juridique | `responsabilite_donneur_ordre` | plafond, % du montant du contrat | présente sans plafond : **blocage** (règle actuelle de l'acheteur) |
| juridique | `responsabilite_sous_traitant` | plafond, % du montant du contrat | sous le minimum réglé : pénalité (règle actuelle du fournisseur) |
| juridique | `protection_design` | catégorie | `usage_limite_au_projet` : rien ; `confidentialite_seule` : pénalité ; `reutilisation_autorisee` : **blocage** ; absente : pénalité |
| financier | `revision_prix` | plafond, % par an | non plafonnée : **blocage** (règle actuelle) |
| financier | `delai_paiement` | jours et point de départ | au-delà du délai réglé : pénalité (règle actuelle) |
| financier | `penalites_retard_execution` | plafond, % du montant du contrat | absentes ou plafonnées sous le minimum : pénalité (règle actuelle des pénalités d'exécution) |
| conformité | `obligation_certification` | catégorie | `norme_et_preuves` : rien ; `norme_sans_preuves` ou `conformite_generale` : pénalité ; absente : **blocage** |
| conformité | `assurance_sous_traitant` | montant garanti, € par sinistre | sous le minimum réglé, ou non chiffré : pénalité ; absente : pénalité |
| opérationnel | `delai_execution` | catégorie | `date_contractuelle` : rien ; `delai_indicatif` : pénalité ; absent : pénalité |

Le nom `penalites_retard_execution` évite « pénalités de retard », qui désigne en droit commercial les pénalités de retard de paiement (C. com., art. L441-10, II), piège déjà présent dans le jeu actuel.

Catégories :

- `protection_design` : `usage_limite_au_projet` (dessins et documents remis utilisés pour le seul projet, et confidentiels), `confidentialite_seule` (confidentialité sans limite d'usage), `reutilisation_autorisee` (le sous-traitant peut les réutiliser).
- `obligation_certification` : `norme_et_preuves` (normes nommées et preuves à remettre : procès-verbaux d'essais, certificats), `norme_sans_preuves`, `conformite_generale` (« conforme aux normes en vigueur », sans norme nommée).
- `delai_execution` : `date_contractuelle` (date ou planning qui engage le sous-traitant), `delai_indicatif`.

Les listes de catégories acceptées, pénalisées et bloquantes sont réglées dans la configuration, comme `transfer_safeguards` aujourd'hui.

**Contrôles de l'extraction, inchangés et appliqués aux nouveaux types** : citation mot pour mot dans le texte masqué, valeur présente dans la citation avec son unité, catégorie évoquée par la citation (termes de `category_terms`), absence redemandée si le texte évoque la clause (termes de `absence_terms`), citation hors consigne refusée.

**Catégorie exigée** (`models.category_required`, amendement du 20/10/2026). La règle garde `delai_paiement` en dur (catégorie exigée seulement si le délai est chiffré) : ce type a le même sens dans les deux profils. Les trois nouveaux types à catégorie (`protection_design`, `obligation_certification`, `delai_execution`) n'ont pas de valeur : leur catégorie est exigée dès qu'ils sont présents, ce que la règle fait déjà. La docstring le dit ; un test couvre chaque type à catégorie du profil `sous_traitance`. Écarté : un attribut de `ProfileSpec` (types dont la catégorie dépend de la valeur), généralité sans second cas.

**Extension de la vérification des valeurs.** `verification.quantities` ne lit aujourd'hui ni l'euro ni les séparateurs de milliers. Pour `assurance_sous_traitant`, unité « € » : chiffres avec espace ou point comme séparateur de milliers (la normalisation NFKC ramène déjà les espaces insécables à une espace), suivis de « € », « euros » ou « EUR » (« 2 000 000 € », « 2.000.000 euros »). Les formes en lettres et les multiples (« deux millions d'euros », « 2 M€ ») ne sont pas lues : limite documentée, qui mène à une nouvelle extraction puis à la revue humaine, dans le sens prudent. Testé avant d'être codé, y compris sur les faux positifs (un montant n'est pas lu comme un pourcentage, ni l'inverse).

## Corpus

Règle actuelle, inchangée : une clause dont le constat bloque ou pénalise exige au moins une référence en vigueur, sinon le domaine est `INSUFFISANT` ; chaque source déclare les clauses qu'elle peut justifier (`manifest.yaml`, en-tête des fiches, `SOURCES.md`).

**Règles des versions** (ADR 009, `docs/spec-versions-corpus.md` ; amendement du 20/10/2026) : chaque source et chaque fiche de la PR 2 les suit. La date d'analyse choisit la version en vigueur ; une version pas encore en vigueur n'est jamais retenue. Une version suivante d'un article se déclare dans `later_versions` du manifeste : fichier nommé par son début de validité, url, date de récupération obligatoire, enchaînement contrôlé par `check_chain`. Une fiche vaut sur l'intersection des périodes des versions qu'elle déclare avoir relues (`versions_verifiees`), sinon de la première version de chaque article cité. Le contrôle des échéances porte sur chaque référence : fin de la dernière version d'un article, fin de la période d'une fiche.

**Sources existantes, rattachements élargis** :

- C. civ., art. 1170, 1171 et 1231-3 : `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` ;
- C. civ., art. 1231-5 : `penalites_retard_execution` ;
- C. com., art. L441-10 et C. mon. fin., art. L112-2 : déjà rattachés à `delai_paiement` et `revision_prix`, types communs.

**Aucun fichier existant de `raw/` ni de `fiches/` ne change**, ni en texte ni en en-tête. Les fiches « Plafonds et clauses de responsabilité » et « Pénalités d'exécution (clause pénale) » décrivent les règles du profil fournisseur, avec ses seuils (« responsabilité de l'acheteur », « inférieur à 100 % », « au moins 5 % ») : rattachées à un constat de sous-traitance, elles seraient fausses dès que les seuils diffèrent ; le juge du CRAG en lit les extraits, et le relecteur leur texte dans le dossier. Deux fiches propres au profil les doublent (« Nouvelles fiches »). Les extraits rattachés aux types du fournisseur restent identiques : la mesure filtrée du profil fournisseur ne change pas. Écartés : élargir le seul en-tête `clauses` des fiches existantes (elles décriraient une autre règle que celle du constat) ; leur ajouter une puce « sous-traitance » (la fiche des pénalités tient en un extrait de 274 mots, pour 300 au plus : une puce de plus la coupe en deux, et change la mesure fournisseur des deux requêtes du jeu figé qui l'attendent).

### Nouvelles sources Légifrance

Existence et version vérifiées sur la page officielle le 16/10/2026, à relire au plan de la PR 2 ; texte copié dans `raw/` tel qu'affiché.

| Source (`source_id`, citation) | Article | Version en vigueur | Versions suivantes | Clause | Ce qu'il apporte |
| --- | --- | --- | --- | --- | --- |
| `code-propriete-intellectuelle`, CPI | L111-1 | depuis le 27/12/2020 | aucune au 16/10/2026 | `protection_design` | droit exclusif de l'auteur du seul fait de la création ; un contrat de louage d'ouvrage n'y déroge pas |
| `code-propriete-intellectuelle`, CPI | L112-2 | depuis le 11/05/1994 | aucune au 16/10/2026 | `protection_design` | œuvres de l'esprit : dessin, architecture, arts appliqués, plans relatifs à l'architecture |
| `code-propriete-intellectuelle`, CPI | L122-4 | depuis le 03/07/1992 | aucune au 16/10/2026 | `protection_design` | reproduction ou adaptation sans consentement de l'auteur : illicite |
| `code-propriete-intellectuelle`, CPI | L131-3 | depuis le 03/07/1992 | aucune au 16/10/2026 | `protection_design` | cession : chaque droit mentionné, domaine d'exploitation délimité (étendue, destination, lieu, durée) |
| `code-civil`, C. civ. | 1231 | depuis le 01/10/2016 | aucune au 16/10/2026 | `delai_execution` | dommages et intérêts après mise en demeure, sauf inexécution définitive |
| `code-civil`, C. civ. | 1231-1 | depuis le 01/10/2016 | aucune au 16/10/2026 | `delai_execution` | dommages et intérêts à raison du retard dans l'exécution |
| `code-assurances`, C. assur. | L124-3 | depuis le 19/12/2007 | aucune au 16/10/2026 | `assurance_sous_traitant` | action directe du tiers lésé contre l'assureur de responsabilité civile du responsable |

Ce que les règles exigent de chacun :

- **Fichier** `raw/<dossier>/<article>.txt` : première ligne « Article … », puis la ligne « Version en vigueur depuis le … », lue par `corpus._VERSION` (début de validité) ; « Création » ou « Modifié par », et les notes « Conformément à … », par les règles existantes. Toute nouvelle ligne d'interface de la page est retirée par une règle testée de `domain/corpus.py`, jamais à la main.
- **Manifeste** : deux sources nouvelles, `code-propriete-intellectuelle` et `code-assurances` (`format: legifrance`, titre, dossier, `file: "{article}.txt"`, `retrieved_at`, rattachements) ; 1231 et 1231-1 ajoutés au Code civil, avec leur date dans `retrieved_at_overrides`, comme 1231-5. Chaque article a sa hiérarchie officielle et son lien (`hierarchy`), exigés par les tests : 16 liens au lieu de 9. La date de lecture (`hierarchy_verified_at`) est portée par la source, non par l'article : les huit hiérarchies du Code civil sont relues au plan et la date de la source mise à jour ; le schéma du manifeste ne change pas. Le motif des intitulés (`LEVEL`, `tests/test_corpus.py`) n'admet pas « Première partie : … », que porte sans doute la hiérarchie du CPI : étendu et testé si la page le confirme. Les noms de source, en lettres et tirets, sont lus par `corpus._ONE` sans extension.
- **Version à venir** constatée à la relecture : déclarée dans `later_versions` (`{article}_{AAAA-MM-JJ}.txt`, url, `retrieved_at`), et relue par chaque fiche qui cite l'article (`versions_verifiees`). Aucune : ni `later_versions`, ni `versions_verifiees`.
- **Date d'analyse** : débuts de validité de 1992 à 2020, antérieurs à toute date d'analyse du jeu et des tests (25/09/2026) : articles retenus. Une analyse antidatée avant leur début les signale « pas encore en vigueur », par la règle existante, sans rien d'ajouté.
- **`SOURCES.md`** : une ligne par fichier (version en vigueur, date de récupération) ; mentions etalab-2.0 de Légifrance ; renvois des nouveaux articles non suivis, listés selon la règle de périmètre (« Renvois non suivis ») ; paragraphe des fiches : les choix de « Comment le projet l'applique » sont ceux de la politique d'achat ou du donneur d'ordre, selon le profil.

### Nouvelles sources EUR-Lex

| Source (`source_id`, citation) | Unité | Version consolidée | Clause | Ce qu'il apporte |
| --- | --- | --- | --- | --- |
| `sti-materiel-roulant`, Règl. (UE) 1302/2014 (STI matériel roulant) | annexe, point 4.2.10.2.1 | au 27/04/2025 | `obligation_certification` | exigences relatives aux matériaux, renvoi à EN 45545-2:2013 |
| `certification-navigabilite`, Règl. (UE) 748/2012 (Part 21) | annexe I, point à choisir au plan | au 07/08/2026 | `obligation_certification` | certification de navigabilité, désignation des spécifications de certification |

- **Règle des versions, inchangée pour EUR-Lex.** `parse_eurlex` ne lit aucune ligne de version : un texte EUR-Lex n'a ni début ni fin de validité, comme le RGPD. Il est toujours en vigueur, n'apparaît jamais dans les échéances, est dédoublonné par la clé `NULLS NOT DISTINCT`, et ne peut pas avoir de `later_versions` (`check_chain` exige un début de validité). La date de la version consolidée est consignée dans `SOURCES.md` et en commentaire du manifeste ; `retrieved_at` est la date de la copie. Limite documentée : une consolidation nouvelle qui modifierait le point ne se voit qu'à la relecture d'EUR-Lex, la CI ne la voit pas (même limite qu'à l'ADR 009). Écarté : lire la date de consolidation comme début de validité ; elle n'est pas celle de l'entrée en vigueur du point (une consolidation reprend tous les amendements, le point inchangé compris), et une analyse antidatée recevrait un constat « pas encore en vigueur » faux.
- **Format des points d'annexe** (point ouvert de la version du 16/10, réglé) :
  - noms de source sans chiffres (`sti-materiel-roulant`, `certification-navigabilite`) : la partie « source » de `corpus._ONE` ne change pas ;
  - unité déclarée au manifeste pour chaque source : `art.` par défaut ; « annexe, point » pour 1302/2014, « annexe I, point » pour Part 21. La référence en est formée (« Règl. (UE) 1302/2014, annexe, point 4.2.10.2.1 »), la même pour toutes les versions ; fichier `point-{numéro}.txt` ;
  - première ligne de la copie : le numéro du point, tel qu'affiché par EUR-Lex (forme lue au plan, point final éventuel compris), comparé à la clé du manifeste comme « Article … » aujourd'hui ; clés du manifeste entre guillemets. `parse_eurlex` prend aujourd'hui la ligne qui suit le titre pour l'intitulé et la retire du texte : si EUR-Lex affiche le numéro et l'intitulé sur une même ligne, la lecture d'un point ne doit perdre aucun paragraphe en silence (testé sur la copie réelle) ;
  - citation d'une fiche : `(source : sti-materiel-roulant, point 4.2.10.2.1)`. `corpus._ONE` est étendue à `point` suivi d'un numéro à points (au moins un point : `4.2.10.2.1`, `21.A.101`) ; les suites des citations du RGPD (« art. 83, par. 5, point c », « art. 4, point 8 ») ne sont pas lues comme une citation de point ;
  - entrée d'un bloc de citation : chaque partie du bloc séparée par « ; » ; elle doit commencer par une citation que `corpus._ONE` lit (les 79 entrées des fiches actuelles le font, vérifié le 20/10/2026), sinon erreur qui nomme la fiche. Une entrée illisible est aujourd'hui ignorée en silence quand une autre entrée du bloc est lue, et partout dans « Comment le projet l'applique » ;
  - `tests/test_corpus.py` (lignes de `SOURCES.md` comparées au manifeste, qui retirent le préfixe `art. `) : adapté à l'unité « point » ;
  - pas de hiérarchie (`hierarchy`) : les tests l'interdisent hors de Légifrance.
- **Contrôle de l'explication** (`domain/explanation.articles`). Il ne repère que « art. » et « article » : un point d'annexe cité par le LLM hors des références retenues de la clause passerait. Il est étendu à « point » suivi d'un numéro à points (au moins un point), pour que ce point soit refusé comme un article ; un numéro de point garde ses points à la normalisation (« 4.2.10.2.1 », jamais « 421021 »). « l'article 4, point 8 du RGPD » reste lu comme l'article 4 seul, comme aujourd'hui : une explication du profil fournisseur acceptée aujourd'hui l'est encore (testé). Testé avant d'être codé : une couche de défense s'ajoute, aucune ne se retire. Le test qui lit le numéro de chaque référence du corpus (`test_aucun_numero_d_article_commun_a_deux_sources`) exige cette extension avant l'entrée du premier point d'annexe.
- **Limite documentée, avec un test qui la montre** : CPI, art. L112-2 (œuvres de l'esprit) et C. mon. fin., art. L112-2 (indexation) portent le même numéro, et le contrôle de l'explication ne compare que les numéros. Une explication d'un constat sur `revision_prix` qui citerait « L112-2 du CPI » passerait. Les deux articles ne servent ni la même clause ni le même domaine. Écarté : reconnaître le nom du code dans le texte du LLM (« code de la propriété intellectuelle », « CPI »…), détection fragile pour un cas étroit.
- **Part 21** : le point se choisit au plan, sur la page EUR-Lex. Il n'entre dans `raw/` que s'il sert la règle (désignation des spécifications de certification, ou preuves de conformité) ; sinon, la fiche sur la certification cite le règlement par son nom, comme CS-25.

### Sources écartées

- C. civ., art. 1344 : il porte sur la mise en demeure de payer ;
- CS-25 de l'EASA n'est **pas ingérée** : reproduction autorisée sous réserve de citer la source (page « Copyright, Disclaimer and Confidentiality » de l'EASA, lue le 16/10/2026), mais texte en anglais seulement, alors que le corpus est en français ; la fiche la cite par son nom ;
- le naval n'a aucune source sectorielle libre : la fiche sur la certification le dit.

**Prérequis levé : C. com., art. L441-10.** La version en vigueur à partir du 01/01/2027 est au corpus depuis la PR #62 (ADR 009), fusionnée le 20/10/2026 ; `scripts/echeances_corpus.py --jours 60` rend `"echeances": []` au 20/10/2026 comme au 15/12/2026.

**Licences.** EUR-Lex et Légifrance : conditions vérifiées le 26/09/2026 (`SOURCES.md`), à relire au plan de la PR 2 (Légifrance : etalab-2.0 ; EUR-Lex : CC BY 4.0 pour les textes consolidés, modifications indiquées ; EASA, pour CS-25, citée par son nom seulement). Mentions et date de mise à jour dans `SOURCES.md`, comme pour les sources actuelles. Une source dont la réutilisation n'est pas claire n'entre pas dans `raw/`.

### Nouvelles fiches

`data/corpus/fiches/`, structure actuelle : « Ce que dit le texte », paraphrases sourcées, chacune citant un article admis ; « Comment le projet l'applique », choix de politique du donneur d'ordre, écrites quand les règles existent. Six fiches, toutes propres au profil (13 fiches au total) :

| Fiche | Clauses | Articles cités | Valide depuis |
| --- | --- | --- | --- |
| Protection du design | `protection_design` | CPI L111-1, L112-2, L122-4, L131-3 | 27/12/2020 |
| Certification et preuves | `obligation_certification` | Règl. (UE) 1302/2014, annexe, point 4.2.10.2.1 ; le point de Part 21 s'il entre | sans borne (EUR-Lex seul) |
| Assurance du sous-traitant | `assurance_sous_traitant` | C. assur. L124-3 | 19/12/2007 |
| Délais d'exécution | `delai_execution` | C. civ. 1231, 1231-1 | 01/10/2016 |
| Responsabilité dans la sous-traitance | `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` | C. civ. 1170, 1171, 1231-3 | 01/10/2018 |
| Pénalités de retard d'exécution | `penalites_retard_execution` | C. civ. 1231-5 | 01/10/2016 |

Ce que les règles exigent de chacune :

- en-tête `id`, `titre`, `clauses` ; `versions_verifiees` seulement si un article cité a plusieurs versions (aucun au 16/10/2026, à confirmer au plan). Fenêtre de validité testée pour chaque fiche, sans fin ;
- chaque article cité partage une clause avec la fiche (test existant) ; paraphrases vérifiées mot à mot contre les fichiers bruts, date consignée dans `SOURCES.md` ; les paraphrases des articles du Code civil déjà cités par les fiches du fournisseur existent donc deux fois, chacune vérifiée ;
- **aucun seuil chiffré** : « sous le minimum réglé dans la configuration », jamais « 2 000 000 € ». Les seuils du profil sont des valeurs de démonstration, ceux du cabinet vivront dans sa propre configuration, où un chiffre écrit dans une fiche deviendrait faux ; le texte du constat, écrit par le code, porte la valeur ;
- CS-25, EN 45545-2 (par son nom seul) et l'absence de source libre pour le naval : dans « Comment le projet l'applique », où les citations sont facultatives (toute paraphrase de « Ce que dit le texte » cite un article admis, test existant) ;
- noms des parties : « donneur d'ordre » et « sous-traitant » ; la fiche sur les pénalités rappelle qu'elles sont distinctes des pénalités de retard de paiement, sans bloc de citation, comme la fiche fournisseur (`penalites-execution.md`) : cité en bloc, L441-10, rattaché au seul `delai_paiement` et à deux versions, ferait échouer le test du rattachement et fermerait la fenêtre de la fiche au 01/01/2027.

### Échéances, recherche datée, ingestion

- **Échéances** : 43 références au lieu de 29 (30 articles, 31 avec un point de Part 21 ; 13 fiches), comptées par `tests/test_echeances_corpus.py` (`22 + 7` aujourd'hui). Aucune nouvelle source n'a de fin annoncée, celles d'EUR-Lex n'en ont jamais. `echeances_corpus.py --jours 60` : code 0 et `"echeances": []` à la date du push, revérifié au plan sur les pages relues.
- **Recherche datée et dossier** : aucun mécanisme nouveau. Un test vérifie que chaque nouvelle référence a son texte dans `reference_texts(date(2026, 9, 25))`, donc dans le dossier. Une analyse antidatée relève des règles existantes, couvertes par le corpus de test.
- **Ingestion** : après la fusion, `ingest` sur le poste, sans migration. `rag_store.sync` compte `kinds` parmi les métadonnées comparées : il remplace les extraits de 1170, 1171, 1231-3 et 1231-5, dont le rattachement s'élargit. Base non réindexée : `mesure-recherche` refuse (`IndexMismatch`, nouvelles références manquantes) ; en mode réel, une analyse sous-traitance ne trouve aucun extrait pour les nouvelles clauses, domaine `INSUFFISANT`, puis `ESCALADE`, dans le sens prudent. Dans le cluster, le crochet d'ingestion du chart s'en charge à la mise à jour. « Relancer ingest » : `docs/exploitation.md` et journal.

### Mesure de la recherche

- Un jeu d'évaluation propre au profil, `data/evaluation/recherche-sous-traitance.yaml`, établi sans le rattachement déclaré comme le premier, à partir des constats des contrats de sous-traitance du jeu ; `mesure-recherche --profil sous_traitance`. Le jeu figé de l'ADR 006 ne change pas. [PR 3] (`evaluation.demo_queries` ne prend déjà que les contrats du profil, depuis la PR 1.)
- **En PR 2, `mesure-recherche --profil sous_traitance` est refusée explicitement** : code non nul, message qui nomme le profil. Le jeu du profil n'existe qu'en PR 3 : `EVALUATION_SETS[profil]` lèverait `KeyError`, jamais une erreur qui dise la cause. Le refus vit dans `application/evaluation.py` (exception explicite pour un profil sans jeu), la CLI en fait un code non nul. `test_un_jeu_d_evaluation_par_profil` exige, jusqu'à la PR 3, un jeu pour chaque profil sauf `sous_traitance`, nommé ; la PR 3 le ramène à l'égalité.
- **Profil fournisseur, mesuré avant et après** : sur `main`, puis sur la branche, le même jour (ADR 009 : on ne compare qu'à date égale) ; PostgreSQL, `ingest` et le modèle local ; chiffres consignés au journal. Mesure filtrée : identique, tout écart est une anomalie à expliquer. Mesure sans filtre : elle change, les nouvelles sources entrant dans la recherche sans filtre de domaine ; écart attendu, mesuré. Qui la lance : fixé au plan (Docker était arrêté sur le poste le 20/10/2026).

## Jeu de démonstration

Sept contrats fictifs dans `data/contracts/`, avec leurs attendus dans `attendus.yaml`, qui porte un champ `profile` obligatoire par contrat depuis la PR 1 (les treize existants : `fournisseur`). Parties fictives, coordonnées dans les plages réservées (`tests/test_donnees_fictives.py`), aucun nom réel.

| Contrat | Univers | Décision attendue | Ce qu'il montre |
| --- | --- | --- | --- |
| `st-01-go-cabine` | mobilier d'une cabine d'avion d'affaires | `GO` | un contrat propre |
| `st-02-reserves-marqueterie` | marqueterie d'une voiture de train historique | `GO_RESERVES` | délai indicatif, assurance non chiffrée, pénalités de retard d'exécution sous le minimum |
| `st-03-no-go-ferronnerie` | ferronnerie d'art pour un yacht | `NO_GO` | réutilisation des dessins autorisée |
| `st-04-no-go-sellerie` | sellerie d'une voiture de train | `NO_GO` | aucune obligation de certification |
| `st-05-reserves-revetements` | revêtements d'une cabine d'avion | `GO_RESERVES` | norme sans preuves, plafond du sous-traitant bas, délai de paiement long, assurance sous le minimum |
| `st-06-no-go-ebenisterie` | ébénisterie d'un navire | `NO_GO` | responsabilité du donneur d'ordre illimitée, révision de prix non plafonnée |
| `st-07-realiste-amenagement` | aménagement d'un navire, rédaction réaliste | `ESCALADE`, puis décision humaine | sous doublures, conflit entre domaines (écart au-delà de `conflict_gap`, comme `demo-13`) : conformité pénalisée (« conforme aux normes en vigueur », assurance absente), les autres domaines sans constat ; en mode réel, formulations indirectes que le code refuse de deviner |

Avec `hard_block_review: false` (réglage actuel), un blocage dur donne `NO_GO` sans revue humaine. Les sept contrats arrivent avec les règles, en PR 2 (amendement du 20/10/2026) : CLAUDE.md exige que chaque règle se déclenche dans au moins un contrat du jeu, et la règle reste vraie à chaque fusion. La répartition exacte des clauses est fixée au plan de la PR 2. Le catalogue des règles de `tests/test_demo.py` (`RULE_CATALOG`, aujourd'hui `RULES["fournisseur"]` seul) devient un catalogue par profil. `tests/test_demo.py` exige que **chaque branche des règles des cinq nouveaux types** (`protection_design`, `penalites_retard_execution`, `obligation_certification`, `assurance_sous_traitant`, `delai_execution`) se déclenche dans au moins un contrat de sous-traitance, et chaque règle reprise (`responsabilite_donneur_ordre`, `responsabilite_sous_traitant`, `revision_prix`, `delai_paiement`) au moins une fois sous ce profil ; les autres branches des règles reprises restent couvertes par le jeu fournisseur.

**Mode démonstration** (`web --demo`) : les contrats des deux profils, sans réseau, sans clé, sans base. L'extraction simulée reconnaît le contrat à son texte masqué **et** à son profil ; un contrat du jeu analysé sous un autre profil lève une erreur explicite (« contrat du jeu du profil sous_traitance »), jamais un résultat faussé.

**Profil choisi, jamais déduit** (amendement du 20/10/2026). `web --demo` demande un profil alors que chaque contrat du jeu déclare le sien : on garde le choix (ADR 010). Dès la PR 2, le formulaire de démonstration propose deux profils, et un contrat du jeu peut y être choisi sous un profil qui n'est pas le sien. Ce cas est refusé à la saisie commune (`saisie.from_demo_set`, en démonstration seulement) : erreur de formulaire (400) pour l'interface, erreur de l'outil sans message d'exception pour le serveur MCP, message qui nomme le profil du contrat. Sans ce refus, l'erreur ne viendrait qu'à l'extraction simulée, et l'analyse finirait par un rapport d'échec (la garde du nœud convertit l'exception). Le contrôle de l'extraction simulée reste : aucune couche n'est retirée. En mode réel, rien ne change : analyser un contrat du jeu sous un autre profil reste un choix légitime.

**Série réelle** : `tests/test_llm_jeu.py` se paramètre par profil (`-k`) ; la série du chantier couvre les sept contrats de sous-traitance, cinq essais chacun, soit 35 analyses payantes, lancées par le propriétaire, résultat consigné à `docs/journal.md`.

## Serveur factice de Mistral (cluster)

Amendement du 20/10/2026, PR 2. Le serveur factice (`tests/mistral_factice.py`, image de test jamais publiée) choisit sa réponse d'après `response_format.json_schema.name`, que le SDK mistralai forme du `__name__` du modèle Pydantic ; il ne reconnaît aujourd'hui que `ExtractionOutput`, et refuserait un schéma propre à la sous-traitance comme « schéma inconnu ».

- Il lit `extraction.SCHEMAS` et en tire la table « nom du schéma → profil » : un schéma de profil ajouté y est reconnu sans liste à tenir. L'image de test part de l'image de l'application (`PYTHONPATH=/app/src`) : le module y est.
- Il range les contrats du jeu par profil et par texte masqué, comme l'extraction simulée de la démonstration (`adapters/demo/extraction.py`).
- Refus explicites (422), chacun testé : texte hors du jeu (existant, « hors du jeu ») ; contrat du jeu reçu sous le schéma d'un autre profil (nouveau, « contrat du jeu du profil … »).
- Les sept contrats de sous-traitance arrivent en PR 2 : sous le schéma de leur profil, le serveur factice rend leurs clauses attendues, comme pour le jeu fournisseur (test existant, étendu aux deux profils). Testés aussi : un texte hors du jeu, refusé comme hors du jeu ; un contrat fournisseur du jeu sous le schéma de la sous-traitance, refusé comme « contrat du jeu du profil fournisseur », et l'inverse.
- Le nom du schéma de la sous-traitance est choisi au plan ; un test exige des noms distincts entre les schémas de `SCHEMAS`, et distincts de `GradeOutput`, `RewriteOutput` et `Draft`, sans quoi le serveur factice ne saurait pas à quoi répondre.
- La réponse `Draft` de l'explication ne change pas (schéma commun). Les scénarios du cluster restent sous `--profil fournisseur`.

## Erreurs

Aucun repli silencieux ; chaque cas est testé.

- Profil absent ou inconnu : refus à l'entrée, avant toute analyse (CLI : code de sortie non nul et message ; web : erreur de formulaire ; MCP : erreur de l'outil, sans message d'exception).
- Section `sous_traitance` absente du fichier du projet, à partir de la PR 2 : `load_config` arrête le programme.
- Type commun à deux profils dans deux domaines différents : test en échec.
- Type ou catégorie rendus par le LLM hors du profil de l'analyse : échec de validation du schéma de sortie, traité comme tout échec de sortie du LLM aujourd'hui.
- Contrat du jeu sous le mauvais profil, en démonstration : refus à la saisie commune (dès la PR 2), puis, en défense, erreur explicite de l'extraction simulée.
- Montant illisible (« 2 M€ ») : valeur absente de la citation, nouvelle extraction puis revue humaine.
- `mesure-recherche --profil sous_traitance` en PR 2, sans jeu d'évaluation du profil : refus explicite, code non nul.
- Entrée d'un bloc de citation d'une fiche que le code ne lit pas : erreur qui nomme la fiche.
- Point d'annexe cité par l'explication hors des références retenues de la clause : explication refusée, comme pour un article.
- Serveur factice de Mistral : schéma inconnu, texte hors du jeu, contrat du jeu sous le schéma d'un autre profil : refus explicite (422).

## Critères d'acceptation

Chacun est une vérification observable, à la fin du chantier ; la PR où il devient vérifiable est indiquée entre crochets.

1. Chaque PR : `./scripts/check.sh` se termine par « check.sh : SUCCÈS » ; couverture au moins égale au seuil `fail_under` (98 %).
2. [PR 1] `run` sans `--profil`, ou avec un profil inconnu, sort avec un code non nul sans rien analyser ; même refus au formulaire web et à l'outil MCP `analyser_contrat` (`tests/test_parite.py`).
3. [PR 1] Sous `--profil fournisseur`, les treize contrats du jeu actuel rendent les mêmes clauses, constats et décisions qu'avant le chantier (`tests/test_demo.py`, doublures), et `data/evaluation/recherche.yaml` est inchangé.
4. [PR 1] Le vrai journal du poste se vérifie et se rejoue à l'identique : `uv run pytest -m journal_reel --journal-reel` passe (enregistrements v1 et v2 relus comme profil `fournisseur`).
5. [PR 1 pour `fournisseur`, PR 2 pour `sous_traitance`] Un enregistrement v3 scellé sous chaque profil se vérifie (`verify`) et se rejoue fidèlement (`replay`, code 0).
6. [PR 2] Pour chaque profil, l'extraction vérifiée exige chacun de ses types une fois et une seule, et le schéma de sortie refuse tout type d'un autre profil.
7. [PR 2] Les sept contrats de sous-traitance rendent leur décision attendue, sous doublures et dans `web --demo` ; la couverture des règles décrite au « Jeu de démonstration » est atteinte.
8. [PR 2] Chaque constat bloquant ou pénalisant des sept contrats a au moins une référence en vigueur parmi les sources rattachées à sa clause (tests du corpus et de la justification).
9. [PR 4] Série réelle, sept contrats × cinq essais : aucune décision automatique plus favorable que l'attendu ; résultat consigné au journal.
10. [PR 2] Aucun fichier de `data/corpus/raw/` sans source, licence et date dans `SOURCES.md` ; aucun texte d'EN 45545 ni de l'OMI dans le dépôt.
11. [PR 2] `tests/test_donnees_fictives.py` passe sur les sept nouveaux contrats.
12. [PR 2] `uv run python scripts/echeances_corpus.py --jours 60` à la date du push : code 0, `"echeances": []` ; `tests/test_echeances_corpus.py` compte 30 + 13 références (31 + 13 avec un point de Part 21).
13. [PR 2] Dans `data/corpus/`, `git diff main` ne touche que `manifest.yaml`, `SOURCES.md` et des fichiers nouveaux ; mesure filtrée du profil fournisseur identique avant et après, le même jour ; mesure sans filtre consignée au journal, avant et après.
14. [PR 2] Chaque nouvelle référence a son texte dans `reference_texts(date(2026, 9, 25))` ; chaque nouvelle fiche a la fenêtre de validité du tableau « Nouvelles fiches ».
15. [PR 2] `mesure-recherche --profil sous_traitance` sort avec un code non nul et un message qui nomme le profil.
16. [PR 2] Serveur factice de Mistral : le schéma de chaque profil de `extraction.SCHEMAS` est reconnu ; un contrat du jeu sous le schéma d'un autre profil est refusé (422) ; les noms des schémas sont distincts.
17. [PR 2] Une explication qui cite un point d'annexe hors des références retenues de la clause est refusée ; la limite de l'homonymie L112-2 est montrée par un test.
18. [PR 2] En démonstration, un contrat du jeu choisi sous un autre profil que le sien est refusé à la saisie : 400 au formulaire, erreur de l'outil MCP sans message d'exception.
19. [PR 2] Les traces de la revue et de la reprise portent `cdg.profil` ; aucun texte de contrat dans les traces des 13 contrats du jeu.
20. [PR 2] Sous doublures, `run --profil sous_traitance` scelle un v3 du profil `sous_traitance` ; `relaunch` d'un tel contrat garde son profil ; `tests/test_parite.py` reçoit `sous_traitance` à la même méthode aux trois portes.

## Découpage

Une branche par PR, chacune verte avec `check.sh` avant d'être poussée par le propriétaire. Prérequis à part : la mise à jour de L441-10, faite par la PR #62 (voir « Corpus »).

1. **Profil explicite.** `domain/profiles.py` avec le seul profil `fournisseur` ; constantes, règles, tables du CRAG, prompts et schéma d'extraction déplacés sans changement de comportement ; `--profil` obligatoire aux trois portes et à `mesure-recherche` ; signatures des ports et de `AnalystInput` ; état, format v3 du journal, rejeu, traces ; scripts, scénarios du cluster et documentation passés à `--profil fournisseur` ; ADR 010 accepté ; CLAUDE.md mis à jour (format v3, profils). Après la PR 1 : `--profil sous_traitance` est refusé comme profil inconnu.
2. **Profil sous-traitance, son corpus et son jeu.** Dans la même PR, car les tests du corpus exigent au moins un article et une fiche pour chaque type de clause (`tests/test_corpus.py`), les fiches décrivent des règles qui doivent exister, et chaque règle se déclenche dans au moins un contrat du jeu (CLAUDE.md ; amendement du 20/10/2026, les contrats venaient en PR 3). Jeu : sept contrats et leurs attendus ; catalogue des règles par profil (`tests/test_demo.py`) ; mode démonstration des deux profils. Code : `sous_traitance` ajouté à `Profile` ; ses types, sa partition, ses unités et catégories ; ses règles ; sa section de configuration ; ses prompts d'extraction et d'explication ; ses tables du CRAG ; son schéma d'extraction ; ses termes d'absence et de catégorie ; extension de la vérification des montants en euros ; port `Explainer` qui porte le profil ; serveur factice de Mistral ; refus de `mesure-recherche --profil sous_traitance` ; refus à la saisie d'un contrat du jeu sous un autre profil, en démonstration ; profil dans les traces de la revue et de la reprise ; tests du passage du profil renforcés avec `sous_traitance`. Corpus : pages et versions relues, licences relues ; textes copiés dans `raw/` tels qu'affichés ; manifeste, unité des points d'annexe et format des citations étendus ; contrôle de l'explication étendu aux points ; `SOURCES.md` ; six fiches ; rattachements élargis ; mesure fournisseur avant et après ; `ingest` à relancer. Documentation : ADR 010 amendé (port d'explication), exploitation, journal, CLAUDE.md. Après la PR 2 : une analyse sous ce profil fonctionne de bout en bout, en mode réel et dans `web --demo`, sur les sept contrats du jeu.
3. **Mesure de la recherche du profil.** Jeu d'évaluation `data/evaluation/recherche-sous-traitance.yaml` ; `mesure-recherche --profil sous_traitance` admise (le filtre par profil de `demo_queries` est fait depuis la PR 1).
4. **Série réelle.** Filtre par profil de `tests/test_llm_jeu.py` ; série lancée par le propriétaire ; résultats et coût consignés au journal.

## Dépendances vérifiées

- **Aucune nouvelle bibliothèque** : `pyproject.toml` et `uv.lock` inchangés.
- **Sources** : articles et versions lus sur leur page officielle le 16/10/2026 (tableau du corpus). Pages : Légifrance (Code de la propriété intellectuelle, `LEGITEXT000006069414` ; Code civil, `LEGITEXT000006070721` ; Code des assurances, `LEGITEXT000006073984`), EUR-Lex (`CELEX:32014R1302`, `CELEX:32012R0748`), EASA (« Copyright, Disclaimer and Confidentiality »). Les requêtes scriptées vers Légifrance déclenchent une vérification anti-robots, non contournée : lecture page par page dans un navigateur.
- **Code confronté le 16/10/2026** : noms cités vérifiés dans le dépôt par une relecture indépendante (`domain/models.py`, `domain/verification.py`, `domain/corpus.py`, `domain/audit.py`, `application/crag.py`, `application/extraction.py`, `application/deps.py`, `application/state.py`, `application/evaluation.py`, `adapters/mcp/`, `adapters/otel/attributes.py`).
- **Amendement, code confronté le 20/10/2026** sur `main` (7b4655b, après les PR #62 et #63) : `domain/corpus.py` (`_VERSION`, `_TITLE`, `_ONE`, `parse_eurlex`, `check_chain`, `fiche_window`), `application/ingestion.py` (`articles`, `source_validities`, `reference_texts`), `adapters/postgres/rag_store.py` (`_METADATA`, `sync`), `application/evaluation.py` (`EVALUATION_SETS`, `_check_set`), `domain/explanation.py` (`articles`), `application/explanation.py` (`LLMExplainer`), `application/deps.py` (`Explainer`), `application/saisie.py` (`from_demo_set`), `adapters/demo/extraction.py`, `adapters/langgraph/orchestrator.py` (garde des nœuds, `thread_status`), `ports/telemetry.py`, `tests/mistral_factice.py`, `tests/test_corpus.py`. Découpage des fiches mesuré avec `chunk_max_words: 300`.
- **SDK mistralai 2.10.1** (`uv.lock`), code installé lu le 20/10/2026 : `extra/utils/response_format.py`, `response_format_from_pydantic_model` forme `json_schema.name` du `__name__` du modèle.

## Écarts avec l'existant

Validés par le propriétaire pendant la conception, consignés dans l'ADR 010 :

- **Cible élargie.** `spec-phase1.md` vise les directions achats et juridiques de PME et d'ETI ; s'y ajoute le donneur d'ordre d'un contrat de sous-traitance.
- **Schéma d'état.** Nouvelle clé `profile` ; `REQUIRED_KINDS`, `DOMAIN_KINDS`, `VALUE_UNITS`, `DOMAIN_LABELS`, `_SUBJECTS`, `_CATEGORY_LABELS` et le schéma d'extraction deviennent des attributs du profil ; les ports `Extractor` et `Crag` et `AnalystInput` portent le profil.
- **Format du journal v3**, avec relecture figée des v2.
- **Interface de la CLI.** `--profil` obligatoire : tout appel existant à `run` et à `mesure-recherche` change.
- **Empreinte commune.** Un réglage du profil `sous_traitance` modifié change l'empreinte de toute la configuration : les analyses fournisseur interrompues escaladent, et les revues fournisseur en attente affichent « configuration modifiée ». Conséquence acceptée. Précision de l'amendement du 20/10/2026 : la reprise d'une telle revue est refusée (`resume`, `ThreadError` : relancer l'analyse), sauf pour un contrat escaladé pour changement de configuration ; la PR 2, qui ajoute la section, a cet effet sur le poste et dans le cluster. `docs/exploitation.md` : `config-check` et revues en attente à trancher avant la mise à jour.
- **Périmètre figé depuis la v1.1** (02/10/2026) : rouvert le 16/10/2026 à la demande explicite du propriétaire.

Introduits par l'amendement du 20/10/2026, validés section par section par le propriétaire ; l'ADR 010 est amendé en PR 2 pour le port d'explication :

- **Port `Explainer`** : il porte le profil, comme `Extractor` et `Crag` (la version du 16/10 ne nommait que ces deux ports).
- **Fiches** : six fiches propres au profil, au lieu de quatre et de l'élargissement des deux fiches existantes, qui restent intactes.
- **Format du corpus** : unité déclarée par source (articles, points d'annexe) ; citations des fiches étendues aux points ; entrée de citation illisible devenue une erreur ; contrôle de l'explication étendu aux points.
- **Démonstration** : contrat du jeu sous un autre profil refusé dès la saisie.
- **Traces** : profil sur la revue et la reprise.
- **`mesure-recherche --profil sous_traitance`** refusée jusqu'au jeu d'évaluation de la PR 3.
- **Découpage** : les sept contrats passent de la PR 3 à la PR 2, avec les règles qu'ils déclenchent ; la PR 3 se réduit au jeu d'évaluation de la recherche du profil.

## Questions pour le cabinet

À poser lors de la présentation ; chaque réponse devient un réglage ou un chantier, jamais une supposition :

1. Qui signe ces contrats, le maître d'ouvrage ou le cabinet ? Est-ce de la sous-traitance au sens de la loi de 1975 ?
2. Sous quel droit et dans quelle langue sont-ils rédigés ?
3. Combien de contrats par projet, et comment sont-ils gérés aujourd'hui ?
4. Qu'est-ce qui coûte le plus : le temps de relecture, les oublis, les litiges ?
5. Quels seuils et quelles sévérités retiendraient-ils : assurance minimale, plafonds, délais, ce qui doit bloquer ?
6. Pour analyser leurs vrais contrats dans leur propre instance : l'envoi d'un contrat masqué à un fournisseur LLM européen leur convient-il, ou faut-il une analyse entièrement locale ?
