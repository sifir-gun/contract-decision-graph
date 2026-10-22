# Spec · Profils appel d'offres public et privé

Version du 22 octobre 2026, révisée le même jour après une relecture indépendante (constats à trancher hors des verdicts, contexte des règles, rattachement par article, jeu de démonstration, découpage). Conçue en séance de brainstorming avec le propriétaire les 21 et 22 octobre, section par section, chaque section validée ; règles amendées le 22/10 après lecture des textes sur Légifrance (« Amendements imposés par les textes »). Source de vérité pour ce chantier ; `docs/spec-phase1.md` et `docs/spec-profils-sous-traitance.md` le restent pour tout ce que ce document ne modifie pas. Décision d'architecture : [ADR 011](adr-011-appels-offres.md), proposé.

**Condition d'entrée.** Aucune ligne de code de ce chantier avant que (1) le chantier sous-traitance soit terminé (PR 2, 3 et 4 de sa spec, fusionnées), (2) les réponses du cabinet aux questions 1 à 7 (« Questions pour le cabinet ») soient consignées dans `docs/journal.md`, et (3) cette spec soit révisée en conséquence et ses écarts validés par le propriétaire. Le profil privé a sa propre condition (question 1).

## Objectif et périmètre

Le système analyse aujourd'hui des contrats du point de vue de celui qui achète ou fait exécuter : l'acheteur (profil `fournisseur`) et, après le chantier sous-traitance, le donneur d'ordre (profil `sous_traitance`). Ce chantier ajoute deux profils pour le même cabinet d'assistance à maîtrise d'ouvrage, quand il reçoit une consultation pour ses propres missions : `appel_offres_public` et `appel_offres_prive`. Le verdict répond à la question « répondre ou ne pas répondre ».

Ce que le chantier doit livrer, et rien de plus :

- une entrée **secteur** (`ferroviaire`, `aeronautique`, `naval`), déclarée à chaque analyse pour ces deux profils, scellée et rejouable ;
- des **constats à trancher**, qui imposent la revue humaine sans peser sur le score ;
- un **rattachement du corpus par profil** : un article ne justifie une clause que pour les profils qu'il déclare ;
- une **fiche du cabinet** dans la configuration, à laquelle les règles comparent ce que le dossier exige ;
- le profil `appel_offres_public`, avec ses clauses, ses règles, ses réglages, ses prompts, ses tables du CRAG, son corpus et ses sept dossiers de démonstration ;
- le profil `appel_offres_prive`, de même avec cinq dossiers, **sous condition** (question 1 au cabinet) ;
- une série réelle sur le modèle Mistral.

**Point de vue inversé.** L'outil défend le **candidat**, futur titulaire du marché : une clause qui protège l'acheteur (pénalités non plafonnées, responsabilité illimitée du titulaire, cession totale des droits) devient un risque.

**Ce que couvre le verdict** :

1. les risques du futur contrat ;
2. les normes du secteur déclaré : le dossier les exige-t-il ?
3. l'opportunité : délai de remise compté depuis la date d'analyse scellée ; chiffre d'affaires, assurance, références et certifications exigés, comparés à la fiche du cabinet ; poids du prix dans les critères d'attribution.

Les décisions restent celles du moteur (`GO`, `GO_RESERVES`, `NO_GO`, `ESCALADE`), affichées pour ces deux profils « répondre », « répondre avec réserves », « ne pas répondre », « escalade ».

**Entrée.** Un texte en français, collé : l'avis ou le règlement de la consultation, et le projet de contrat (CCAP) ou le cahier des charges. Comme aujourd'hui, une seule entrée texte ; profil et secteur obligatoires.

**Données.** Dossiers fictifs seulement, dans le dépôt public. Un vrai dossier du cabinet n'entre ni dans le dépôt, ni dans le journal du poste du propriétaire ; il ne s'analyse que dans l'instance du cabinet. Les vraies valeurs de sa fiche vivent dans sa propre configuration ; le dépôt porte une fiche fictive.

## Non-objectifs

