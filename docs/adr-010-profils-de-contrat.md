# ADR-010 : profils de contrat

- **Statut** : accepté, PR 1 du chantier (branche `profils-sous-traitance`) ; proposé le 16/10/2026, conception validée section par section par le propriétaire. Amendé le 21/10/2026, PR 2 (ci-dessous).
- **Portée** : la liste des types de clauses, leur partition entre les domaines, les règles, les réglages et le prompt d'extraction, aujourd'hui uniques ; le choix d'un profil à chaque analyse, son scellement et son rejeu. Spec : [`spec-profils-sous-traitance.md`](spec-profils-sous-traitance.md).

## Contexte

Le système n'analyse qu'un genre de contrat : le contrat fournisseur d'une PME ou d'une ETI. Ses dix types de clauses (`REQUIRED_KINDS`), leur partition entre les quatre domaines (`DOMAIN_KINDS`), les unités des valeurs (`VALUE_UNITS`) et les libellés des domaines pour le CRAG (`DOMAIN_LABELS`) sont des constantes du code ; les règles sont du Python, une fonction par domaine, avec leurs seuils dans `config/decision.yaml`.

Un cabinet d'assistance à maîtrise d'ouvrage, qui coordonne l'aménagement intérieur de transports de luxe, gère de nombreux contrats avec des corps de métier, des artisans d'art et des industriels, ainsi qu'avec des architectes et des assureurs. Ses contrats de sous-traitance appellent d'autres clauses : délais d'exécution, protection des dessins de l'architecte, obligation de certification, assurance du sous-traitant. Les contrats d'architectes et les polices d'assurance suivront.

## Options

- **A. Profils de contrat.** Un profil par famille de contrats, choisi à chaque analyse, scellé dans le journal ; le profil `fournisseur` reprend l'existant à l'identique.
- **B. Liste unique élargie.** Les nouveaux types s'ajoutent aux dix actuels, pour tous les contrats.
- **C. Une instance par famille.** La liste des types devient un réglage de toute l'instance.

## Décision

**Option A.**

- B est écartée : un contrat de prestation de nettoyage serait pénalisé pour une obligation de certification absente, et chaque contrat devrait rendre des clauses sans rapport avec lui (extraction plus longue, plus d'occasions d'erreur du modèle).
- C est écartée : moins de travail tout de suite, mais une instance de plus par famille, dès les contrats d'architectes ; le profil d'une décision ne serait lisible que dans l'empreinte de la configuration.

Conséquences de A, retenues :

