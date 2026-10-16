# Spec · Profils de contrat, profil sous-traitance

Version du 16 octobre 2026. Conçue en séance de brainstorming avec le propriétaire, section par section, chaque section validée. Source de vérité pour ce chantier ; `docs/spec-phase1.md` le reste pour tout ce que ce document ne modifie pas. Décision d'architecture : [ADR 009](adr-009-profils-de-contrat.md).

## Objectif et périmètre

Le système analyse aujourd'hui un seul genre de contrat : le contrat fournisseur d'une PME ou d'une ETI, sur dix types de clauses fixés dans le code. Ce chantier introduit la notion de **profil de contrat**, puis un second profil, la **sous-traitance**, pour servir un cabinet d'assistance à maîtrise d'ouvrage qui coordonne l'aménagement intérieur de transports de luxe (aviation d'affaires, ferroviaire, naval) et gère de nombreux contrats avec des corps de métier, des artisans d'art et des industriels.

Ce que le chantier doit livrer, et rien de plus :

- un profil `fournisseur` explicite, qui reprend à l'identique le comportement actuel ;
- un profil `sous_traitance`, avec ses clauses, ses règles, ses réglages, son prompt d'extraction et ses sources ;
- le choix du profil à chaque analyse, obligatoire, aux trois portes (CLI, interface web, serveur MCP), scellé dans le journal et rejouable ;
- une démonstration présentée sur le poste du propriétaire : sept contrats de sous-traitance fictifs, dans le mode démonstration de l'interface web, et une série réelle sur le modèle Mistral.

**Point de vue.** L'outil défend le **donneur d'ordre**, celui qui engage le sous-traitant : le maître d'ouvrage ou le cabinet lui-même, selon le contrat. Qui signe n'est pas connu : c'est la première question à poser au cabinet (voir « Questions pour le cabinet »).

**Souveraineté.** Inchangée : embeddings locaux, fournisseur LLM européen par défaut (Mistral). Pendant la démonstration, un vrai contrat du cabinet n'est analysé qu'avec son accord explicite, son texte masqué partant chez Mistral comme aujourd'hui.

**Données.** Contrats fictifs seulement, dans le dépôt public : aucune partie réelle, aucun nom de client, de chantier, d'architecte ou de projet. Les réglages propres au cabinet (seuils, montants, sévérités) vivront dans sa propre configuration, jamais dans le dépôt.

## Non-objectifs

- Les contrats d'architectes et les polices d'assurance comme familles de contrats : ce seront deux profils de plus, plus tard. L'assurance n'est ici vérifiée que comme une clause du contrat de sous-traitance.
- Les contrats en anglais : ils restent rejetés par `validate_input`, comme aujourd'hui.
- La comparaison de plusieurs offres, la cohérence entre plusieurs contrats (contrat principal et sous-contrats), le suivi d'exécution.
- La loi n° 75-1334 du 31 décembre 1975 relative à la sous-traitance (acceptation du sous-traitant, garanties de paiement) : elle ne s'applique qu'à une vraie sous-traitance, ce qu'on ignore.
- Le contenu technique des normes : l'outil vérifie ce que le contrat **exige** (une norme nommée, des preuves à remettre), jamais qu'un matériau est conforme, ni que la norme citée est la bonne pour le secteur. Il ne prétend jamais qu'une pièce est certifiée.
- Les normes payantes et les textes non libres : ni EN 45545-2 (CEN, vendue par les organismes de normalisation), ni les textes de l'Organisation maritime internationale n'entrent dans le corpus.
- Un modèle LLM local, un hébergement, une installation chez le cabinet ; une nouvelle animation du terminal (`scripts/demo_terminal.sh`).
- Des poids, des seuils de décision ou une politique d'arbitrage propres à chaque profil : communs aux deux profils au départ.

## Le profil

### Définition

Un profil est un ensemble nommé, en deux parties.

**Dans le code**, un nouveau module du domaine pur, `src/cdg/domain/profiles.py`. Pour chaque profil :

- ses types de clauses (ce que sont aujourd'hui `REQUIRED_KINDS`) et leur partition entre les **quatre domaines existants** (aujourd'hui `DOMAIN_KINDS`) ;
- l'unité de chaque type chiffré (aujourd'hui `verification.VALUE_UNITS`) et les catégories admises de chaque type catégoriel ;
- le libellé de chaque domaine pour le CRAG (aujourd'hui `crag.DOMAIN_LABELS`) ;
- la fonction de règles de chaque domaine ;
- le nom de son prompt d'extraction, dans `src/cdg/application/prompts/`.

`Profile = Literal["fournisseur", "sous_traitance"]`. Le profil `fournisseur` reprend à l'identique les constantes, les règles et le prompt actuels, déplacés sans changement de comportement. `domain/profiles.py` n'importe que le domaine (`tests/test_isolation.py`).

**Invariant.** Un type de clause présent dans deux profils (`revision_prix`, `delai_paiement`) appartient au même domaine dans les deux : le corpus déduit le domaine d'indexation d'une clause par une table unique (`corpus.by_domain`). Un test le vérifie.

**Dans `config/decision.yaml`** :

- les sections actuelles (`rules`, `extraction.absence_terms`, `extraction.category_terms`) restent à leur place et **sont** le profil fournisseur : une configuration archivée se relit sans changement ;
- une nouvelle section `sous_traitance`, avec `rules`, `absence_terms` et `category_terms` du profil. Règle de l'ADR 006 : absente par défaut dans le modèle Pydantic, pour relire l'archive (aucune analyse sous-traitance n'était possible avant elle), mais exigée par `load_config` dans le fichier du projet, à toute profondeur ;
- poids, seuils, marge, budget, politique d'arbitrage, contrôle de l'entrée, réglages du CRAG et de l'explication : communs.

### Choix du profil

Obligatoire à chaque analyse, sans valeur par défaut : une analyse sous le mauvais profil ne chercherait pas les bonnes clauses.

- **CLI** : `run --profil fournisseur|sous_traitance`. `relaunch` reprend le profil du contrat d'origine, lu dans son état, et le scelle de nouveau.
- **Interface web** : une liste dans le formulaire d'analyse, sans option présélectionnée ; le profil s'affiche dans la liste des contrats, le dossier et l'historique.
- **Serveur MCP** : argument `profil` obligatoire de l'outil `analyser` ; le profil figure dans la liste blanche des réponses (`adapters/mcp/presentation.py`), qui accepte les types de clauses de tous les profils.
- **Saisie commune** : `application/saisie.py` valide le profil pour le web et le serveur MCP. `tests/test_parite.py` vérifie la même méthode du service aux trois portes.

### Profil dans l'état et dans le journal

- `run_contract` place le profil dans le contexte d'analyse, avec l'empreinte de la configuration, avant tout nœud : il est dans le premier checkpoint. Nouvelle clé d'état `profile`, sans réducteur, jamais modifiée par un nœud.
- `extract_clauses`, `verify_extraction`, les analystes et `decision_gate` lisent le profil de l'état ; aucun nœud ne lit plus `REQUIRED_KINDS` directement.
- **Format v3 du journal.** Le profil entre dans la partie décision de l'enregistrement (`DecisionRecord`), donc dans `decision_hash`. Les enregistrements v2 sont relus par des modèles figés (`AuditRecordV2`, `DecisionRecordV2`), comme les v1 aujourd'hui ; v1 et v2 restent vérifiables, et sont relus comme profil `fournisseur`, le seul qui existait alors.
- **Rejeu.** Un enregistrement v3 est rejoué sous son profil et sa configuration archivée ; v1 et v2 sous le profil `fournisseur`. Sens fidèle et réévaluation inchangés (ADR 005).
- **Analyse interrompue.** Le profil est dans son checkpoint. Une analyse commencée avant ce chantier n'a pas de clé `profile` : elle est relue comme `fournisseur`, seul profil possible alors, et sa configuration a changé, donc elle part en ESCALADE selon la règle existante.
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
| financier | `penalites_retard` | plafond, % du montant du contrat | absentes ou plafonnées sous le minimum : pénalité (règle actuelle des pénalités d'exécution) |
| conformité | `obligation_certification` | catégorie | `norme_et_preuves` : rien ; `norme_sans_preuves` ou `conformite_generale` : pénalité ; absente : **blocage** |
| conformité | `assurance_sous_traitant` | montant garanti, € par sinistre | sous le minimum réglé, ou non chiffré : pénalité ; absente : pénalité |
| opérationnel | `delai_execution` | catégorie | `date_contractuelle` : rien ; `delai_indicatif` : pénalité ; absent : pénalité |

Catégories :

- `protection_design` : `usage_limite_au_projet` (dessins et documents remis utilisés pour le seul projet, et confidentiels), `confidentialite_seule` (confidentialité sans limite d'usage), `reutilisation_autorisee` (le sous-traitant peut les réutiliser).
- `obligation_certification` : `norme_et_preuves` (normes nommées et preuves à remettre : procès-verbaux d'essais, certificats), `norme_sans_preuves`, `conformite_generale` (« conforme aux normes en vigueur », sans norme nommée).
- `delai_execution` : `date_contractuelle` (date ou planning qui engage le sous-traitant), `delai_indicatif`.

Les listes de catégories acceptées, pénalisées et bloquantes sont réglées dans la configuration, comme `transfer_safeguards` aujourd'hui. Les fonctions de règles communes (plafonds, pénalités) sont partagées entre profils dans `domain/rules/` ; seuls les messages des constats nomment les parties du profil (« donneur d'ordre », « sous-traitant »).

**Contrôles de l'extraction, inchangés et appliqués aux nouveaux types** : citation mot pour mot dans le texte masqué, valeur présente dans la citation avec son unité, catégorie évoquée par la citation (termes de `category_terms`), absence redemandée si le texte évoque la clause (termes de `absence_terms`), citation hors consigne refusée.

**Extension de la vérification des valeurs.** `verification.quantities` ne lit aujourd'hui ni l'euro ni les séparateurs de milliers. Pour `assurance_sous_traitant`, unité « € » : chiffres avec espace, espace insécable, espace fine insécable ou point comme séparateur de milliers, suivis de « € », « euros » ou « EUR » (« 2 000 000 € », « 2.000.000 euros »). Les formes en lettres et les multiples (« deux millions d'euros », « 2 M€ ») ne sont pas lues : limite documentée, qui mène à une nouvelle extraction puis à la revue humaine, dans le sens prudent. Testé avant d'être codé, y compris sur les faux positifs (un montant n'est pas lu comme un pourcentage, ni l'inverse).

## Corpus

Règle actuelle, inchangée : une clause dont le constat bloque ou pénalise exige au moins une référence en vigueur, sinon le domaine est `INSUFFISANT` ; chaque source déclare les clauses qu'elle peut justifier (`manifest.yaml`, en-tête des fiches, `SOURCES.md`).

**Sources existantes, rattachements élargis** :

- C. civ., art. 1170, 1171 et 1231-3 : `responsabilite_donneur_ordre`, `responsabilite_sous_traitant` ;
- C. civ., art. 1231-5 : `penalites_retard` ;
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

- le manifeste nomme aujourd'hui les fichiers EUR-Lex par article (`art-{article}.txt`) ; un point d'annexe demande d'étendre ce format, testé comme le reste ;
- C. civ., art. 1344, écarté : il porte sur la mise en demeure de payer ;
- CS-25 de l'EASA n'est **pas ingérée** : reproduction autorisée sous réserve de citer la source (page « Copyright, Disclaimer and Confidentiality » de l'EASA, lue le 16/10/2026), mais texte en anglais seulement, alors que le corpus est en français ; la fiche la cite par son nom ;
- le naval n'a aucune source sectorielle libre : la fiche sur la certification le dit.

**Licences.** EUR-Lex et Légifrance : conditions vérifiées le 26/09/2026 (`SOURCES.md`), à relire en PR 2 ; mentions et date de mise à jour dans `SOURCES.md`, comme pour les sources actuelles. Une source dont la réutilisation n'est pas claire n'entre pas dans `raw/`.

**Nouvelles fiches** (`data/corpus/fiches/`, structure actuelle : « Ce que dit le texte », paraphrases sourcées ; « Comment le projet l'applique », choix de politique du donneur d'ordre) : protection du design, certification et preuves, assurances du sous-traitant, délais d'exécution.

**Mesure de la recherche.** Un jeu d'évaluation propre au profil, `data/evaluation/recherche-sous-traitance.yaml`, établi sans le rattachement déclaré comme le premier ; `mesure-recherche --profil sous_traitance`. Le jeu figé de l'ADR 006 ne change pas.

## Jeu de démonstration

Sept contrats fictifs dans `data/contracts/`, avec leurs attendus dans `attendus.yaml`, qui reçoit un champ `profile` obligatoire par contrat (les treize existants : `fournisseur`). Parties fictives, coordonnées dans les plages réservées (`tests/test_donnees_fictives.py`), aucun nom réel.

| Contrat | Univers | Décision attendue | Ce qu'il montre |
| --- | --- | --- | --- |
| `st-01-go-cabine` | mobilier d'une cabine d'avion d'affaires | `GO` | un contrat propre |
| `st-02-reserves-marqueterie` | marqueterie d'une voiture de train historique | `GO_RESERVES` | délai indicatif, assurance non chiffrée, pénalités de retard sous le minimum |
| `st-03-no-go-ferronnerie` | ferronnerie d'art pour un yacht | `NO_GO` | réutilisation des dessins autorisée |
| `st-04-no-go-sellerie` | sellerie d'une voiture de train | `NO_GO` | aucune obligation de certification |
| `st-05-reserves-revetements` | revêtements d'une cabine d'avion | `GO_RESERVES` | norme sans preuves, plafond du sous-traitant bas, délai de paiement long, assurance sous le minimum |
| `st-06-no-go-ebenisterie` | ébénisterie d'un navire | `NO_GO` | responsabilité du donneur d'ordre illimitée, révision de prix non plafonnée |
| `st-07-realiste-amenagement` | aménagement d'un navire, rédaction réaliste | `ESCALADE`, puis décision humaine | formulation ambiguë que le code refuse de deviner ; « conforme aux normes en vigueur » ; absence de protection du design |

Avec `hard_block_review: false` (réglage actuel), un blocage dur donne `NO_GO` sans revue humaine. La répartition exacte des clauses est fixée en PR 3 ; `tests/test_demo.py` exige que **chaque branche de chaque nouvelle règle** se déclenche dans au moins un contrat.

**Mode démonstration** (`web --demo`) : les contrats des deux profils, sans réseau, sans clé, sans base. L'extraction simulée reconnaît le contrat à son texte masqué **et** à son profil ; un contrat du jeu analysé sous un autre profil lève une erreur explicite (« contrat du jeu du profil sous_traitance »), jamais un résultat faussé.

**Série réelle** : `tests/test_llm_jeu.py` couvre les sept contrats, cinq essais chacun ; payante, lancée par le propriétaire, résultat consigné à `docs/journal.md`.

## Erreurs

Aucun repli silencieux ; chaque cas est testé.

- Profil absent ou inconnu : refus à l'entrée, avant toute analyse (CLI : code de sortie non nul et message ; web : erreur de formulaire ; MCP : erreur de l'outil, sans message d'exception).
- Section `sous_traitance` absente du fichier du projet : `load_config` arrête le programme.
- Type commun à deux profils dans deux domaines différents : test en échec.
- Type de clause rendu par le LLM hors du profil de l'analyse : problème d'extraction (« type de clause inconnu »), nouvelle extraction puis revue humaine, comme aujourd'hui.
- Contrat du jeu sous le mauvais profil, en démonstration : erreur explicite.
- Montant illisible (« 2 M€ ») : valeur absente de la citation, nouvelle extraction puis revue humaine.

## Critères d'acceptation

Chacun est une vérification observable.

1. `./scripts/check.sh` se termine par « check.sh : SUCCÈS » ; couverture au moins égale au seuil `fail_under` (98 %).
2. `run` sans `--profil`, ou avec un profil inconnu, sort avec un code non nul sans rien analyser ; même refus au formulaire web et à l'outil MCP `analyser` (`tests/test_parite.py`).
3. Sous `--profil fournisseur`, les treize contrats du jeu actuel rendent les mêmes clauses, constats et décisions qu'avant le chantier (`tests/test_demo.py`, doublures).
4. Le vrai journal du poste se vérifie et se rejoue à l'identique : `uv run pytest -m journal_reel --journal-reel` passe (enregistrements v1 et v2 relus comme profil `fournisseur`).
5. Un enregistrement v3 scellé sous chaque profil se vérifie (`verify`) et se rejoue fidèlement (`replay`, code 0).
6. Pour chaque profil, l'extraction vérifiée exige chacun de ses types une fois et une seule, et refuse tout type d'un autre profil.
7. Les sept contrats de sous-traitance rendent leur décision attendue, sous doublures et dans `web --demo` ; chaque branche de chaque nouvelle règle se déclenche dans au moins un contrat.
8. Chaque constat bloquant ou pénalisant des sept contrats a au moins une référence en vigueur parmi les sources rattachées à sa clause (tests du corpus et de la justification).
9. Série réelle, sept contrats × cinq essais : aucune décision automatique plus favorable que l'attendu ; résultat consigné au journal.
10. Aucun fichier de `data/corpus/raw/` sans source, licence et date dans `SOURCES.md` ; aucun texte d'EN 45545 ni de l'OMI dans le dépôt.
11. `tests/test_donnees_fictives.py` passe sur les sept nouveaux contrats.

## Découpage

Une branche par PR, chacune verte avec `check.sh` avant d'être poussée par le propriétaire.

1. **Profil explicite.** `domain/profiles.py` ; profil `fournisseur` déplacé sans changement de comportement ; `--profil` obligatoire aux trois portes ; état, format v3 du journal, rejeu, traces ; scripts, scénarios du cluster et documentation passés à `--profil fournisseur` ; ADR 009 accepté ; CLAUDE.md mis à jour (format v3, profils).
2. **Corpus.** Licences relues ; textes copiés à la main dans `raw/` ; manifeste (format d'annexe EUR-Lex), `SOURCES.md`, quatre fiches, rattachements élargis ; jeu d'évaluation de la recherche du profil.
3. **Profil sous-traitance.** Règles et section de configuration ; prompt d'extraction ; termes d'absence et de catégorie ; extension de la vérification des montants en euros ; sept contrats et leurs attendus ; mode démonstration.
4. **Série réelle.** Lancée par le propriétaire ; résultats et coût consignés au journal.

## Dépendances vérifiées

- **Aucune nouvelle bibliothèque** : `pyproject.toml` et `uv.lock` inchangés.
- **Sources** : articles et versions lus sur leur page officielle le 16/10/2026 (tableau du corpus). Pages : Légifrance (Code de la propriété intellectuelle, `LEGITEXT000006069414` ; Code civil, `LEGITEXT000006070721` ; Code des assurances, `LEGITEXT000006073984`), EUR-Lex (`CELEX:32014R1302`, `CELEX:32012R0748`), EASA (« Copyright, Disclaimer and Confidentiality »). Les requêtes scriptées vers Légifrance déclenchent une vérification anti-robots, non contournée : lecture page par page dans un navigateur.

## Écarts avec l'existant

Validés par le propriétaire pendant la conception, consignés dans l'ADR 009 :

- **Cible élargie.** `spec-phase1.md` vise les directions achats et juridiques de PME et d'ETI ; s'y ajoute le donneur d'ordre d'un contrat de sous-traitance.
- **Schéma d'état.** Nouvelle clé `profile` ; `REQUIRED_KINDS`, `DOMAIN_KINDS`, `VALUE_UNITS` et `DOMAIN_LABELS` deviennent des attributs du profil.
- **Format du journal v3**, avec relecture figée des v2.
- **Interface de la CLI.** `--profil` obligatoire : tout appel existant à `run` change.
- **Périmètre figé depuis la v1.1** (02/10/2026) : rouvert le 16/10/2026 à la demande explicite du propriétaire.

## Questions pour le cabinet

À poser lors de la présentation ; chaque réponse devient un réglage ou un chantier, jamais une supposition :

1. Qui signe ces contrats, le maître d'ouvrage ou le cabinet ? Est-ce de la sous-traitance au sens de la loi de 1975 ?
2. Sous quel droit et dans quelle langue sont-ils rédigés ?
3. Combien de contrats par projet, et comment sont-ils gérés aujourd'hui ?
4. Qu'est-ce qui coûte le plus : le temps de relecture, les oublis, les litiges ?
5. Quels seuils et quelles sévérités retiendraient-ils : assurance minimale, plafonds, délais, ce qui doit bloquer ?
6. L'envoi d'un contrat masqué à un fournisseur LLM européen leur convient-il, ou faut-il une analyse entièrement locale ?