- La récupération des avis par API. Constaté le 21/10/2026 : l'API BOAMP de la DILA (Licence Ouverte, sans clé mentionnée) et l'API de recherche TED (sans authentification) donnent les **avis**, pas les pièces du dossier ; aucune API officielle des pièces (règlement, CCAP, CCTP), publiées sur le profil de chaque acheteur. Sous-projet possible plus tard, avec son port, son adaptateur et un avis scellé pour le rejeu.
- Plusieurs pièces saisies séparément, l'ordre de priorité entre les pièces.
- Les chances de gagner, le prix de l'offre, la concurrence, la charge du cabinet.
- Savoir si le cabinet maîtrise les normes exigées ; le contenu des normes ; la conformité d'une pièce. EN 45545-2 (payante) et les textes de l'Organisation maritime internationale (non libres) restent hors du corpus : seuls leurs **noms** sont lus.
- Les dossiers en anglais : toujours rejetés par `validate_input`.
- La rédaction de la réponse (mémoire technique, DUME, DC1, DC2).
- Le contrôle de la régularité du dossier lui-même (par exemple un chiffre d'affaires exigé au-delà du plafond de R2142-7) : l'outil juge si le cabinet peut et doit répondre, pas si l'acheteur respecte le code.
- Un secteur pour les profils `fournisseur` et `sous_traitance` : fourni avec eux, il est refusé.
- Des poids, des seuils de décision ou une politique d'arbitrage propres à ces profils : communs à tous les profils, comme pour la sous-traitance.
- Un CCAG autre que le CCAG-PI pris en charge : cité, il donne un constat à trancher.
- Un rattachement déterministe d'un article à un constat : les références d'un constat viennent du CRAG, comme aujourd'hui.

## Les entrées

### Secteur

- `Sector = Literal["ferroviaire", "aeronautique", "naval"]`, dans `domain/profiles.py`. Chaque profil déclare s'il exige un secteur (`ProfileSpec`) : oui pour les deux profils d'appel d'offres, non pour les autres.
- **Contrôle commun** : `application/saisie.py` pour l'interface et le serveur MCP, même règle dans la CLI. Refusés, avant toute analyse : profil d'appel d'offres sans secteur ; autre profil avec un secteur ; secteur inconnu, vide ou d'une autre casse. Les messages citent les valeurs admises et ne reprennent jamais la valeur reçue (comme pour le profil, ADR 010 et amendement de l'ADR 007).
- **Portes** : `run --secteur` ; liste sans présélection dans le formulaire d'analyse ; argument `secteur` de l'outil `analyser_contrat`, dans la liste blanche des réponses (`adapters/mcp/presentation.py`). Le secteur s'affiche dans la liste des contrats, le dossier et l'historique. `relaunch` reprend le secteur du contrat d'origine, lu dans son état. `tests/test_parite.py` vérifie le même secteur et le même refus aux trois portes.
- **État** : clé `sector`, posée avec le profil avant tout nœud, jamais modifiée par un nœud ; `None` pour les profils sans secteur. `AnalystInput` la porte. Une seule lecture tolérante, sur le modèle de `profiles.of_state` : un état sans clé `sector` vaut `None`.
- **Traces** : attribut `cdg.secteur` dans la liste blanche (`adapters/otel/attributes.py`).
- **Nom jamais renommé ni retiré** : un secteur scellé dans un enregistrement ou un checkpoint reste lisible ; un secteur s'ajoute seulement.

### Fiche du cabinet et listes lues par le code

Section `appels_offres` de `config/decision.yaml`, commune aux deux profils ; structure indicative, fixée dans le plan :

```yaml
appels_offres:
  candidat:
    chiffre_affaires_annuel: 1200000   # €, dernier exercice
    assurance_rc_pro: 2000000          # € par sinistre
    references:                        # missions similaires, par secteur
      ferroviaire: 4
      aeronautique: 6
      naval: 1
    certifications: ["ISO 9001"]
  normes_par_secteur:
    ferroviaire: ["EN 45545-2"]
    aeronautique: ["CS-25"]
    naval: ["SOLAS"]
  prefixes_normes: ["EN", "NF", "ISO", "CS-"]
  certifications_reconnues: ["ISO 9001", "ISO 14001", "EN 9100"]
```

- Valeurs fictives dans le dépôt, valeurs de démonstration pour les listes ; les vraies dans la configuration de l'instance du cabinet (questions 4 et 8).
- Modèle Pydantic. Règle de l'ADR 006 : absente par défaut dans le modèle (une configuration archivée d'avant ce chantier se relit), exigée par `load_config` dans le fichier du projet, à toute profondeur ; un secteur sans nombre de références ou sans liste de normes est refusé au démarrage.
- Archivée avec la configuration et couverte par son empreinte : la modifier change l'empreinte de toute la configuration (« Écarts avec l'existant »). Elle entre avec le profil public (PR 2), pas avant : une section que rien ne lit changerait l'empreinte pour rien.
- Un identifiant signalé par un préfixe mais absent de toutes les listes est une « norme non reconnue ».
- Limite assumée : « trois références de moins de trois ans » n'est lu que comme un nombre ; le cabinet tient à jour son compte de références valables.

## Clauses et règles

Le LLM rend, pour chaque type, la clause et sa citation ; **le code** lit les dates, les noms de normes et de certifications dans la citation vérifiée, et compare. Chaque sévérité (blocage, pénalité et son poids) et chaque seuil vivent dans `decision.yaml`, section du profil ; les valeurs ci-dessous sont des valeurs de démonstration, à valider avec le cabinet.

### Types de clauses

Treize types pour le profil public, douze pour le privé (tous sauf `ccag_reference`). Les **types d'opportunité** sont `chiffre_affaires_exige`, `assurance_exigee`, `certifications_exigees`, `references_exigees`, `delai_remise` et `poids_critere_prix`.

| Domaine | Type | Lu comment |
| --- | --- | --- |
| juridique | `responsabilite_titulaire` | plafond, % du montant du marché |
| juridique | `propriete_intellectuelle` | catégorie : `concession`, `cession_limitee`, `cession_totale` |
| juridique | `ccag_reference` (public) | catégorie : `ccag_pi`, `ccag_autre` |
| financier | `penalites_retard_execution` | plafond, % du montant du marché |
| financier | `delai_paiement` | jours et point de départ ; public : `reception_demande` (R2192-12), `date_facture`, `fin_de_mois` ; privé : les catégories du profil fournisseur (`date_facture`, `fin_de_mois`, `facture_periodique`) |
| financier | `forme_prix` | catégorie : `revisable`, `actualisable`, `ferme` |
| financier | `chiffre_affaires_exige` | € |
| conformité | `normes_secteur` | identifiants de normes, lus par le code |
| conformité | `assurance_exigee` | € par sinistre |
| conformité | `certifications_exigees` | identifiants de certifications, lus par le code |
| opérationnel | `delai_remise` | date limite, lue par le code |
| opérationnel | `references_exigees` | nombre de références |
| opérationnel | `poids_critere_prix` | % de la note, ou catégorie `critere_unique` (critère unique du coût, R2152-7, 1°) ; pour une fourchette (R2152-12), la borne haute est demandée au modèle |

`penalites_retard_execution` reprend le nom du profil sous-traitance (même domaine, sens de la règle propre à chaque profil) : « pénalités de retard » désigne en droit commercial celles d'un retard de paiement (C. com., art. L441-10, II). `delai_paiement` est commun avec les profils fournisseur et sous-traitance, au même domaine ; ses catégories, ses seuils et sa règle sont ceux du profil. Noms des parties dans les constats : « acheteur » et « titulaire » (public, vocabulaire du CCAG) ; « client » et « titulaire » (privé).

### Règles

Trois effets, comme aujourd'hui (`domain/rules/_common.py`) : blocage, pénalité, information ; seuls les deux premiers exigent une référence en vigueur. S'y ajoute le constat à trancher (section suivante), qui n'est pas un constat de règle au sens de `RuleFinding` et n'exige aucune référence.