- **Profil obligatoire**, sans valeur par défaut, à chaque porte (CLI, interface web, serveur MCP) : une analyse sous le mauvais profil ne chercherait pas les bonnes clauses, sans le signaler.
- **Profil dans l'état**, avant tout nœud, et dans la partie décision de l'enregistrement d'audit : **format v3**. Les v1 et v2 restent vérifiables et sont relus comme profil `fournisseur`, le seul qui existait.
- **Configuration** : les sections actuelles restent le profil `fournisseur`, pour que les configurations archivées se relisent sans changement ; chaque nouveau profil a sa section, absente par défaut dans le modèle, exigée dans le fichier du projet (règle de l'ADR 006).
- **Quatre domaines inchangés**, donc le graphe et le fan-out aussi : seul le contenu de chaque domaine dépend du profil. Un type commun à deux profils reste dans le même domaine.
- **Tables et schéma par profil** : sujets et catégories du CRAG, prompts d'extraction et d'explication, schéma de sortie de l'extraction (un type hors du profil échoue à la validation du schéma).

## Conséquences

- Tout appel existant à `run` et à `mesure-recherche` doit passer `--profil fournisseur` : scripts, scénarios du cluster, documentation.
- La démonstration au cabinet ne traite que des contrats fictifs : un vrai contrat n'entre ni dans le dépôt, ni dans le journal du poste du propriétaire (« Aucun contrat réel », CLAUDE.md, inchangé). Ses vrais contrats relèveront de sa propre instance, chantier à part.
- La cible de `spec-phase1.md` (directions achats et juridiques de PME et d'ETI) s'élargit au donneur d'ordre d'un contrat de sous-traitance.
- Le périmètre figé depuis la v1.1 est rouvert le 16/10/2026, à la demande explicite du propriétaire, pour ce seul chantier.
- **Empreinte commune** : l'empreinte couvre toute la configuration. Modifier un réglage du profil `sous_traitance` fait escalader les analyses fournisseur interrompues, et signale « configuration modifiée » aux revues fournisseur en attente. Accepté : une empreinte par profil compliquerait l'archive et le rejeu pour un cas rare.
- **Format v3 sans retour en deçà.** Un enregistrement v3 n'est lisible que par le code de cette version et des suivantes. Une version antérieure (`main` d'avant la fusion, un autre worktree qui partage la base du poste, un ancien pod pendant une mise à jour progressive, un retour arrière en deçà de cette version) signale une chaîne rompue : `verify` rend « version d'enregistrement inconnue : 3 » (`ChaineRompue`, code 1), une fausse alerte d'altération, et le rejeu d'un v3 y est impossible (vérifié en relecture finale avec le code de 5e5a8f6, sur une chaîne v2 puis v3). Le journal étant en ajout seul, un v3 scellé ne s'efface pas. Règle : aucune analyse réelle (CLI `run`, interface réelle, serveur MCP réel), ni rien qui scelle (`resume`, `relaunch`, `expire`), depuis la branche de la PR 1 sur la base du poste avant sa fusion ; la démonstration et les tests n'écrivent pas dans le vrai journal. La forme v2 que relit le code courant est prouvée par un enregistrement de référence scellé par le code d'avant les profils (`tests/fixtures/journal-v2-reference.json`).
- **Un nom de profil ne se renomme ni ne se retire** : il est scellé dans les enregistrements v3 et dans les checkpoints, et `profiles.of_state`, `audit.record_profile` et les listes des contrats refuseraient un nom disparu, comme un profil inconnu. Un profil s'ajoute seulement.
- Aucune dépendance ajoutée.

## Amendement du 21/10/2026 : profil sous-traitance (PR 2)

Amendement de la spec du 20/10/2026, validé section par section par le propriétaire ; il met en œuvre ce que la version du 16/10 laissait à la PR 2.

- **Deux profils.** `Profile` vaut `fournisseur` ou `sous_traitance`. Le second a neuf types de clauses, dans les quatre domaines existants ; `revision_prix` et `delai_paiement`, communs aux deux, y gardent leur domaine (`profiles.KIND_DOMAIN` le vérifie). Ses règles (`rules/sous_traitance.py`, et les règles communes de `rules/_common.py`, qui prennent les noms des parties du profil), sa section `sous_traitance` de `config/decision.yaml`, ses prompts d'extraction et d'explication, son schéma d'extraction (`ExtractionOutputSousTraitance`) et ses tables du CRAG lui sont propres. Son corpus aussi, sauf pour les deux types communs, justifiés par les mêmes sources que pour le fournisseur (C. com., art. L441-10 ; C. mon. fin., art. L112-2 ; fiches « Délais de paiement entre professionnels » et « Révision et indexation des prix ») : le rattachement se fait par clause. Le profil `fournisseur` ne change pas : mêmes clauses, constats et décisions sur ses 13 contrats, `data/evaluation/recherche.yaml` inchangé.
- **Port d'explication.** `Explainer` reçoit le profil de l'analyse, en argument nommé, comme `Extractor` et `Crag` ; le nœud `explain` le lit par `profiles.of_state`. `LLMExplainer` lit le prompt de chaque profil à sa construction (`PROMPT_FILES`), à la construction des dépendances de l'analyse : avec un fichier manquant, la première analyse échoue, explicitement. Le schéma `Draft` et les contrôles de `domain/explanation.py` restent communs ; le gabarit (`TemplateOnly`) ne change pas. Écarté : un champ `profile` dans `ExplanationRequest`, qui ferait passer le profil par une autre voie que pour les deux autres ports.
- **Contrôle de l'explication.** Une citation que touche un nom de source du corpus (citation ou titre du manifeste, lus par l'application et passés au domaine) est comparée par source et numéro ; sans nom, par son numéro seul, comme avant. Dans une énumération, seul le numéro que le nom touche lui est rattaché : le premier pour un nom avant, le dernier pour un nom après, même avec un nom avant ; les autres sont comparés par leur seul numéro. Un nom pris après une citation ne sert pas de nom avant à la suivante ; une citation nommée avant elle consomme aussi le nom qui la suit, sauf s'il ouvre la citation suivante. Quand une citation ou une énumération a un nom avant, le nom qui la suit reste à la citation suivante s'il en ouvre la référence : aucune préposition avant lui, et entre lui et elle des séparateurs seuls, « l'article » ou « l'art. » admis ; sans nom avant, le nom qui suit une énumération va toujours à son dernier numéro. Limites (docstring de `explanation.citations`) : un numéro d'une énumération qui viserait l'homonyme d'un autre code passe, comme une citation sans nom, y compris dans le texte du constat ; après une citation seule, « l'article » n'ouvre pas la suivante. Le CPI et le C. mon. fin. ont chacun un article L112-2 : le test qui interdisait tout numéro commun à deux sources est remplacé par un test sur chaque numéro partagé.
- **Traces de la revue et de la reprise.** Elles portent `cdg.profil`, lu dans le statut final du moteur (`thread_status`), qui ne leur est connu qu'à la fin. Limites : l'expiration n'en porte aucun, une seule trace couvrant plusieurs contrats de profils peut-être différents ; une revue refusée avant le moteur (quatre yeux, `FourEyesRefused`), ou en erreur du moteur (`ThreadError` : contrat inconnu, qui n'attend pas de revue, ou analysé sous une autre configuration), n'a pas de statut final, donc pas de profil : sa trace porte le type de l'erreur.
- **Refus en démonstration.** Le choix du profil reste obligatoire en démonstration, alors que chaque contrat du jeu déclare le sien. Un contrat du jeu choisi sous un autre profil y est refusé à la saisie commune (`saisie.from_demo_set`) : 400 au formulaire, erreur de l'outil MCP sans message d'exception, message qui nomme le profil du contrat. L'extraction simulée le refuse encore, en défense. En mode réel, rien ne change : analyser un contrat du jeu sous un autre profil reste permis.
- **Conséquence prévue, constatée.** La section `sous_traitance` change l'empreinte de toute la configuration (« Empreinte commune ») : à la mise à jour, les analyses fournisseur interrompues escaladent, et les revues en attente sous l'ancienne configuration ne se reprennent plus. Procédure : [exploitation](exploitation.md), « Mise à jour vers le profil sous-traitance ».
