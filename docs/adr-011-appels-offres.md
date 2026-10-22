# ADR-011 : profils appel d'offres, secteur et fiche du candidat

- **Statut** : proposé le 22/10/2026, conception validée section par section par le propriétaire, spec validée le même jour ; accepté à la PR 1 du chantier. Numéro à confirmer à la fusion si un autre chantier prend le 011 avant.
- **Portée** : deux profils qui défendent le candidat à une consultation ; une entrée secteur, scellée ; une fiche du cabinet dans la configuration ; des constats qui imposent la revue humaine ; le rattachement du corpus par profil. Spec : [`spec-appels-offres.md`](spec-appels-offres.md).

## Contexte

Les profils existants (ADR 010) défendent celui qui achète ou fait exécuter : l'acheteur, le donneur d'ordre. Le cabinet d'assistance à maîtrise d'ouvrage qui a motivé le profil sous-traitance répond aussi, pour ses propres missions, à des consultations publiques et privées dans l'aménagement de transports de luxe (ferroviaire, aéronautique, naval). Il veut un verdict « répondre ou ne pas répondre » qui tienne compte des risques du futur contrat, des normes du secteur et de l'opportunité : délai de remise, capacités et références exigées, poids du prix.

Trois faits pèsent sur la conception : les quatre domaines sont figés dans le code (`Domain`, et `decide` exige les quatre verdicts) ; le public et le privé partagent leurs types de clauses mais pas leurs textes, alors que la recherche ne filtre que par domaine et par type ; l'opportunité n'a de sens que rapportée à ce que le cabinet est capable de prouver.

## Options

**Faire entrer l'opportunité dans le verdict :**

- **A. Deux profils sur le moteur actuel.** Chaque critère, risque ou opportunité, est un type de clause rangé dans l'un des quatre domaines ; les règles comparent le dossier à une fiche du cabinet.
- **B. Un cinquième domaine « opportunité ».** Séparation plus lisible ; touche tous les profils, le fan-out (ADR 001), les poids, la vérification de la décision et le rejeu des anciens enregistrements.
- **C. L'opportunité à côté du verdict.** Bilan calculé et affiché sans effet sur la décision : un délai de remise dépassé n'empêcherait pas un `GO`.

**Connaître le secteur :**

- **Déclaré** à chaque analyse, comme le profil, et scellé.
- **Extrait** par le LLM avec une citation vérifiée par le code.

## Décision

**Option A, secteur déclaré.**

- B est écartée : son coût porte sur tous les profils pour une séparation surtout visuelle. C est écartée : elle ne répond pas au besoin, l'opportunité devant peser sur la décision.
- Le secteur extrait est écarté : un dossier ambigu ou piégé orienterait la vérification des normes vers le mauvais secteur. L'utilisateur sait si l'objet est un train ; comme le profil, le secteur n'est jamais déduit du texte.

Conséquences de la décision, retenues :

- **Point de vue inversé** pour ces deux profils : une clause qui protège l'acheteur devient un risque.
- **Secteur obligatoire** pour les profils qui le déclarent, refusé pour les autres, aux trois portes ; dans l'état avant tout nœud, dans `AnalystInput` et dans la partie décision de l'enregistrement.
- **Constats à trancher** : une règle peut imposer la revue humaine (CCAG non pris en charge, norme non reconnue, certification hors catalogue, date illisible ou absente), la proposition restant celle des règles, sur le modèle des constats du contrat (`input_findings`). Ils ne pèsent ni sur le score ni sur le blocage, et n'exigent aucune référence. Ils ne vont pas dans `AgentVerdict` : la partie décision et ses verdicts sont communs aux formats v1 à v3, et un champ ajouté, même avec une valeur par défaut, changerait leur relecture et leur rejeu.
- **Contexte des règles** : la signature des règles change, pour leur passer le profil, le secteur, la date d'analyse et le CCAG cité ; le rejeu les reprend de l'enregistrement scellé.
- **Format v4** du journal : secteur et constats à trancher au niveau de la partie décision du v4, comme le profil au v3, donc dans `decision_hash` ; les v3 relus par des modèles figés, comme les v2 et v1.
- **Fiche du cabinet** dans `config/decision.yaml` (section `candidat`) : archivée et couverte par l'empreinte comme tout ce qui pèse sur une décision, donc rejouable sans mécanisme nouveau. Une fiche dans un fichier à part, avec sa propre empreinte et sa propre archive, a été écartée : plus de code pour éviter des escalades rares.
- **Rattachement du corpus par profil, article par article** : une source du manifeste est un code entier, dont les articles servent des profils différents (C. civ. 1102 pour le privé, 1163 pour les deux) ; chaque article déclare ses profils comme ses types, et la recherche filtre par domaine, type et profil (migration suivante libre). Les articles existants déclarent les profils qu'ils servent déjà : leur recherche ne change pas.
- **Valeurs par défaut du CCAG-PI** dans la configuration, chacune avec un extrait d'appui vérifié par un test contre le texte ingéré ; une dérogation écrite dans le CCAP l'emporte. Les références d'un constat viennent toujours du CRAG.
- **Quatre domaines inchangés**, donc le graphe aussi.

## Conséquences

- La spec sous-traitance excluait de vérifier qu'une norme citée est la bonne pour le secteur ; ces profils le font, par une liste de normes réglée par secteur, sans jamais juger du contenu d'une norme ni de la conformité d'une pièce.
- **Format v4 sans retour en deçà**, comme le v3 (ADR 010) : un v4 n'est lisible que par le code de cette version et des suivantes ; aucune analyse réelle ni rien qui scelle depuis la branche de la PR 1 sur la base du poste avant sa fusion.
- **Un nom de secteur ne se renomme ni ne se retire**, comme un nom de profil.
- **Empreinte commune** : modifier la fiche du cabinet (au moins chaque année, pour le chiffre d'affaires) ou un réglage de ces profils fait escalader les analyses interrompues de tous les profils, et signale « configuration modifiée » aux revues en attente. Accepté, comme dans l'ADR 010.
- Les règles du profil public suivent le Code de la commande publique : une insuffisance de références, de chiffre d'affaires ou de certification n'y bloque pas (R2142-3, R2142-14, R2143-5), elle devient une réserve.
- Aucun code avant la fin du chantier sous-traitance et les réponses du cabinet ; le profil privé n'est codé que si ses dossiers privés sont en français.
- Le périmètre figé depuis la v1.1 est rouvert le 21/10/2026, à la demande explicite du propriétaire, pour ce chantier.
- Aucune dépendance ajoutée.