| Type | Public | Privé |
| --- | --- | --- |
| `responsabilite_titulaire` | sans plafond, absente, ou plafond au-dessus du maximum (100 %) : pénalité | idem |
| `propriete_intellectuelle` | `cession_totale` : pénalité ; muette sous le CCAG-PI : `cession_limitee` (art. 35.2.1), information ; muette sans CCAG : information (CPI L131-3 : une cession est expresse) | `cession_totale` : pénalité ; absente : information |
| `ccag_reference` | `ccag_pi` : valeurs par défaut ci-dessous ; `ccag_autre` : constat à trancher, et les autres règles prennent leur branche « sans CCAG » ; absente : aucun défaut | — |
| `penalites_retard_execution` | non plafonnées (sans CCAG ou sous `ccag_autre`, ou par dérogation au CCAG-PI lue dans la citation) : **blocage** ; plafond au-dessus du maximum (10 %) : pénalité ; muettes ou sans plafond sous le CCAG-PI sans dérogation : 10 % (art. 14.1.2), information ; absentes sans CCAG : information | non plafonnées : **blocage** ; plafond au-dessus du maximum : pénalité ; absentes : information |
| `delai_paiement` | plus de 30 jours, ou point de départ `fin_de_mois` : pénalité, le constat précisant que 50 jours (établissements publics de santé) et 60 jours (entreprises publiques) sont légaux (R2192-11), le code ne connaissant pas le type d'acheteur ; présent sans nombre de jours : pénalité par prudence ; muet : délai réglementaire, information | règle commune du profil fournisseur (`_payment_delay`), seuils du profil : 60 jours après la facture, 45 jours fin de mois (L441-10, I) |
| `forme_prix` | `ferme` : pénalité ; muette sous le CCAG-PI : `ferme` (art. 10.1.1), pénalité, le constat citant R2112-10 et R2112-11 (actualisation obligatoire pour un service non courant, que le code ne sait pas distinguer) ; muette sans CCAG : pénalité | `ferme` ou muette : pénalité |
| `chiffre_affaires_exige` | au-dessus de celui de la fiche : pénalité (« candidature seule impossible ; groupement ou capacités d'un tiers », R2142-3, R2143-12) ; présent sans montant : information | au-dessus de celui de la fiche : **blocage** ; sans montant : information |
| `normes_secteur` | une norme attendue pour le secteur déclaré : rien ; seulement des normes d'autres secteurs, ou exigence de conformité sans norme nommée (« normes en vigueur ») : pénalité ; absente : pénalité ; en plus, toute norme non reconnue dans la citation : constat à trancher | idem |
| `assurance_exigee` | au-dessus de celle de la fiche : pénalité ; sans montant (art. 9 du CCAG-PI) : information | au-dessus de celle de la fiche : pénalité ; sans montant : information |
| `certifications_exigees` | une certification que la fiche n'a pas : pénalité (preuve équivalente, R2143-5 ; capacités d'un tiers, R2142-3) ; en plus, toute certification hors catalogue : constat à trancher | non détenue : **blocage** ; hors catalogue : constat à trancher |
| `delai_remise` | date lue, antérieure à la date d'analyse : **blocage** ; date lue, moins de jours restants que le minimum (15) : pénalité ; date lue, délai suffisant : rien ; aucune date lisible, ou plusieurs : constat à trancher ; absente : constat à trancher | idem |
| `references_exigees` | plus que la fiche n'en compte pour le secteur : pénalité (R2142-14 : l'absence de références de même nature ne peut justifier à elle seule l'élimination) ; présentes sans nombre : information | plus que la fiche n'en compte : **blocage** ; sans nombre : information |
| `poids_critere_prix` | au-dessus du maximum (50 %), ou `critere_unique` : pénalité ; présent sans pourcentage : information ; absent : rien | idem |

Un type d'opportunité absent du dossier ne donne aucun constat, sauf `delai_remise` (absente : constat à trancher, car on ne décide pas de répondre sans date limite). Un type d'opportunité présent mais non chiffré donne une information : l'exigence est à vérifier, rien n'est comparable. Dans le public, il reste deux blocages : les pénalités non plafonnées et la date de remise passée ; toute insuffisance du cabinet face au dossier y devient une réserve. Avec `hard_block_review: false` (réglage actuel), un blocage donne `NO_GO` sans revue humaine.

### Contexte des règles

Les règles d'aujourd'hui ne reçoivent que les clauses et la configuration (`RuleFn = Callable[[list[Clause], DecisionConfig], Assessment]`). Celles de ces profils ont besoin du secteur, de la date d'analyse et de la catégorie du CCAG cité. **La signature change** : chaque règle reçoit un contexte (profil, secteur, date d'analyse, catégorie de `ccag_reference` après vérification), que les règles existantes ignorent. Le nœud `analyst` le construit depuis l'état et `AnalystInput` ; le rejeu (`audit._rejustified`) depuis l'enregistrement scellé (secteur et `analysis_date` de la partie décision, `ccag_reference` parmi ses clauses). Une décision rejouée l'est donc avec le secteur, la date et le CCAG de son scellement.

### Le CCAG dans le public

Un CCAP renvoie à un CCAG et s'en remet à lui pour ce qu'il ne dit pas ; le CCAG ne s'applique qu'aux marchés qui s'y réfèrent, et une dérogation doit être écrite dans le CCAP (CCAG-PI, art. 1.1 et 1.2). Une clause muette du CCAP n'est donc pas absente du marché.

- Valeurs par défaut du CCAG-PI, dans `decision.yaml`, section du profil : pénalités plafonnées à 10 % (art. 14.1.2) ; `cession_limitee` (art. 35.2.1) ; `ferme` (art. 10.1.1) ; assurance sans montant (art. 9). Chacune porte un **extrait d'appui** : une phrase de l'article qui la fonde (par exemple « Les prix sont réputés fermes. »).
- Une règle qui applique une valeur du CCAG le dit dans son constat (« CCAP muet : plafond de 10 % du CCAG-PI, art. 14.1.2 »), par une information, ou par la pénalité qui en découle (`forme_prix`). Les références d'une pénalité viennent du CRAG, parmi les articles rattachés à la clause et au profil, comme pour tout constat.
- Dérogation : termes réglés (`derogation_terms`, par exemple « par dérogation », « déroge ») cherchés par le code dans la citation de la clause ; une clause sans plafond dont la citation évoque une dérogation est non plafonnée. Sans terme de dérogation, la valeur du CCAG s'applique.
- **Un test vérifie que chaque extrait d'appui figure mot pour mot dans l'article du CCAG-PI ingéré** : la configuration ne peut s'écarter du texte sans qu'un test échoue.
- `ccag_autre` (CCAG-FCS, CCAG-Travaux…) : constat à trancher ; aucune valeur par défaut n'est supposée, les autres règles jugent les clauses comme sans CCAG.

### Constat à trancher

Nouveau : un constat qu'une règle ne peut résoudre (CCAG non pris en charge, norme non reconnue, certification hors catalogue, date de remise illisible, ambiguë ou absente) **impose la revue humaine**, la proposition restant celle des règles. C'est le mécanisme des constats du contrat (`input_findings`, J4 : `decide` impose alors la revue), étendu aux constats des règles :

