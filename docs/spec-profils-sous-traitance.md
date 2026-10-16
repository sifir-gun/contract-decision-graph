# Spec · Profils de contrat, profil sous-traitance

Version du 16 octobre 2026, révisée le même jour après une relecture indépendante (ordre des PR, schéma d'extraction et tables du CRAG par profil, vrai contrat écarté de la démonstration). Conçue en séance de brainstorming avec le propriétaire, section par section, chaque section validée. Source de vérité pour ce chantier ; `docs/spec-phase1.md` le reste pour tout ce que ce document ne modifie pas. Décision d'architecture : [ADR 009](adr-009-profils-de-contrat.md).

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
- Une empreinte de configuration par profil : l'empreinte couvre tout le fichier, comme aujourd'hui (conséquence acceptée, ADR 009).

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
- **Format v3 du journal.** Le profil entre dans la partie décision de l'enregistrement (`DecisionRecord`), donc dans `decision_hash`. Les enregistrements v2 sont relus par des modèles figés (`AuditRecordV2`, `DecisionRecordV2`), comme les v1 aujourd'hui ; v1 et v2 restent vérifiables, et sont relus comme profil `fournisseur`, le seul qui existait alors.
- **Rejeu.** Un enregistrement v3 est rejoué sous son profil et sa configuration archivée ; v1 et v2 sous le profil `fournisseur`. Sens fidèle et réévaluation inchangés (ADR 005).
- **Analyse interrompue.** Le profil est dans son checkpoint. Une analyse commencée avant la PR 1 n'a pas de clé `profile` : elle est relue comme `fournisseur`, seul profil possible alors. En PR 1 la configuration ne change pas : elle reprend normalement, sans escalade (le constat de code modifié s'affiche au réviseur, comme aujourd'hui). À partir de la PR 2, la section `sous_traitance` change l'empreinte de la configuration : une analyse commencée avant part en ESCALADE, selon la règle existante.
- **Traces.** Le nom du profil devient un attribut autorisé (`adapters/otel/attributes.py`, liste blanche) ; aucun texte.

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

**Extension de la vérification des valeurs.** `verification.quantities` ne lit aujourd'hui ni l'euro ni les séparateurs de milliers. Pour `assurance_sous_traitant`, unité « € » : chiffres avec espace ou point comme séparateur de milliers (la normalisation NFKC ramène déjà les espaces insécables à une espace), suivis de « € », « euros » ou « EUR » (« 2 000 000 € », « 2.000.000 euros »). Les formes en lettres et les multiples (« deux millions d'euros », « 2 M€ ») ne sont pas lues : limite documentée, qui mène à une nouvelle extraction puis à la revue humaine, dans le sens prudent. Testé avant d'être codé, y compris sur les faux positifs (un montant n'est pas lu comme un pourcentage, ni l'inverse).

## Corpus

Règle actuelle, inchangée : une clause dont le constat bloque ou pénalise exige au moins une référence en vigueur, sinon le domaine est `INSUFFISANT` ; chaque source déclare les clauses qu'elle peut justifier (`manifest.yaml`, en-tête des fiches, `SOURCES.md`).

**Sources existantes, rattachements élargis** :

- C. civ., art. 1170, 1171 et 1231-3 : `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` ;
- C. civ., art. 1231-5 : `penalites_retard_execution` ;
- C. com., art. L441-10 et C. monét. fin., art. L112-2 : déjà rattachés à `delai_paiement` et `revision_prix`, types communs ;
- fiches existantes : responsabilité, pénalités, avec les nouveaux types de leur sujet.

**Nouvelles sources** (existence et version vérifiées sur la page officielle le 16/10/2026, texte à copier à la main dans `raw/` en PR 2) :

| Source | Article | Version en vigueur | Clause | Ce qu'il apporte |
| --- | --- | --- | --- | --- |
| Code de la propriété intellectuelle | L111-1 | depuis le 27/12/2020 | `protection_design` | droit exclusif de l'auteur du seul fait de la création ; un contrat de louage d'ouvrage n'y déroge pas |
| Code de la propriété intellectuelle | L112-2 | depuis le 11/05/1994 | `protection_design` | œuvres de l'esprit : dessin, architecture, arts appliqués, plans relatifs à l'architecture |
| Code de la propriété intellectuelle | L122-4 | depuis le 03/07/1992 | `protection_design` | reproduction ou adaptation sans consentement de l'auteur : illicite |
| Code de la propriété intellectuelle | L131-3 | depuis le 03/07/1992 | `protection_design` | cession : chaque droit mentionné, domaine d'exploitation délimité (étendue, destination, lieu, durée) |
| Code civil | 1231 | depuis le 01/10/2016 | `delai_execution` | dommages et intérêts après mise en demeure, sauf inexécution définitive |
| Code civil | 1231-1 | depuis le 01/10/2016 | `delai_execution` | dommages et intérêts à raison du retard dans l'exécution |
| Code des assurances | L124-3 | depuis le 19/12/2007 | `assurance_sous_traitant` | action directe du tiers lésé contre l'assureur de responsabilité civile du responsable |
| Règlement (UE) n° 1302/2014 (STI matériel roulant) | annexe, point 4.2.10.2.1 | consolidé au 27/04/2025 | `obligation_certification` | exigences relatives aux matériaux, renvoi à EN 45545-2:2013 |
| Règlement (UE) n° 748/2012 (Part 21) | point à choisir en PR 2 | consolidé au 07/08/2026 | `obligation_certification` | certification de navigabilité, désignation des spécifications de certification |

Points ouverts pour la PR 2 :

- le manifeste nomme aujourd'hui les fichiers EUR-Lex par article (`art-{article}.txt`), et le format des citations des fiches (`corpus._ONE`) n'admet que `art.` et des identifiants de source sans chiffres : un point d'annexe et une source nommée d'après « 1302/2014 » demandent d'étendre les deux, testés comme le reste ;
- Part 21 : si aucun point du règlement ne sert la règle, il n'entre pas dans `raw/`, et la fiche sur la certification le cite par son nom, comme CS-25 ;
- C. civ., art. 1344, écarté : il porte sur la mise en demeure de payer ;
- CS-25 de l'EASA n'est **pas ingérée** : reproduction autorisée sous réserve de citer la source (page « Copyright, Disclaimer and Confidentiality » de l'EASA, lue le 16/10/2026), mais texte en anglais seulement, alors que le corpus est en français ; la fiche la cite par son nom ;
- le naval n'a aucune source sectorielle libre : la fiche sur la certification le dit.

**Prérequis, hors de ce chantier : C. com., art. L441-10.** La version indexée est en vigueur jusqu'au 01/01/2027 ; à partir du 02/11/2026, le job `audit` de la CI, et donc `check.sh`, échoue tant que l'article et sa fiche ne sont pas mis à jour (`docs/journal.md`, « Échéances du corpus surveillées par la CI »). Cette mise à jour, propre au profil `fournisseur` et commune aux deux par `delai_paiement`, se fait dans sa propre PR, avant toute PR de ce chantier poussée après le 01/11/2026.

**Licences.** EUR-Lex et Légifrance : conditions vérifiées le 26/09/2026 (`SOURCES.md`), à relire en PR 2 ; mentions et date de mise à jour dans `SOURCES.md`, comme pour les sources actuelles. Une source dont la réutilisation n'est pas claire n'entre pas dans `raw/`.

**Nouvelles fiches** (`data/corpus/fiches/`, structure actuelle : « Ce que dit le texte », paraphrases sourcées ; « Comment le projet l'applique », choix de politique du donneur d'ordre, écrites quand les règles existent) : protection du design, certification et preuves, assurances du sous-traitant, délais d'exécution.

**Mesure de la recherche.** Un jeu d'évaluation propre au profil, `data/evaluation/recherche-sous-traitance.yaml`, établi sans le rattachement déclaré comme le premier, à partir des constats des contrats de sous-traitance du jeu ; `mesure-recherche --profil sous_traitance`. `evaluation.demo_queries` applique à chaque contrat les règles de son profil. Le jeu figé de l'ADR 006 ne change pas.

## Jeu de démonstration

Sept contrats fictifs dans `data/contracts/`, avec leurs attendus dans `attendus.yaml`, qui reçoit un champ `profile` obligatoire par contrat (les treize existants : `fournisseur`). Parties fictives, coordonnées dans les plages réservées (`tests/test_donnees_fictives.py`), aucun nom réel.

| Contrat | Univers | Décision attendue | Ce qu'il montre |
| --- | --- | --- | --- |
| `st-01-go-cabine` | mobilier d'une cabine d'avion d'affaires | `GO` | un contrat propre |
| `st-02-reserves-marqueterie` | marqueterie d'une voiture de train historique | `GO_RESERVES` | délai indicatif, assurance non chiffrée, pénalités de retard d'exécution sous le minimum |
| `st-03-no-go-ferronnerie` | ferronnerie d'art pour un yacht | `NO_GO` | réutilisation des dessins autorisée |
| `st-04-no-go-sellerie` | sellerie d'une voiture de train | `NO_GO` | aucune obligation de certification |
| `st-05-reserves-revetements` | revêtements d'une cabine d'avion | `GO_RESERVES` | norme sans preuves, plafond du sous-traitant bas, délai de paiement long, assurance sous le minimum |
| `st-06-no-go-ebenisterie` | ébénisterie d'un navire | `NO_GO` | responsabilité du donneur d'ordre illimitée, révision de prix non plafonnée |
| `st-07-realiste-amenagement` | aménagement d'un navire, rédaction réaliste | `ESCALADE`, puis décision humaine | sous doublures, conflit entre domaines (écart au-delà de `conflict_gap`, comme `demo-13`) : conformité pénalisée (« conforme aux normes en vigueur », assurance absente), les autres domaines sans constat ; en mode réel, formulations indirectes que le code refuse de deviner |

Avec `hard_block_review: false` (réglage actuel), un blocage dur donne `NO_GO` sans revue humaine. La répartition exacte des clauses est fixée en PR 3. `tests/test_demo.py` exige que **chaque branche des règles des cinq nouveaux types** (`protection_design`, `penalites_retard_execution`, `obligation_certification`, `assurance_sous_traitant`, `delai_execution`) se déclenche dans au moins un contrat de sous-traitance, et chaque règle reprise (`responsabilite_donneur_ordre`, `responsabilite_sous_traitant`, `revision_prix`, `delai_paiement`) au moins une fois sous ce profil ; les autres branches des règles reprises restent couvertes par le jeu fournisseur.

**Mode démonstration** (`web --demo`) : les contrats des deux profils, sans réseau, sans clé, sans base. L'extraction simulée reconnaît le contrat à son texte masqué **et** à son profil ; un contrat du jeu analysé sous un autre profil lève une erreur explicite (« contrat du jeu du profil sous_traitance »), jamais un résultat faussé.

**Série réelle** : `tests/test_llm_jeu.py` se paramètre par profil (`-k`) ; la série du chantier couvre les sept contrats de sous-traitance, cinq essais chacun, soit 35 analyses payantes, lancées par le propriétaire, résultat consigné à `docs/journal.md`.

## Erreurs

Aucun repli silencieux ; chaque cas est testé.

- Profil absent ou inconnu : refus à l'entrée, avant toute analyse (CLI : code de sortie non nul et message ; web : erreur de formulaire ; MCP : erreur de l'outil, sans message d'exception).
- Section `sous_traitance` absente du fichier du projet, à partir de la PR 2 : `load_config` arrête le programme.
- Type commun à deux profils dans deux domaines différents : test en échec.
- Type ou catégorie rendus par le LLM hors du profil de l'analyse : échec de validation du schéma de sortie, traité comme tout échec de sortie du LLM aujourd'hui.
- Contrat du jeu sous le mauvais profil, en démonstration : erreur explicite.
- Montant illisible (« 2 M€ ») : valeur absente de la citation, nouvelle extraction puis revue humaine.

## Critères d'acceptation

Chacun est une vérification observable, à la fin du chantier ; la PR où il devient vérifiable est indiquée entre crochets.

1. Chaque PR : `./scripts/check.sh` se termine par « check.sh : SUCCÈS » ; couverture au moins égale au seuil `fail_under` (98 %).
2. [PR 1] `run` sans `--profil`, ou avec un profil inconnu, sort avec un code non nul sans rien analyser ; même refus au formulaire web et à l'outil MCP `analyser_contrat` (`tests/test_parite.py`).
3. [PR 1] Sous `--profil fournisseur`, les treize contrats du jeu actuel rendent les mêmes clauses, constats et décisions qu'avant le chantier (`tests/test_demo.py`, doublures), et `data/evaluation/recherche.yaml` est inchangé.
4. [PR 1] Le vrai journal du poste se vérifie et se rejoue à l'identique : `uv run pytest -m journal_reel --journal-reel` passe (enregistrements v1 et v2 relus comme profil `fournisseur`).
5. [PR 1 pour `fournisseur`, PR 2 pour `sous_traitance`] Un enregistrement v3 scellé sous chaque profil se vérifie (`verify`) et se rejoue fidèlement (`replay`, code 0).
6. [PR 2] Pour chaque profil, l'extraction vérifiée exige chacun de ses types une fois et une seule, et le schéma de sortie refuse tout type d'un autre profil.
7. [PR 3] Les sept contrats de sous-traitance rendent leur décision attendue, sous doublures et dans `web --demo` ; la couverture des règles décrite au « Jeu de démonstration » est atteinte.
8. [PR 3] Chaque constat bloquant ou pénalisant des sept contrats a au moins une référence en vigueur parmi les sources rattachées à sa clause (tests du corpus et de la justification).
9. [PR 4] Série réelle, sept contrats × cinq essais : aucune décision automatique plus favorable que l'attendu ; résultat consigné au journal.
10. [PR 2] Aucun fichier de `data/corpus/raw/` sans source, licence et date dans `SOURCES.md` ; aucun texte d'EN 45545 ni de l'OMI dans le dépôt.
11. [PR 3] `tests/test_donnees_fictives.py` passe sur les sept nouveaux contrats.

## Découpage

Une branche par PR, chacune verte avec `check.sh` avant d'être poussée par le propriétaire. Prérequis à part : la mise à jour de L441-10 (voir « Corpus »).

1. **Profil explicite.** `domain/profiles.py` avec le seul profil `fournisseur` ; constantes, règles, tables du CRAG, prompts et schéma d'extraction déplacés sans changement de comportement ; `--profil` obligatoire aux trois portes et à `mesure-recherche` ; signatures des ports et de `AnalystInput` ; état, format v3 du journal, rejeu, traces ; scripts, scénarios du cluster et documentation passés à `--profil fournisseur` ; ADR 009 accepté ; CLAUDE.md mis à jour (format v3, profils). Après la PR 1 : `--profil sous_traitance` est refusé comme profil inconnu.
2. **Profil sous-traitance et son corpus.** Dans la même PR, car les tests du corpus exigent au moins un article et une fiche pour chaque type de clause (`tests/test_corpus.py`), et les fiches décrivent des règles qui doivent exister. Code : `sous_traitance` ajouté à `Profile` ; ses types, sa partition, ses unités et catégories ; ses règles ; sa section de configuration ; ses prompts d'extraction et d'explication ; ses tables du CRAG ; son schéma d'extraction ; ses termes d'absence et de catégorie ; extension de la vérification des montants en euros. Corpus : licences relues ; textes copiés à la main dans `raw/` ; manifeste et format des citations étendus ; `SOURCES.md` ; quatre fiches ; rattachements élargis ; `ingest` à relancer. Après la PR 2 : une analyse sous ce profil fonctionne de bout en bout en mode réel, sans contrat du jeu pour la démontrer.
3. **Jeu et démonstration.** Sept contrats et leurs attendus ; mode démonstration ; jeu d'évaluation de la recherche du profil et filtre par profil de `demo_queries`.
4. **Série réelle.** Filtre par profil de `tests/test_llm_jeu.py` ; série lancée par le propriétaire ; résultats et coût consignés au journal.

## Dépendances vérifiées

- **Aucune nouvelle bibliothèque** : `pyproject.toml` et `uv.lock` inchangés.
- **Sources** : articles et versions lus sur leur page officielle le 16/10/2026 (tableau du corpus). Pages : Légifrance (Code de la propriété intellectuelle, `LEGITEXT000006069414` ; Code civil, `LEGITEXT000006070721` ; Code des assurances, `LEGITEXT000006073984`), EUR-Lex (`CELEX:32014R1302`, `CELEX:32012R0748`), EASA (« Copyright, Disclaimer and Confidentiality »). Les requêtes scriptées vers Légifrance déclenchent une vérification anti-robots, non contournée : lecture page par page dans un navigateur.
- **Code confronté le 16/10/2026** : noms cités vérifiés dans le dépôt par une relecture indépendante (`domain/models.py`, `domain/verification.py`, `domain/corpus.py`, `domain/audit.py`, `application/crag.py`, `application/extraction.py`, `application/deps.py`, `application/state.py`, `application/evaluation.py`, `adapters/mcp/`, `adapters/otel/attributes.py`).

## Écarts avec l'existant

Validés par le propriétaire pendant la conception, consignés dans l'ADR 009 :

- **Cible élargie.** `spec-phase1.md` vise les directions achats et juridiques de PME et d'ETI ; s'y ajoute le donneur d'ordre d'un contrat de sous-traitance.
- **Schéma d'état.** Nouvelle clé `profile` ; `REQUIRED_KINDS`, `DOMAIN_KINDS`, `VALUE_UNITS`, `DOMAIN_LABELS`, `_SUBJECTS`, `_CATEGORY_LABELS` et le schéma d'extraction deviennent des attributs du profil ; les ports `Extractor` et `Crag` et `AnalystInput` portent le profil.
- **Format du journal v3**, avec relecture figée des v2.
- **Interface de la CLI.** `--profil` obligatoire : tout appel existant à `run` et à `mesure-recherche` change.
- **Empreinte commune.** Un réglage du profil `sous_traitance` modifié change l'empreinte de toute la configuration : les analyses fournisseur interrompues escaladent, et les revues fournisseur en attente affichent « configuration modifiée ». Conséquence acceptée.
- **Périmètre figé depuis la v1.1** (02/10/2026) : rouvert le 16/10/2026 à la demande explicite du propriétaire.

## Questions pour le cabinet

À poser lors de la présentation ; chaque réponse devient un réglage ou un chantier, jamais une supposition :

1. Qui signe ces contrats, le maître d'ouvrage ou le cabinet ? Est-ce de la sous-traitance au sens de la loi de 1975 ?
2. Sous quel droit et dans quelle langue sont-ils rédigés ?
3. Combien de contrats par projet, et comment sont-ils gérés aujourd'hui ?
4. Qu'est-ce qui coûte le plus : le temps de relecture, les oublis, les litiges ?
5. Quels seuils et quelles sévérités retiendraient-ils : assurance minimale, plafonds, délais, ce qui doit bloquer ?
6. Pour analyser leurs vrais contrats dans leur propre instance : l'envoi d'un contrat masqué à un fournisseur LLM européen leur convient-il, ou faut-il une analyse entièrement locale ?
