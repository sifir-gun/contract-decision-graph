# ADR-001 : fan-out vers quatre analystes, décision déterministe

- **Statut** : accepté. Décidé avant le J1 (spec du 23/09/2026, « Justification multi-agents ») ; rédigé le 26/09/2026 (J5), avec les mesures de la série 8 (`docs/journal.md`).
- **Portée** : découpage de l'analyse d'un contrat en analystes, pattern d'orchestration, place des LLM, règle qui rend `NO_GO`.

## Contexte

Un contrat fournisseur est jugé sur quatre domaines : juridique (plafonds de responsabilité), financier (révision de prix, pénalités d'exécution, délai de paiement), conformité (données personnelles, accord de traitement, transferts hors UE) et opérationnel (durée d'engagement, préavis). Chaque domaine a ses règles, ses seuils et ses sources : le RGPD ne justifie pas un délai de paiement, le Code de commerce ne justifie pas un transfert.

Le verdict doit être déterministe, rejouable et auditable, et un humain doit pouvoir trancher. Les LLM se limitent à trois rôles : extraire les clauses, juger la pertinence des extraits du corpus (CRAG), expliquer un verdict déjà figé.

## Décision

**Fan-out / fan-in avec une décision déterministe, et un vérificateur sur l'extraction en amont.**

```
validate_input → extract_clauses ⇄ verify_extraction → Send ×4 → analyst (juridique, financier, conformité, opérationnel)
                                                                  → decision_gate → [human_review] → explain → audit_seal
```