- les règles rendent leurs constats à trancher à part de leurs `RuleFinding` (un champ de `Assessment`, structure interne non scellée) : ils ne pèsent ni sur le score, ni sur le blocage, et n'exigent aucune référence, donc ne rendent jamais un domaine `INSUFFISANT` ;
- l'analyste les écrit dans une clé d'état dédiée, avec son domaine ; `decide` impose la revue s'il y en a, comme pour `input_findings` ;
- ils sont scellés **au niveau de la partie décision du v4**, comme le profil l'a été au v3. `AgentVerdict` et `Clause` ne changent pas : un champ ajouté à l'un d'eux, même avec une valeur par défaut, changerait la relecture et le rejeu des v1 à v3 scellés (`_DecisionPart.verdicts` leur est commun) ;
- au rejeu, ils sont recalculés par les règles, puis comparés comme le reste de la partie décision.

Un constat à trancher ne reprend **jamais** un identifiant lu dans le dossier : il nomme le type et renvoie à la citation (« une norme exigée n'est pas reconnue : voir la citation »). La citation reste, comme aujourd'hui, montrée sur demande et dans son enveloppe par le serveur MCP. Seuls des identifiants des listes de la configuration peuvent figurer dans un constat (« normes citées : EN 45545-2, attendue pour le ferroviaire »).

### Lecture par le code

Fonctions pures du domaine, testées avant d'être codées, y compris sur les faux positifs :

- **dates** en français : « 14 novembre 2026 », « 1er décembre 2026 », « 14/11/2026 », « 14.11.2026 », avec ou sans heure. Exactement une date lisible dans la citation de `delai_remise`, sinon constat à trancher (sans année, deux dates). Jours restants comptés depuis la date d'analyse scellée (`analysis_date`, déjà dans la partie décision de l'enregistrement) ;
- **nombres en lettres** de un à vingt (« trois références »), pour `references_exigees` ; un montant en lettres ou en multiples (« deux millions d'euros », « 2 M€ ») reste illisible : nouvelle extraction, puis revue humaine, comme dans la spec sous-traitance ;
- **montants en euros** : lecture prévue par la PR 2 de la sous-traitance, reprise ;
- **identifiants** de normes et de certifications : recherche en mots entiers, après normalisation NFKC, insensible à la casse et aux espaces internes (« EN45545-2 », « EN 45545-2 »).

Contrôles de l'extraction inchangés et appliqués aux nouveaux types : citation mot pour mot dans le texte masqué, valeur présente dans la citation avec son unité, catégorie évoquée par la citation (`category_terms`), absence redemandée si le texte évoque la clause (`absence_terms`), citation hors consigne refusée.

### Contrôle de l'explication

`domain/explanation.py` ne reconnaît aujourd'hui que les articles de forme `L` ou sans préfixe (`_NUMBER`) : « art. R2192-12 » n'y est pas vu, et « art. 14.1.2 » y devient « 14 ». Le contrôle des références citées par le LLM perdrait les articles du profil public sans le signaler, et le test `test_aucun_numero_d_article_commun_a_deux_sources` casserait. La PR 2 étend la reconnaissance aux préfixes `R` et `D` et aux points des articles du CCAG, testée avant d'être codée, et vérifie qu'aucun numéro n'est commun à deux sources (le CCAG-PI numérote ses articles de 1 à 43). Elle ajoute aussi les libellés de décision du profil au contrôle de l'explication.

## Corpus

Règle actuelle, inchangée : un constat qui bloque ou pénalise exige au moins une référence en vigueur parmi les sources rattachées à sa clause, sinon le domaine est `INSUFFISANT`.

**Rattachement par profil, article par article.** Le public et le privé partagent leurs types, pas leurs textes : aujourd'hui la recherche filtre par domaine et par type (`domain = %s AND %s = ANY(kinds)`, `adapters/postgres/rag_store.py`), et une analyse publique pourrait retenir L441-10 pour le délai de paiement. Dans le manifeste, une source est un code entier (`code-civil` réunira 1102, privé seulement, 1163 et 1166, les deux profils, 1170 et 1231-5, tous les profils) : chaque **article** déclare donc ses profils, comme il déclare ses types. Une fiche déclare ses profils dans son en-tête ; une fiche dont le contenu diffère d'un régime à l'autre est scindée, une par profil. `SOURCES.md` le reporte. Colonne `profiles` de `rag_chunks` (migration suivante libre, 010 si la sous-traitance n'en ajoute pas ; puis relancer `ingest`), profil dans la recherche du port, filtre par domaine, type **et** profil. Les articles actuels déclarent les profils qu'ils servent déjà : la recherche des profils existants rend exactement les mêmes résultats, et `data/evaluation/recherche.yaml` est inchangé.

**Sources** (lues sur leur page officielle le 22/10/2026, sauf mention ; texte copié à la main dans `raw/` à la PR du profil) :

