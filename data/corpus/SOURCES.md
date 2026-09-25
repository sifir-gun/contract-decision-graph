# Sources du corpus RAG

Textes publics uniquement, plus des fiches rédigées pour le projet. Chaque source porte un identifiant (`source_id`), repris dans la table `rag_chunks` et cité par les fiches. **Aucune affirmation juridique d'une fiche sans source listée ici.**

## Règle de périmètre

Un article est admis s'il **sert une règle** (`src/cdg/domain/rules/`), ou s'il est **cité directement** par un article qui sert une règle, ou s'il **définit un terme utilisé par une règle**. Les renvois de second degré ne sont pas suivis. Les renvois non récupérés sont listés dans le README (« Limites connues »).

## Récupération

- Manuelle, depuis un navigateur, le **24/09/2026**. EUR-Lex et Légifrance bloquent les requêtes automatiques par une vérification anti-robots, que l'agent n'a pas contournée.
- Un fichier `.txt` UTF-8 par article, texte copié tel qu'affiché. Le nettoyage (métadonnées de version, notes, lignes d'interface) se fait à l'ingestion, par des règles testées (`src/cdg/domain/corpus.py`).

Licences, indiquées d'après les conditions publiées par chaque site, non vérifiées par l'agent (sites inaccessibles aux requêtes automatiques) :
- **EUR-Lex** : réutilisation autorisée avec mention de la source (« © Union européenne, https://eur-lex.europa.eu »), selon la Décision 2011/833/UE ;
- **Légifrance** : données publiées sous Licence Ouverte / Open Licence (Etalab 2.0).

## Textes publics

| source_id | Article | Fichier (`data/corpus/raw/`) | Périmètre | Récupéré le |
| --- | --- | --- | --- | --- |
| `rgpd` | art. 4 | `rgpd/art-4.txt` | Définit des termes utilisés par la règle conformité (« données à caractère personnel », « sous-traitant ») | 24/09/2026 |
| `rgpd` | art. 28 | `rgpd/art-28.txt` | Sert la règle conformité : accord de traitement (sous-traitant) | 24/09/2026 |
| `rgpd` | art. 32 | `rgpd/art-32.txt` | Cité par l'art. 28 | 24/09/2026 |
| `rgpd` | art. 33 | `rgpd/art-33.txt` | Cité par l'art. 28 (« articles 32 à 36 ») | 24/09/2026 |
| `rgpd` | art. 36 | `rgpd/art-36.txt` | Cité par l'art. 28 (« articles 32 à 36 ») | 24/09/2026 |
| `rgpd` | art. 40 | `rgpd/art-40.txt` | Cité par les art. 28 et 46 (codes de conduite) | 24/09/2026 |
| `rgpd` | art. 42 | `rgpd/art-42.txt` | Cité par les art. 28 et 46 (certification) | 24/09/2026 |
| `rgpd` | art. 44 | `rgpd/art-44.txt` | Sert la règle transfert hors UE | 24/09/2026 |
| `rgpd` | art. 45 | `rgpd/art-45.txt` | Sert la règle transfert hors UE (décision d'adéquation) | 24/09/2026 |
| `rgpd` | art. 46 | `rgpd/art-46.txt` | Sert la règle transfert hors UE (garanties appropriées) | 24/09/2026 |
| `rgpd` | art. 79 | `rgpd/art-79.txt` | **Hors périmètre, non ingéré** : cité seulement par l'art. 82, lui-même cité par l'art. 28 (second degré) | 24/09/2026 |
| `rgpd` | art. 82 | `rgpd/art-82.txt` | Cité par l'art. 28 | 24/09/2026 |
| `rgpd` | art. 83 | `rgpd/art-83.txt` | Cité par l'art. 28 | 24/09/2026 |
| `rgpd` | art. 84 | `rgpd/art-84.txt` | Cité par l'art. 28 | 24/09/2026 |
| `code-commerce` | L441-10 | `code-commerce/L441-10.txt` | Sert la règle financière : pénalités de retard. **Version en vigueur jusqu'au 01/01/2027** | 24/09/2026 |
| `code-commerce` | L442-1 | `code-commerce/L442-1.txt` | Sert les règles opérationnelle (préavis, rupture brutale) et juridique (déséquilibre significatif) | 24/09/2026 |
| `code-civil` | 1170 | `code-civil/1170.txt` | Sert la règle juridique : clause privant de sa substance l'obligation essentielle | 24/09/2026 |
| `code-civil` | 1171 | `code-civil/1171.txt` | Sert la règle juridique : déséquilibre significatif | 24/09/2026 |
| `code-civil` | 1231-3 | `code-civil/1231-3.txt` | Sert la règle juridique : dommages prévisibles, plafonds de responsabilité | 24/09/2026 |
| `code-civil` | 1210 | `code-civil/1210.txt` | Sert la règle opérationnelle : durée d'engagement (engagements perpétuels) | 24/09/2026 |
| `code-civil` | 1211 | `code-civil/1211.txt` | Sert la règle opérationnelle : préavis de résiliation | 24/09/2026 |
| `code-monetaire-financier` | L112-2 | `code-monetaire-financier/L112-2.txt` | Sert la règle financière : révision de prix (indexation) | 24/09/2026 |

URL officielles :
- `rgpd` : https://eur-lex.europa.eu/legal-content/FR/TXT/?uri=CELEX:32016R0679
- `code-commerce` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000005634379/
- `code-civil` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006070721/
- `code-monetaire-financier` : https://www.legifrance.gouv.fr/codes/texte_lc/LEGITEXT000006072026/

## Fiches de référence

Rédigées pour ce projet, dans `fiches/`. Chaque fiche commence par « Fiche synthétique rédigée pour ce projet, non constitutive d'un avis juridique », et chaque ligne d'affirmation cite l'article qu'elle paraphrase (`source_id`, `art.`). Un test vérifie que chaque citation désigne un article admis ci-dessus.

| source_id | Fiche | Domaine | Articles cités |
| --- | --- | --- | --- |
| `fiche-sous-traitance-rgpd` | `fiches/sous-traitance-rgpd.md` | conformite | RGPD 4, 28, 32, 33, 36, 40, 42, 82, 83, 84 |
| `fiche-transferts-hors-ue` | `fiches/transferts-hors-ue.md` | conformite | RGPD 40, 42, 44, 45, 46 |
| `fiche-responsabilite-plafonds` | `fiches/responsabilite-plafonds.md` | juridique | C. civ. 1170, 1171, 1231-3 ; C. com. L442-1 |
| `fiche-penalites-retard` | `fiches/penalites-retard.md` | financier | C. com. L441-10 |
| `fiche-revision-prix` | `fiches/revision-prix.md` | financier | C. mon. fin. L112-2 |
| `fiche-duree-preavis` | `fiches/duree-preavis.md` | operationnel | C. civ. 1210, 1211 ; C. com. L442-1 |