- **Une extraction, vérifiée par code**, partagée par les quatre analystes : citations mot pour mot, absences évoquées par le texte, valeur et catégorie dans la citation, citations hors des consignes injectées. En cas de problème, nouvelle extraction avec retour ciblé, puis escalade.
- **Quatre analystes lancés en parallèle** par `Send`, depuis l'arête qui suit la vérification ; leurs verdicts sont fusionnés par un réducteur (`verdicts`, et `failures` pour les échecs).
- **Chaque analyste est un outil borné, pas un agent autonome** : il applique les règles de son domaine en Python pur, puis appelle le CRAG seulement pour les clauses qui portent un constat, et la justification déduit son statut (`OK` ou `INSUFFISANT`). Aucune boucle ouverte, aucun choix d'outil par un LLM : le seul LLM d'un analyste est le juge de pertinence du CRAG, et la réécriture de la requête, sur le petit modèle.
- **Le gate (`decision_gate`) est du code** : blocage dur, analyste en échec, budget, `INSUFFISANT`, conflit entre domaines, puis seuils et marge. L'issue la plus conservatrice l'emporte. Le doute (escalade, marge faible, tentative d'instruction détectée) mène à la revue humaine, par `interrupt()`.

## L'argument honnête

**Le découpage ne se justifie pas par la qualité.** Les verdicts des analystes sont des fonctions déterministes des clauses extraites : un agent unique qui appliquerait les mêmes règles, domaine après domaine, rendrait les mêmes verdicts. Aucun domaine ne déborde du contexte d'un modèle, et les analystes ne débattent pas : ils ne répondent pas à la même question. La qualité se joue en amont, dans l'extraction, que les quatre partagent.

Il se justifie par deux choses :

1. **L'audit par domaine.** Chaque domaine rend son verdict, scellé avec ses constats rattachés à leur clause, ses références retenues et le résumé de sa recherche (requêtes, passes, références expirées). Le corpus est filtré par domaine et par clause : le juge ne peut pas retenir une source d'un autre domaine. Un échec touche un domaine, pas l'analyse : l'analyste en échec est repris (`RetryPolicy`), puis consigné, les autres verdicts sont conservés par le checkpointer, et le gate escalade. Le rejeu recalcule chaque domaine à partir des références figées.
2. **La latence**, mais peu, comme le mesure la série 8.

### Gain de latence mesuré (série 8, 65 essais sur le jeu de démonstration)

Durée réelle de l'étape des analystes, entre les horodatages des checkpoints, comparée à la somme des latences des appels LLM de chaque analyste :

| Essais | Somme des latences LLM par analyste | Domaine le plus long seul (borne idéale) | Durée réelle de l'étape | Écart |
| --- | --- | --- | --- | --- |
| 29 essais avec des appels LLM dans au moins deux domaines | 71,3 s | 35,8 s | 53,0 s | **26 % de moins** |
| Les 54 essais qui atteignent les analystes | 91,4 s | — | 86,1 s | 6 % de moins |

- Par contrat, en médiane : de 0,9 s à 2,4 s pour l'étape des analystes, sur une analyse médiane de 6,1 s (extraction 3,0 s, explication 1,6 s).
- 25 essais sur 54 n'appellent le LLM que dans un seul domaine, 14 dans deux, 10 dans trois, 5 dans quatre : sur ce jeu, un contrat n'a le plus souvent de constats que dans un ou deux domaines. Avec un seul domaine appelé, il n'y a rien à paralléliser, et l'étape dure même plus longtemps que ses appels (embedding, base, orchestration).
- **Ordre de grandeur : au mieux une seconde gagnée par contrat, sur six.** Les séries 6 et 7 donnaient le même ordre de grandeur (30 % et 28 % de moins sur les essais à plusieurs domaines).

Le gain croîtrait avec des contrats qui déclenchent des constats dans plusieurs domaines à la fois, ou avec un juge plus lent. Il ne suffit pas, seul, à justifier le découpage.

## Un point faible commun : l'extraction

Les quatre analystes lisent la même extraction. Une erreur ou une injection à cet endroit les touche tous : quatre verdicts concordants valent un seul témoin sur les clauses. C'est pourquoi le vérificateur d'extraction fait partie du pattern, et non un détail d'implémentation.

La série 4 (J4) l'a montré : une consigne glissée dans un contrat faisait **omettre** au modèle une clause bloquante, sans rien citer de faux ; les quatre analystes suivaient, et le contrat sortait en `GO`. Cinq couches se complètent depuis : prompt d'extraction, vérification par code, détection d'instructions, règles, revue humaine. Aucune ne suffit seule.

Sur le contrat réaliste du jeu, le modèle lit « ne peut être inférieur à un trimestre » comme un préavis de 3 mois. Le code refuse cette valeur, absente de la citation, à raison : un minimum n'est pas la durée du préavis. Le modèle la rend encore au second essai, et le contrat part en revue humaine. **Le modèle devine, le code refuse la devinette.**

D'une série à l'autre, le même modèle, à température 0, ne commet pas les mêmes erreurs : le contrat 04, extrait sans faute à la série 6, voit sa durée omise aux 5 premiers essais des séries 7 et 8, rattrapée à chaque fois par la vérification des absences. La sûreté repose sur les contrôles par code, pas sur la régularité du modèle.

## Alternatives écartées

- **Un agent LLM unique qui lit le contrat et décide.** Rejeté pour l'audit, pas pour la qualité : un verdict rendu par un modèle n'est ni déterministe ni rejouable, et sa justification n'est pas contrôlable. Ici, un LLM ne décide jamais.
- **Un analyste unique, en code, qui enchaîne les quatre domaines.** Même qualité, par construction. Écarté pour l'isolement des échecs par domaine et le verdict scellé par domaine, qu'il faudrait réimplémenter dans un seul nœud ; la seconde de latence gagnée compte peu.
- **Un superviseur piloté par LLM** qui choisirait les analystes à appeler. Le routage est fixe et connu d'avance ; un routeur LLM ajouterait coût, variabilité et surface d'attaque, sans gain.
- **Débat ou conseil par vote** entre agents. Les analystes ne répondent pas à la même question : il n'y a rien à mettre aux voix. Et un vote entre LLM ne serait pas rejouable.
- **Une agrégation des verdicts par un LLM.** Le gate est du code ; ses seuils, sa marge et son ordre sont dans la configuration, et scellés par leur empreinte.

## `NO_GO` n'est rendu que sur un blocage dur

Avec la configuration du projet, le pire cumul de toutes les pénalités sans blocage donne un score agrégé de 0,505 : au pire `GO_RESERVES`, avec une marge de 0,005, donc en revue humaine (`test_pire_cumul_des_penalites_sans_blocage_reste_au_dessus_du_seuil_no_go`). Des risques cumulés produisent au pire une réserve ou une escalade vers un humain ; `NO_GO` a toujours une raison explicite et nommée, la règle bloquante (responsabilité de l'acheteur illimitée, révision de prix non plafonnée, données personnelles sans accord de traitement, transfert sans garantie).

C'est un choix de conception, pas une propriété du code : le seuil `NO_GO` reste dans la configuration, et une configuration client plus sévère peut l'atteindre. La réserve est mince : une pénalité supplémentaire de plus de 0,005 point pondéré rendrait `NO_GO` atteignable sans blocage dur.

## Place de LangGraph

LangGraph porte l'orchestration : `Send` pour le fan-out, réducteurs pour la fusion, `interrupt()` et `Command(resume=...)` pour l'humain, checkpoints PostgreSQL pour la reprise et l'historique. Il est confiné à `adapters/langgraph/` : nœuds, règles, gate, politique et audit sont des fonctions pures, testées sans le framework (`docs/adr-002-ports-et-adaptateurs.md`).

## Conséquences

- Le système se présente comme « multi-agents » par sa forme, mais ses analystes sont des outils bornés, et ses LLM ne décident rien. Le README le dit.
- Le coût du découpage est de la complexité, pas de l'argent : réducteurs, gardes d'échec par analyste, routage du fan-out ; le CRAG n'est appelé que pour les clauses qui portent un constat, comme il le serait dans un analyste unique.
- Le gain de latence est mesuré, et modeste ; il n'est pas présenté comme la raison du découpage.
- Le point faible commun reste l'extraction : toute évolution qui réduirait sa vérification affaiblirait les quatre analystes à la fois.