| Profils | Source | Version en vigueur | Clauses | Ce qu'elle apporte |
| --- | --- | --- | --- | --- |
| public | CCP, L2192-10 | depuis le 01/04/2019 | `delai_paiement` | le marché ne peut prévoir un délai supérieur au délai réglementaire |
| public | CCP, R2192-10 | depuis le 01/04/2019 | `delai_paiement` | trente jours pour les pouvoirs adjudicateurs |
| public | CCP, R2192-11 | depuis le 01/04/2019 | `delai_paiement` | cinquante jours (établissements publics de santé), soixante (entreprises publiques) |
| public | CCP, R2192-12 | depuis le 01/04/2019 | `delai_paiement` | délai couru à compter de la réception de la demande de paiement |
| public | CCP, R2112-9 à R2112-14 | depuis le 01/04/2019 (R2112-13 modifié par le décret 2018-1225) | `forme_prix` | prix ferme, actualisation obligatoire hors fournitures et services courants, prix révisable |
| public | CCP, R2142-1 à R2142-3 | R2142-3 depuis le 01/01/2025 (décret 2024-1251), les autres depuis le 01/04/2019 | `chiffre_affaires_exige`, `assurance_exigee`, `certifications_exigees`, `references_exigees` | conditions de participation annoncées dans l'avis ou les documents ; niveaux liés et proportionnés ; capacités d'autres opérateurs |
| public | CCP, R2142-6, R2142-7, R2142-12 | R2142-7 depuis le 01/01/2026 (décret 2025-1383 : plafond d'une fois et demie le montant estimé), les autres depuis le 01/04/2019 | `chiffre_affaires_exige`, `assurance_exigee` | chiffre d'affaires minimal et son plafond ; assurance des risques professionnels |
| public | CCP, R2142-13, R2142-14 | depuis le 01/04/2019 | `references_exigees` | références exigibles ; leur absence pour des marchés de même nature n'élimine pas à elle seule |
| public | CCP, R2143-5, R2143-12 | depuis le 01/04/2019 | `certifications_exigees`, `chiffre_affaires_exige` | documents équivalents acceptés ; preuve des capacités d'un tiers |
| public | CCP, R2152-7 | depuis le 21/08/2026 (décret 2022-767) | `poids_critere_prix` | critère unique du coût, ou pluralité de critères dont le prix ou le coût |
| public | CCP, R2152-11, R2152-12 | depuis le 01/04/2019 | `poids_critere_prix` | critères indiqués dans les documents ; pondérés en procédure formalisée, éventuellement en fourchette |
| public | CCP, R2161-2, R2161-3 | depuis le 01/04/2019 | `delai_remise` | trente-cinq jours en appel d'offres ouvert, réductions à trente ou quinze jours |
| public | CCAG-PI (arrêté du 30/03/2021, NOR ECOM2106874A), art. 1, 9, 10, 14, 35 | art. 10 et 14 depuis le 08/10/2021 (arrêté du 30/09/2021), les autres depuis le 01/04/2021 | `ccag_reference`, `penalites_retard_execution`, `forme_prix`, `assurance_exigee`, `propriete_intellectuelle` | application et dérogations ; assurance ; prix réputés fermes ; pénalités plafonnées à 10 % ; cession limitée aux finalités |
| privé | C. com., L441-10 (versions au corpus, ADR 009) | version du 01/01/2027 au corpus | `delai_paiement` | soixante jours après la facture, quarante-cinq jours fin de mois |
| privé | C. civ., 1102 | depuis le 01/10/2016 | `chiffre_affaires_exige`, `assurance_exigee`, `certifications_exigees`, `references_exigees`, `delai_remise`, `poids_critere_prix` | liberté de choisir son cocontractant et le contenu du contrat |
| les deux | C. civ., 1163, 1166 | depuis le 01/10/2016 | `normes_secteur` | prestation déterminée ou déterminable ; à défaut, qualité conforme aux attentes légitimes |
| les deux | C. civ., 1170, 1171, 1231-5 (au corpus, rattachements élargis) | inchangées | `responsabilite_titulaire`, `penalites_retard_execution` | obligation essentielle, déséquilibre significatif, clause pénale |
| les deux | CPI, L111-1, L122-4, L131-3 (spec sous-traitance, vérifiés le 16/10/2026) | voir cette spec | `propriete_intellectuelle` | droits de l'auteur ; cession expresse et délimitée |
| les deux | Règlement (UE) n° 1302/2014, annexe, point 4.2.10.2.1 (spec sous-traitance, vérifié le 16/10/2026) | consolidé au 27/04/2025 | `normes_secteur` (ferroviaire) | exigences relatives aux matériaux, renvoi à EN 45545-2 |

Part 21 et CS-25 sont cités par leur nom seulement (spec sous-traitance). Le naval n'a aucune source sectorielle libre : la fiche le dit, ses constats s'appuient sur C. civ. 1163 et 1166.

**Fiches** (structure actuelle : « Ce que dit le texte », paraphrases sourcées ; « Comment le projet l'applique ») : conditions de participation (public) ; le CCAG et ses clauses par défaut ; normes du secteur (les deux) ; prix, paiement et critères d'attribution dans le public ; et, avec le profil privé, appels d'offres privés.

**Points ouverts pour la PR du profil public** :

- les articles du CCAG-PI se citent par point (« art. 14.1.2 ») et la source est un arrêté du Journal officiel, pas un code : le format des citations des fiches (`corpus._ONE`) et le manifeste s'étendent, testés comme le reste ;
- licences relues (conditions de Légifrance vérifiées le 26/09/2026, `SOURCES.md`) ;
- Légifrance déclenche une vérification anti-robots sur les requêtes scriptées, non contournée : lecture et copie page par page ;
- R2152-7 a changé le 21/08/2026 : sa version antérieure (du 01/04/2019 au 20/08/2026) entre aussi au corpus si un dossier du jeu est daté d'avant (ADR 009).

**Mesure de la recherche** : un jeu d'évaluation par profil (`recherche-appel-offres-public.yaml`, `recherche-appel-offres-prive.yaml`), établi sans le rattachement déclaré, comme le premier (ADR 006) ; `mesure-recherche --profil appel_offres_public`. `evaluation.demo_queries` et `mesure-recherche` lisent le secteur et la date d'analyse de chaque dossier. Le jeu figé de l'ADR 006 ne change pas.

## Jeu de démonstration

Dossiers fictifs dans `data/contracts/`, avec leurs attendus. Parties fictives, coordonnées dans les plages réservées (`tests/test_donnees_fictives.py`), aucun nom réel d'acheteur, de chantier ni de projet.

Convention des attendus, inchangée : `proposed_decision` (proposition des règles), `final_decision` et, quand le dossier passe en revue, `human` (décision et motif). Un constat à trancher ou une instruction détectée imposent la revue sans changer la proposition (comme `demo-11-piege-injection` : `NO_GO` proposé, revue imposée).

| Dossier | Profil | Secteur | Proposition | Revue et décision finale | Ce qu'il montre |
| --- | --- | --- | --- | --- | --- |
| `aop-01-go-ferroviaire` | public | ferroviaire | `GO` | — | mission d'AMO pour rénover des voitures voyageurs ; CCAG-PI cité, clauses muettes couvertes par lui ; prix actualisables ; EN 45545-2 nommée |
| `aop-02-reserves-aeronautique` | public | aéronautique | `GO_RESERVES` | — | prix muets sous le CCAG (fermes), prix pesant 60 % de la note, assurance exigée au-dessus de la fiche |
| `aop-03-reserves-naval` | public | naval | `GO_RESERVES` | — | quatre références navales exigées pour une à la fiche, chiffre d'affaires et certification au-delà de la fiche : réserves « groupement ou capacités d'un tiers » |
| `aop-04-no-go-penalites` | public | ferroviaire | `NO_GO` | — | pénalités sans plafond par dérogation à l'art. 14.1.2 du CCAG-PI ; paiement à 45 jours |
| `aop-05-revue-ccag` | public | aéronautique | fixée dans le plan (règles) | revue imposée ; décision humaine dans les attendus | CCAG-Travaux cité ; date de remise sans année ; norme non reconnue ; certification hors catalogue |
| `aop-06-piege-injection` | public | naval | fixée dans le plan (règles) | revue imposée ; décision humaine dans les attendus | paragraphe adressé à l'IA dans le règlement, reconnu par les motifs existants (`instruction_patterns`, comme `demo-11`) ; date de remise absente |
| `aop-07-no-go-date-passee` | public | ferroviaire | `NO_GO` | — | date de remise antérieure à la date d'analyse ; normes d'un autre secteur citées |
| `aopr-01-go-cabine` | privé | aéronautique | `GO` | — | cabine d'avion d'affaires, CS-25 nommée |
| `aopr-02-reserves-yacht` | privé | naval | `GO_RESERVES` | — | cession totale des droits, paiement à 75 jours, normes ferroviaires citées pour un yacht |
| `aopr-03-no-go-certification` | privé | aéronautique | `NO_GO` | — | EN 9100 exigée, absente de la fiche |
| `aopr-04-no-go-references` | privé | ferroviaire | `NO_GO` | selon le plan | références insuffisantes, délai de remise trop court |
| `aopr-05-realiste` | privé | naval | `ESCALADE` | décision humaine | rédaction réaliste, conflit entre domaines (écart au-delà de `conflict_gap`, comme `demo-13`) |

**Couverture.** Le catalogue des branches de `tests/test_demo.py` énumère, **pour chaque profil séparément** (le privé peut ne jamais être codé, et les règles sont propres au profil), chaque branche de chaque règle du tableau « Règles », informations et constats à trancher compris ; chacune se déclenche dans au moins un dossier du profil. S'y ajoutent : chaque type et chaque secteur présents dans les dossiers du profil ; pour le public, une dérogation au CCAG lue et une branche `ccag_autre`. Le plan de chaque PR fixe la répartition exacte des clauses et les attendus ; un dossier peut porter à la fois une proposition et une revue imposée, et un dossier de plus peut entrer en revue si la couverture l'exige (le privé compte cinq branches de `delai_remise` pour cinq dossiers).

**Attendus.** Chaque dossier d'appel d'offres porte `sector` (comme `profile`) et sa propre `analysis_date`, obligatoires pour ces profils ; la date d'un dossier l'emporte sur la date globale du fichier. Les tests et la série réelle analysent à cette date. Dans `web --demo`, le résultat est celui de la date saisie : juste, mais différent de l'attendu une fois une date de remise passée ; la documentation de la démonstration indique la date de chaque dossier. Un dossier du jeu analysé sous un autre profil ou un autre secteur lève une erreur explicite (« dossier du jeu du profil appel_offres_public, secteur ferroviaire »), jamais un résultat faussé.

**Série réelle** : `tests/test_llm_jeu.py` paramétré par profil ; sept dossiers publics × cinq essais (35 analyses payantes), plus cinq privés × cinq (25) si le profil privé est codé ; lancée par le propriétaire, résultat et coût consignés à `docs/journal.md`.

## Erreurs

Aucun repli silencieux ; chaque cas est testé.

- Secteur absent, inconnu, ou fourni avec un profil qui n'en prend pas : refus à l'entrée, avant toute analyse (CLI : code de sortie non nul et message ; web : erreur de formulaire ; MCP : erreur de l'outil, sans message d'exception ni valeur reçue).
- Section `appels_offres`, section d'un profil d'appel d'offres, valeurs par défaut du CCAG-PI, extraits d'appui ou termes de dérogation absents du fichier du projet, ou fiche incomplète : `load_config` arrête le programme.
- CCAG autre que le CCAG-PI, norme non reconnue, certification hors catalogue, date de remise illisible, ambiguë ou absente : constat à trancher, revue humaine.
- Nombre ou montant illisible : valeur absente de la citation, nouvelle extraction puis revue humaine.
- Type ou catégorie hors du profil de l'analyse : échec de validation du schéma de sortie, chemin actuel d'un échec de sortie du LLM.
- Extrait d'appui d'une valeur du CCAG absent du texte ingéré : test en échec.
- Dossier du jeu sous le mauvais profil ou secteur, en démonstration : erreur explicite.

## Données et sécurité

- **Fiche du cabinet** : données d'affaires. Dans la configuration archivée en base, comme tout réglage ; jamais dans les traces ni les journaux (listes blanches inchangées : aucune valeur de configuration n'y entre). Les constats, écrits par le code, citent la valeur exigée et celle de la fiche ; ils restent visibles comme tout constat (dossier, interface, MCP), l'outil étant celui du cabinet.
- **Le dossier est une donnée non fiable**, comme un contrat : délimité dans les prompts, toutes les couches de défense s'appliquent (`domain/instructions.py`, vérification de l'extraction, liste blanche MCP). Une injection ne peut modifier la fiche, puisque la comparaison est faite par le code ; elle peut tenter de cacher une exigence, et l'absence est redemandée dès que le texte l'évoque. Un dossier piège dans le jeu (`aop-06`).
- **Aucun texte du dossier dans un constat écrit par le code** : un identifiant lu dans la citation (norme, certification) n'y est jamais repris, puisque les constats des règles passent hors enveloppe dans les réponses MCP et entrent dans le prompt d'explication (« Constat à trancher »).
- **Secteur et profil** : jamais déduits du texte.

