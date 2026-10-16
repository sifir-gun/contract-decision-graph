# ADR-009 : profils de contrat

- **Statut** : proposé, le 16/10/2026 (branche `profils-sous-traitance`, conception validée section par section par le propriétaire) ; accepté à la fusion de la PR 1 du chantier.
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

- B est écartée : un contrat de prestation de nettoyage serait pénalisé pour une obligation de certification absente, et l'empreinte de toute nouvelle décision changerait pour des clauses sans rapport avec le contrat.
- C est écartée : moins de travail tout de suite, mais une instance de plus par famille, dès les contrats d'architectes ; le profil d'une décision ne serait lisible que dans l'empreinte de la configuration.

Conséquences de A, retenues :

- **Profil obligatoire**, sans valeur par défaut, à chaque porte (CLI, interface web, serveur MCP) : une analyse sous le mauvais profil ne chercherait pas les bonnes clauses, sans le signaler.
- **Profil dans l'état**, avant tout nœud, et dans la partie décision de l'enregistrement d'audit : **format v3**. Les v1 et v2 restent vérifiables et sont relus comme profil `fournisseur`, le seul qui existait.
- **Configuration** : les sections actuelles restent le profil `fournisseur`, pour que les configurations archivées se relisent sans changement ; chaque nouveau profil a sa section, absente par défaut dans le modèle, exigée dans le fichier du projet (règle de l'ADR 006).
- **Quatre domaines inchangés**, donc le graphe et le fan-out aussi : seul le contenu de chaque domaine dépend du profil. Un type commun à deux profils reste dans le même domaine.

## Conséquences

- Tout appel existant à `run` doit passer `--profil fournisseur` : scripts, scénarios du cluster, documentation.
- La cible de `spec-phase1.md` (directions achats et juridiques de PME et d'ETI) s'élargit au donneur d'ordre d'un contrat de sous-traitance.
- Le périmètre figé depuis la v1.1 est rouvert le 16/10/2026, à la demande explicite du propriétaire, pour ce seul chantier.
- Aucune dépendance ajoutée.