## Critères d'acceptation

Chacun est une vérification observable ; la PR où il devient vérifiable est indiquée entre crochets.

1. Chaque PR : `./scripts/check.sh` se termine par « check.sh : SUCCÈS » ; couverture au moins égale au seuil `fail_under`.
2. [PR 1] `run --profil fournisseur` avec `--secteur`, ou avec un secteur inconnu, sort avec un code non nul sans rien analyser ; [PR 2] de même `run --profil appel_offres_public` sans `--secteur`. Même refus au formulaire et à l'outil MCP (`tests/test_parite.py`).
3. [PR 1] Les profils existants rendent les mêmes clauses, constats et décisions qu'avant le chantier (`tests/test_demo.py`) ; `recherche.yaml` inchangé ; `mesure-recherche --profil fournisseur` rend les mêmes résultats qu'avant la migration.
4. [PR 1] Le vrai journal du poste se vérifie et se rejoue à l'identique (`pytest -m journal_reel --journal-reel`) ; un enregistrement v3 de référence, scellé par le code d'avant ce chantier (à ne jamais régénérer, comme celui du v2), est recalculé et vérifié par le code v4, puis rejoué en réévaluation sans différence.
5. [PR 1] Un constat à trancher impose la revue humaine, la proposition restant celle des règles ; il ne change ni le score ni le statut de recherche du domaine (tests de `decide` et de la justification).
6. [PR 1] Un enregistrement v4 scellé avec un secteur et un constat à trancher (doublures) se vérifie et se rejoue fidèlement, secteur, date et constat compris.
7. [PR 2] Lecture des dates, des nombres en lettres et des identifiants : cas lisibles, illisibles et faux positifs testés ; reconnaissance des articles `R`, `D` et des points du CCAG par le contrôle de l'explication.
8. [PR 2] Les sept dossiers publics rendent leurs attendus (proposition, revue, décision finale) à leur date d'analyse, sous doublures et dans `web --demo` ; la couverture du « Jeu de démonstration » est atteinte.
9. [PR 2] Chaque extrait d'appui des valeurs par défaut du CCAG-PI figure mot pour mot dans l'article ingéré.
10. [PR 2] Chaque constat bloquant ou pénalisant des dossiers publics a au moins une référence en vigueur parmi les articles rattachés à sa clause **et à son profil** ; une analyse publique ne retient jamais un article réservé au privé, et inversement.
11. [PR 2] Un enregistrement v4 scellé sous `appel_offres_public` se vérifie (`verify`) et se rejoue fidèlement (`replay`, code 0).
12. [PR 3, si codé] Critères 8, 10 et 11 pour les cinq dossiers privés.
13. [PR 4] Série réelle : aucune décision automatique plus favorable que l'attendu ; résultat et coût consignés au journal.
14. Chaque PR : `pyproject.toml` et `uv.lock` inchangés ; `tests/test_donnees_fictives.py` passe sur les nouveaux dossiers.

## Découpage

Une branche par PR, chacune verte avec `check.sh` avant d'être poussée par le propriétaire. Condition d'entrée en tête de ce document.

1. **Socle commun.** Entrée `secteur` aux trois portes, état, `AnalystInput`, traces, parité ; contexte des règles (signature, nœud `analyst`, rejeu) ; constats à trancher (`Assessment`, clé d'état, `decide`) ; format v4 du journal (secteur et constats à trancher dans la partie décision), relecture figée des v3 (`DecisionRecordV3`, `AuditRecordV3`) et enregistrement v3 de référence ; rattachement du corpus par profil, article par article (migration, manifeste, fiches, `SOURCES.md`, port, recherche) ; ADR 011 accepté ; CLAUDE.md et documentation (format v4, secteur, rattachement par profil). Aucun profil ajouté et aucune configuration changée : à son issue, tout secteur est refusé, puisqu'aucun profil n'en prend.
2. **Profil `appel_offres_public`.** Ses types, sa partition, ses unités et catégories ; ses règles ; la section `appels_offres` (fiche et listes) et sa section de profil, dont les valeurs par défaut du CCAG-PI, leurs extraits d'appui et les termes de dérogation ; la lecture des dates, des nombres en lettres et des identifiants ; ses prompts d'extraction et d'explication ; ses tables du CRAG ; son schéma d'extraction ; ses termes d'absence et de catégorie ; le contrôle de l'explication étendu (articles `R` et `D`, points du CCAG, libellés de décision du profil). Corpus : CCP, CCAG-PI, C. civ. 1163 et 1166, fiches, rattachements. **Ses sept dossiers**, leur mode démonstration, son jeu d'évaluation de la recherche. Les règles arrivent avec leurs dossiers (CLAUDE.md : une règle ajoutée va avec un contrat), contrairement au découpage de la sous-traitance.
3. **Profil `appel_offres_prive`**, sous condition : les dossiers privés du cabinet sont en français (question 1). De même, avec ses cinq dossiers, C. civ. 1102 et la fiche « appels d'offres privés ». Sinon, cette PR reste dans la spec, suspendue, et la décision est consignée au journal.
4. **Série réelle.** Filtre par profil de `tests/test_llm_jeu.py` ; série lancée par le propriétaire.

## Dépendances vérifiées

- **Aucune nouvelle bibliothèque** : `pyproject.toml` et `uv.lock` inchangés.
- **Sources** : lues sur Légifrance le 22/10/2026 dans un navigateur, page par page (tableau du corpus) ; Code de la commande publique (`LEGITEXT000037701019`), CCAG-PI (`JORFTEXT000043310613`, version consolidée, données mises à jour le 01/07/2024), Code civil (`LEGITEXT000006070721`). L441-10 : fichiers du corpus (`L441-10_2027-01-01.txt`, récupéré le 16/10/2026). CPI et règlement (UE) n° 1302/2014 : vérifiés le 16/10/2026 par la spec sous-traitance.
- **API des avis** (non-objectif, consultées le 21/10/2026) : API BOAMP (data.gouv.fr, DILA ; jeu `boamp` sur `boamp-datadila.opendatasoft.com`, champs dont `datelimitereponse` et `donnees`), API de recherche TED (`docs.ted.europa.eu`, sans authentification).
- **Code confronté le 22/10/2026**, puis par une relecture indépendante : `domain/profiles.py` ; `domain/models.py` (`Domain`, `Clause`, `PaymentBasis`) ; `domain/decision.py` (`decide`, `input_findings`) ; `domain/audit.py` (`_DecisionPart`, partie décision commune aux formats, `analysis_date` scellée, `replay_sense`, `_rejustified`) ; `domain/rules/_common.py` (`Effect`, `RuleFinding.requires_reference`, `RuleFn`) ; `domain/rules/financier.py` (`_payment_delay`) ; `domain/justification.py` (`justify`) ; `domain/explanation.py` (`_NUMBER`) ; `adapters/postgres/rag_store.py` (filtre de la recherche) ; `data/corpus/manifest.yaml` ; `data/contracts/attendus.yaml` ; `tests/test_demo.py`.

## Amendements imposés par les textes

Validés par le propriétaire le 22/10/2026, après la séance de conception :

- Public, références insuffisantes : pénalité, et non blocage (R2142-14).
- Public, chiffre d'affaires insuffisant : pénalité, et non blocage (R2142-3, R2143-12).
- Public, certification non détenue : pénalité, et non blocage (R2143-5, R2142-3).
- Public, délai de paiement : seuil de 30 jours, le constat mentionnant les 50 et 60 jours de R2192-11.
- Valeurs par défaut du CCAG-PI telles que lues (art. 9, 10.1.1, 14.1.2, 35.2.1).

## Écarts avec le design validé en séance

Introduits à l'écriture de cette spec et à sa relecture indépendante, **à valider par le propriétaire** à sa relecture ; la validation est consignée ici avant tout plan.

À l'écriture :

1. `propriete_intellectuelle` absente : information, et non pénalité. Le candidat est l'auteur, et une cession de droits est expresse (CPI L131-3) : sans clause, il garde ses droits.
2. `penalites_retard` devient `penalites_retard_execution`, nom du profil sous-traitance, pour la raison qu'il donne (L441-10, II).
3. `ccag_reference` : catégories `ccag_pi` et `ccag_autre` ; absente, aucun CCAG n'est cité.
4. Dérogation au CCAG lue par des termes réglés (`derogation_terms`) : sans eux, une clause sans plafond sous le CCAG-PI prendrait à tort son plafond.
5. `delai_paiement` public : point de départ `reception_demande` (R2192-12) ; un délai « fin de mois » est pénalisé.
6. `delai_remise` absente : constat à trancher.
7. Jeu public de sept dossiers au lieu de six : le profil privé pouvant ne jamais être codé, le public couvre seul ses branches (date passée comprise) ; `aop-03` passe de `NO_GO` à `GO_RESERVES` après les amendements.
8. `poids_critere_prix` ajouté (treize types en public, douze en privé), retenu par le propriétaire le 22/10/2026 ; catégorie `critere_unique` et borne haute d'une fourchette ; sources R2152-7, R2152-11, R2152-12 (public) et C. civ. 1102 (privé).

À la relecture indépendante :

9. **Constats à trancher hors des verdicts** : scellés au niveau de la partie décision du v4, et non dans `AgentVerdict`, dont la modification casserait la relecture des v1 à v3.
10. **Signature des règles** : un contexte (profil, secteur, date d'analyse, CCAG) passé par l'analyste et par le rejeu.
11. Effets explicites : « rien » sous le CCAG devient une **information** ; un type d'opportunité présent mais non chiffré, une information ; un délai de paiement public non chiffré, une pénalité par prudence (règle commune) ; sous `ccag_autre`, les autres règles jugent sans CCAG.
12. Aucun rattachement déterministe de l'article du CCAG : ses références viennent du CRAG, comme pour tout constat ; les valeurs par défaut portent un extrait d'appui, vérifié mot pour mot.
13. **Rattachement par article**, et non par source ; fiche scindée quand son contenu diffère d'un régime à l'autre.
14. Un constat écrit par le code ne reprend jamais un identifiant lu dans le dossier.
15. Contrôle de l'explication étendu (articles `R`, `D`, points du CCAG) dans la PR 2.
16. Découpage : la fiche, les listes et les lectures passent de la PR 1 à la PR 2, avec leurs consommateurs ; la PR 1 ne change aucune configuration.
17. Jeu : propositions et revues explicites, `aop-05` et `aop-06` en revue imposée (et non `ESCALADE`) ; attendus `sector` et `analysis_date` par dossier.
18. Critère 4 : un v3 se rejoue en réévaluation, sans différence (`replay_sense` : un autre commit n'est jamais un rejeu fidèle).

## Écarts avec l'existant

Consignés dans l'ADR 011 :

- **Point de vue inversé** : ces profils défendent le candidat, les autres celui qui achète ou fait exécuter.
- **Norme et secteur** : la spec sous-traitance excluait de vérifier « que la norme citée est la bonne pour le secteur » ; ces profils le font, par une liste réglée, sans jamais juger du contenu d'une norme.
- **Entrée `secteur`** obligatoire pour ces profils, aux trois portes, et **format v4 du journal** (secteur et constats à trancher dans la partie décision), avec relecture figée des v3. Un v4 n'est lisible que par le code de cette version et des suivantes (même règle que le v3, ADR 010).
- **Constats à trancher** : une règle peut imposer la revue humaine.
- **Signature des règles** : elles reçoivent un contexte.
- **Rattachement du corpus par profil**, article par article : schéma de `rag_chunks` (migration), manifeste, fiches, port de la recherche.
- **Empreinte commune** : un réglage de ces profils ou de la fiche du cabinet modifié change l'empreinte de toute la configuration ; les analyses interrompues de tous les profils escaladent. Conséquence acceptée, comme dans l'ADR 010 ; à noter : la fiche change au moins une fois par an (chiffre d'affaires).
- **Libellés de décision par profil**, à l'affichage et dans le contrôle de l'explication ; la décision scellée ne change pas. Risque noté : « répondre » est un verbe courant, que le contrôle de l'explication pourrait prendre à tort pour un libellé de décision (refus, puis gabarit) ; mesuré par la série réelle.
- **Cible élargie** : le cabinet candidat à des marchés publics et privés.
- **Périmètre figé depuis la v1.1** : rouvert le 21/10/2026 à la demande explicite du propriétaire, pour ce chantier.

## Questions pour le cabinet

À poser lors de la démonstration de la sous-traitance, **avant tout code de ce chantier** ; chaque réponse devient un réglage, une révision de cette spec ou un chantier, jamais une supposition :

1. Dans quelle langue sont rédigés vos dossiers privés ? (condition du profil privé : l'anglais reste rejeté)
2. À quel CCAG se réfèrent les marchés publics de vos missions ? (CCAG-PI supposé)
3. Qu'est-ce qui doit bloquer pour vous : pénalités non plafonnées, date dépassée ; et, dans le privé, références, chiffre d'affaires, certification manquants ?
4. Quelles normes attendez-vous pour chaque secteur, et quelles certifications détenez-vous ?
5. Quel délai minimal vous faut-il pour préparer une réponse, et au-delà de quel poids du prix renoncez-vous ?
6. Qui sont vos acheteurs publics : pouvoirs adjudicateurs, entreprises publiques (soixante jours de délai de paiement légal), établissements de santé (cinquante) ?
7. Combien de consultations recevez-vous par mois, et comment décidez-vous aujourd'hui d'y répondre ?
8. Les vraies valeurs de la fiche (chiffre d'affaires, assurance, références par secteur) : dans votre instance seulement, jamais dans le dépôt.
