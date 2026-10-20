# Plan d'implémentation : versions successives des textes du corpus

> **Pour les agents :** sous-skill requis : superpowers:subagent-driven-development
> (recommandé) ou superpowers:executing-plans, tâche par tâche. Les étapes se cochent
> (`- [ ]`).

**But :** garder les versions successives d'un article dans le corpus (L441-10 : 2019 et
2027) et laisser la date d'analyse choisir la version, sans perdre les signaux
« expirée » ni la défense des échéances.

**Architecture :** une règle pure dans `domain/corpus.py` (`in_force`,
`drop_superseded`, fenêtre des fiches), appliquée deux fois : en SQL dans l'adaptateur
PostgreSQL (avant `DISTINCT ON` et `LIMIT`) et en Python dans `crag.generate` (qui sert
aussi la démonstration). Le manifeste déclare les versions suivantes ; une migration
change la clé d'unicité des extraits ; les fiches déclarent les versions relues.

**Pile :** Python 3.12, pydantic v2, psycopg, pgvector, PostgreSQL 16, pytest.

**Spec :** `docs/spec-versions-corpus.md` (validée le 16/10/2026). La lire avant chaque
tâche ; elle tranche en cas de doute.

## Contraintes globales

- Français partout (code, messages, commits) ; un commit par tâche au vert, message en
  français, terminé par `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Tests d'abord ; doublures pour les LLM ; aucune dépendance ajoutée.
- Aucun repli silencieux : toute anomalie de versions, de manifeste ou de fiche est une
  `ValueError` qui nomme le fichier ou la fiche.
- Isolation (`tests/test_isolation.py`) : `domain/` n'importe ni `ports/` ni
  `application/` ; `drop_superseded` travaille sur un `Protocol` du domaine.
- Le domaine renvoie des valeurs ; les dates comparées sont des `date`, jamais des
  chaînes.
- Aucun champ ajouté à `ClauseRetrieval`, `RetrievalTrace`, `AgentVerdict` ni à l'état ;
  format v2 du journal inchangé.
- Aucun réglage ajouté à `config/decision.yaml`.
- Données synthétiques des tests : domaines réservés (RFC 2606), aucun nom réel.
- PostgreSQL 16 (poste 16.11, cluster 16.15), vérifié le 16/10/2026 :
  `CREATE UNIQUE INDEX IF NOT EXISTS … NULLS NOT DISTINCT` (documentation officielle) ;
  `ON CONFLICT (colonnes)` retient tout index unique portant exactement ces colonnes ;
  comportement de `NULLS NOT DISTINCT` en arbitre **non décrit** par la documentation :
  prouvé par le test de la tâche 3.
- Une seule suite de tests à la fois sur la base locale. Avant tout push :
  `./scripts/check.sh`, dont seule la dernière ligne fait foi.
- Rien n'est poussé : le propriétaire lance `git push` et `gh pr create`.

## Points de vigilance

Cas que la spec implique et qu'aucun critère ne couvre ; chacun a son test dans la tâche
qui possède le code.

1. Copie Légifrance avec l'étiquette « NOTA : » seule sur sa ligne avant la note : elle
   ne doit pas entrer dans le texte indexé (tâche 2,
   `test_etiquette_nota_hors_du_texte`).
2. Date d'analyse égale au début d'une version (01/01/2027) : la nouvelle version est en
   vigueur, l'ancienne expirée (tâche 1, `test_in_force_bornes`).
3. Référence indexée dans deux domaines : le filtre SQL ne doit pas dépendre du domaine
   (tâche 4, la parité couvre `financier` et `operationnel` : l'article synthétique est
   rattaché aux deux).
4. Fiche qui ne déclare que la version la plus récente : elle n'est pas en vigueur avant
   le début de celle-ci (tâche 6, `test_fiche_qui_ne_couvre_que_la_seconde_version`).
5. Clause avec une référence expirée et une autre à venir : constat de clôture « hors de
   leur période de vigueur », pas « toutes expirées » (tâche 5,
   `test_generate_cloture_mixte`).

## Carte des critères d'acceptation

1. Échéances : script au 02/11/2026 et au 01/03/2027, code 0, `"echeances": []` →
   tâche 7 : `test_script_reussit_hors_horizon` (dates du critère) ; tâche 8 : commande.
2. Démonstration, `delai_paiement`, au 31/12/2026 et au 01/01/2027 → tâche 7 :
   `test_demo_l441_10_selon_la_date`.
3. Deux versions de L441-10 en base, ingestion rejouable → tâche 7 :
   `test_deux_versions_de_l441_10_stockees` (`pg`).
4. Parité SQL/Python aux 19/08/2026, 31/12/2026, 01/01/2027 → tâche 4 :
   `test_parite_sql_python_corpus_synthetique` ; tâche 7 :
   `test_parite_sql_python_corpus_reel` (`pg`).
5. `NULLS NOT DISTINCT` arbitre de `ON CONFLICT` → tâche 3 :
   `test_insertion_sans_debut_deja_presente_ignoree` (`pg`).
6. Dossier daté → tâche 7 : `test_dossier_texte_de_la_version_de_sa_date` (code en
   tâche 6).
7. Fiche sans déclaration face à deux versions → tâche 6 :
   `test_fiche_sans_declaration_expire_a_la_seconde_version`,
   `test_script_echoue_sur_une_fiche_non_relue`.
8. Analyses antidatées → tâche 6 : `test_demo_responsabilite_avant_l442_1`,
   `test_demo_delai_avant_2019_insuffisant` (constats de la tâche 5, fenêtres des
   fiches de la tâche 6).
9. Versions mal enchaînées → tâche 2 : `test_versions_mal_enchainees_refusees`.
10. Seul le 4e alinéa du I diffère → tâche 7 :
    `test_l441_10_seul_le_quatrieme_alinea_change`.
11. `./scripts/check.sh` → tâche 8, dernière ligne « check.sh : SUCCÈS ».

**Écarts d'ordre avec la spec (section « Réalisation »), sans effet sur le
comportement :**
- le texte daté des références (`reference_texts(on)`), le dossier et le jeu
  d'évaluation daté passent de la tâche 8 à la tâche 6. Sinon, dès la tâche 7,
  `reference_texts()` (indexé par référence) garderait le texte 2027 et la citation
  figée « au sens du 3 du I de l'article 289 du code général des impôts » casserait ;
- le critère 8 passe de la tâche 5 à la tâche 6 : avant les fenêtres des fiches, une
  fiche n'a pas de début de validité et resterait retenue.

---

### Tâche 1 : règle pure des versions

**Fichiers :**
- Modifier : `src/cdg/domain/corpus.py`
- Test : `tests/test_versions.py` (nouveau)

**Interfaces :**
- Produit, dans `cdg.domain.corpus` :
  - `class Versioned(Protocol)` : propriétés en lecture `reference: str`,
    `valid_from: date | None`, `valid_until: date | None` ;
  - `in_force(valid_from: date | None, valid_until: date | None, on: date) -> bool` ;
  - `drop_superseded[T: Versioned](items: Sequence[T], on: date) -> list[T]`.

- [ ] **Étape 1 : tests**

```python
D19, D27 = date(2019, 4, 26), date(2027, 1, 1)


@dataclass(frozen=True)
class V:
    reference: str
    valid_from: date | None
    valid_until: date | None


OLD, NEW = V("A", D19, D27), V("A", D27, None)


def test_in_force_bornes():
    assert corpus.in_force(D19, D27, D19) and not corpus.in_force(D19, D27, D27)
    assert corpus.in_force(D27, None, D27) and not corpus.in_force(D27, None, D19)
    assert corpus.in_force(None, None, date(1900, 1, 1))


def test_drop_superseded_garde_la_version_en_vigueur():
    other = V("B", None, None)
    items = [NEW, other, OLD]
    assert corpus.drop_superseded(items, date(2026, 12, 31)) == [other, OLD]
    assert corpus.drop_superseded(items, D27) == [NEW, other]


def test_drop_superseded_sans_version_en_vigueur_garde_tout():
    assert corpus.drop_superseded([NEW, OLD], date(2019, 1, 1)) == [NEW, OLD]
    expired = V("C", D19, D27)
    assert corpus.drop_superseded([expired], date(2027, 3, 1)) == [expired]
```

- [ ] **Étape 2 :** `uv run pytest tests/test_versions.py -q` → échec
  (`AttributeError: … in_force`).
- [ ] **Étape 3 :** implémenter dans `domain/corpus.py`. `in_force` réutilise
  `expired`. `drop_superseded` : références qui ont au moins un élément en vigueur, puis
  filtre en conservant l'ordre. Mettre à jour la docstring du module (règle 4).
- [ ] **Étape 4 :** `uv run pytest tests/test_versions.py tests/test_isolation.py -q` →
  vert ; `uv run mypy` → vert.
- [ ] **Étape 5 :** commit « Corpus : règle pure des versions en vigueur et
  supplantées ».

### Tâche 2 : parseur, manifeste `later_versions`, corpus synthétique

**Fichiers :**
- Modifier : `src/cdg/domain/corpus.py` (`_VERSION`, étiquette « NOTA : »,
  `Manifest`, `check_chain`)
- Modifier : `src/cdg/application/ingestion.py` (`load_manifest`, `articles`,
  `load_fiches` : lecture de `RAW`, `FICHES`, `MANIFEST` à l'appel)
- Modifier : `tests/conftest.py` (fixture `corpus_synthetique`)
- Test : `tests/test_corpus.py`, `tests/test_versions.py`

**Interfaces :**
- Produit :
  - `_VERSION` accepte `Version en vigueur à partir du {date}` → `(valid_from, None)` ;
  - une ligne exactement `NOTA :` (espaces de bord ignorés) est écartée du texte ;
  - `Manifest.files()` inclut les fichiers de `later_versions` (même clé
    `(source_id, article)`) ; `Manifest.later(source_id: str, article: str) ->
    list[dict[str, Any]]` ; `Manifest.__post_init__` refuse un article de
    `later_versions` absent de `articles` ;
  - `check_chain(versions: Sequence[tuple[str, Article]]) -> None` (domaine) : chemins
    et versions dans l'ordre du manifeste ; erreurs : suffixe `_AAAA-MM-JJ` d'une version
    suivante différent de `valid_from` ; `valid_until` d'une version ≠ `valid_from` de la
    suivante ; version ouverte avant la dernière ;
  - `ingestion.load_manifest(path: Path | None = None)`,
    `ingestion.load_fiches(directory: Path | None = None)` : défaut lu à l'appel ;
  - `ingestion.articles()` rend une `Article` par version, dans l'ordre ; version
    suivante sans `retrieved_at` : `ValueError` ;
  - fixture `corpus_synthetique(tmp_path, monkeypatch) -> Path` (racine) : remplace
    `ingestion.RAW`, `ingestion.FICHES`, `ingestion.MANIFEST`, vide le cache de
    `ingestion.reference_texts`.
- Contenu du corpus synthétique (`code-test`, format `legifrance`, citation
  `C. test`, `retrieved_at: 2026-10-01`) :
  - `articles: {"T1": [delai_paiement, preavis_resiliation]}` (deux domaines) ;
  - `code-test/T1.txt` : « Version en vigueur du 26 avril 2019 au 01 janvier 2027 »,
    « Modifié par Texte de test n°1 - art. 1 », paragraphes « Alinéa commun aux deux
    versions. » et « Alinéa propre à la première version. » ;
  - `code-test/T1_2027-01-01.txt` : « Version en vigueur à partir du 01 janvier 2027 »,
    « Modifié par Texte de test n°2 - art. 2 », « Alinéa commun aux deux versions. »,
    « Alinéa propre à la seconde version. », une ligne `NOTA :`, puis « Conformément à
    l'article 9 du texte de test n°2, ces dispositions entrent en vigueur le 1er janvier
    2027. » ;
  - `later_versions: {"T1": [{file: "T1_2027-01-01.txt", url:
    "https://example.org/T1", retrieved_at: 2026-10-16}]}` ;
  - `fiches/test.md` : `id: fiche-test`, `titre: Fiche de test`,
    `clauses: [delai_paiement]`, `versions_verifiees: {code-test: {T1: [2019-04-26,
    2027-01-01]}}` (lu à la tâche 6), avertissement, « Ce que dit le texte » : « - Alinéa
    commun aux deux versions (source : code-test, art. T1). », « Comment le projet
    l'applique » : « - Choix de test. ».

- [ ] **Étape 1 : tests**

```python
def test_version_a_venir_ouverte(corpus_synthetique):
    second = corpus.parse_legifrance(
        (corpus_synthetique / "raw/code-test/T1_2027-01-01.txt").read_text("utf-8")
    )
    assert (second.valid_from, second.valid_until) == (date(2027, 1, 1), None)
    assert second.note.startswith("Conformément à l'article 9")


def test_etiquette_nota_hors_du_texte(corpus_synthetique):
    second = ...  # comme ci-dessus
    assert "NOTA" not in second.text
    assert second.text.endswith("Alinéa propre à la seconde version.")


def test_une_article_par_version_meme_reference(corpus_synthetique):
    found = list(ingestion.articles())
    assert [(a.reference, a.valid_from, a.retrieved_at) for a, _ in found] == [
        ("C. test, art. T1", date(2019, 4, 26), date(2026, 10, 1)),
        ("C. test, art. T1", date(2027, 1, 1), date(2026, 10, 16)),
    ]
    assert {tuple(k) for _, k in found} == {("delai_paiement", "preavis_resiliation")}


@pytest.mark.parametrize("change,message", [...])
def test_versions_mal_enchainees_refusees(corpus_synthetique, change, message):
    # cas : trou (début 2027-01-02, fichier renommé en conséquence) ; chevauchement
    # (première version jusqu'au 01 février 2027) ; première version ouverte
    # (« Version en vigueur depuis le 26 avril 2019 ») ; suffixe ≠ début ; version
    # suivante sans retrieved_at ; article de later_versions absent d'articles
    change(corpus_synthetique)
    with pytest.raises(ValueError, match=message):
        list(ingestion.articles())
```

  Le message attendu nomme le fichier fautif (`T1_2027-01-01.txt` ou `T1.txt`).
  Sur le corpus réel, `test_manifeste_couvre_tout_le_corpus` et les tests existants
  restent verts sans changement.
- [ ] **Étape 2 :** `uv run pytest tests/test_corpus.py tests/test_versions.py -q` →
  échecs attendus (motif de version, fixture absente).
- [ ] **Étape 3 :** implémenter (ordre : `_VERSION` et « NOTA : », `Manifest`,
  `check_chain`, `articles`, défauts lus à l'appel, fixture).
- [ ] **Étape 4 :** `uv run pytest -m "not pg" -q` → vert ; `uv run mypy` ;
  `uv run ruff check`.
- [ ] **Étape 5 :** commit « Corpus : versions suivantes déclarées au manifeste, et
  enchaînement contrôlé ».

### Tâche 3 : migration 009 et clé d'unicité versionnée

**Fichiers :**
- Créer : `migrations/009_rag_versions.sql` (texte de la spec, section 2)
- Modifier : `src/cdg/adapters/postgres/rag_store.py` (`insert`)
- Test : `tests/test_retrieval.py`, `tests/test_migrations.py:188`,
  `tests/test_cli.py:38-46`

**Interfaces :**
- Produit : `rag_store.insert` en
  `ON CONFLICT (domain, content_hash, embedding_model, valid_from) DO NOTHING`.

- [ ] **Étape 1 : tests** (`pg`, dans `tests/test_retrieval.py`, sur des `ChunkRow`
  construites comme `rows()`) :
  - `test_meme_texte_deux_debuts_deux_lignes` : la ligne `C. com., art. L441-10` de
    `CHUNKS` insérée avec `valid_from=date(2019, 4, 26)` puis avec
    `valid_from=date(2027, 1, 1)` : `insert` rend 1 puis 1 ;
  - `test_insertion_sans_debut_deja_presente_ignoree` : la même ligne sans
    `valid_from`, insérée deux fois : 1 puis 0 ;
  - `tests/test_migrations.py` : `names[-1] == "009_rag_versions.sql"` ; après
    `migrations.apply`, `pg_indexes` contient `rag_chunks_version_key` et
    `pg_constraint` ne contient plus
    `rag_chunks_domain_content_hash_embedding_model_key` ; relancée, rien ne change
    (assertion existante) ;
  - `tests/test_cli.py` : `009_rag_versions.sql` ajouté à la liste des migrations.
- [ ] **Étape 2 :** adapter `insert` d'abord ;
  `uv run pytest tests/test_retrieval.py tests/test_migrations.py tests/test_cli.py -q`
  → échecs attendus (`ON CONFLICT` sans arbitre, migration absente).
- [ ] **Étape 3 :** écrire la migration.
- [ ] **Étape 4 :** mêmes tests → verts (chaque session de tests PostgreSQL relance
  `setup-db`, donc la 009).
- [ ] **Étape 5 :** commit « Base : clé d'unicité des extraits par version
  (migration 009) ».

### Tâche 4 : recherche datée

**Fichiers :**
- Modifier : `src/cdg/ports/retriever.py` (`Passage.valid_from`, signatures)
- Modifier : `src/cdg/adapters/postgres/rag_store.py` (`search`, `search_unfiltered`,
  `_ranked`, `indexed`, `PgvectorRetriever`)
- Modifier : `src/cdg/application/crag.py` (`retrieve` seulement)
- Modifier : `src/cdg/application/evaluation.py` (`check_index`, `measure`, `run`)
- Modifier : `src/cdg/cli.py` (`_mesure_recherche` : `on=today()`)
- Modifier : `tests/doubles.py` (`FakeRetriever`, `passage(..., valid_from=None)`),
  `tests/test_crag.py` (`Unfiltered`), `tests/test_llm_criteres.py` (`FixedRetriever`),
  `tests/test_evaluation_recherche.py`, `tests/test_retrieval.py`
- Test : `tests/test_retrieval.py`, `tests/test_ports.py`, `tests/test_crag.py`

**Interfaces :**
- Consomme : `drop_superseded`, `in_force` (tâche 1) ; corpus synthétique (tâche 2).
- Produit :
  - `Passage.valid_from: date | None = None` ;
  - `Retriever.search(domain, query, *, kind: str, k: int, on: date) -> list[Passage]` ;
  - `CorpusSearch.search_unfiltered(query, *, k: int, on: date) -> list[Passage]` ;
  - `CorpusSearch.indexed() -> list[tuple[str, date | None, str, str | None]]`
    (référence, début, texte, empreinte) ;
  - `rag_store.search(source, domain, query, *, kind, k, model, settings, on)` et
    `rag_store.search_unfiltered(source, query, *, k, model, settings, on)` ;
  - `evaluation.measure(queries, retriever, corpus, ks, on: date)`,
    `evaluation.run(config, retriever, corpus, ks, on: date)` ; le rapport porte
    `"date": on.isoformat()` ;
  - `FakeRetriever.search` applique `drop_superseded(..., on)` avant la coupe à k.
- Condition SQL ajoutée au filtre de `_ranked` (texte fixe, paramètre `on` répété) :

```sql
((valid_from IS NULL OR valid_from <= %s) AND (valid_until IS NULL OR valid_until > %s))
OR NOT EXISTS (
    SELECT 1 FROM rag_chunks AS other
    WHERE other.embedding_model = rag_chunks.embedding_model
      AND other.reference = rag_chunks.reference
      AND (other.valid_from IS NULL OR other.valid_from <= %s)
      AND (other.valid_until IS NULL OR other.valid_until > %s))
```

- [ ] **Étape 1 : tests**
  - `tests/test_retrieval.py` : `search(...)` du module de test reçoit `on`, défaut
    `date(2026, 9, 25)` ; `test_parite_sql_python_corpus_synthetique` (`pg`) : lignes du
    corpus synthétique (`ingestion.rows(HashEmbedder(), MAX_WORDS, PREFIX)`) insérées
    par un fixture qui les supprime ensuite (`source_id` `code-test` et `fiche-test`) ;
    pour `on` dans `(date(2019, 1, 1), date(2026, 8, 19), date(2026, 12, 31),
    date(2027, 1, 1))` et chaque couple (`financier`, `delai_paiement`),
    (`operationnel`, `preavis_resiliation`), `VECTOR` et `k=100`, résultats SQL
    restreints à ces deux `source_id` :
    `{(p.reference, p.valid_from) for p in sql} == {(r.reference, r.valid_from) for r in
    drop_superseded(lignes, on)}`, où `lignes` sont les `ChunkRow` du domaine et de la
    clause (elles satisfont `Versioned`) ;
  - `test_lecture_des_extraits_indexes_chacun_une_fois` : quadruplets attendus
    `(r, None, t, empreinte)` ; `FakeRetriever.indexed()` rend aussi des quadruplets ;
  - `tests/test_ports.py` : inchangé, il détecte les signatures ;
  - `tests/test_crag.py` : `test_retrieve_passe_la_date_d_analyse` : un `FakeRetriever`
    enregistre `on` ; `crag.retrieve(state, …)` le reçoit égal à
    `state["analysis_date"]` ;
  - `tests/test_evaluation_recherche.py` : `run(..., on=date(2026, 10, 1))` rend
    `"date": "2026-10-01"` ; avec le corpus synthétique, une doublure `CorpusSearch`
    dont `indexed()` rend les quadruplets de `pending_chunks` sauf ceux de début
    `2027-01-01` : `check_index` lève `IndexMismatch`.
- [ ] **Étape 2 :** `uv run pytest tests/test_retrieval.py tests/test_ports.py
  tests/test_crag.py tests/test_evaluation_recherche.py -q` → échecs attendus.
- [ ] **Étape 3 :** implémenter port, adaptateur, doublures, `retrieve`, évaluation,
  CLI.
- [ ] **Étape 4 :** `uv run pytest -q` (base démarrée) → vert ; `uv run mypy`.
- [ ] **Étape 5 :** commit « Recherche datée : versions supplantées écartées avant le
  regroupement ».

### Tâche 5 : CRAG, versions à venir et constats

**Fichiers :**
- Modifier : `src/cdg/application/crag.py` (`generate`, docstring du module)
- Modifier : `src/cdg/adapters/demo/references.py` (`valid_from` du `Passage`)
- Test : `tests/test_crag.py`, `tests/test_web_demo.py`

**Interfaces :**
- Consomme : `drop_superseded`, `in_force`, `expired` ; `passage(..., valid_from=…)` ;
  `D19 = date(2019, 4, 26)`, `D27 = date(2027, 1, 1)` dans le module de test.
- Produit : textes exacts des constats (spec, section 3) :
  - `NOT_YET = "référence pas encore en vigueur à la date d'analyse ({on}) :
    {reference}, version en vigueur à partir du {start}, non retenue ({kind})"` ;
  - `OUT_OF_FORCE = "références pertinentes toutes hors de leur période de vigueur pour
    la clause {kind} : elles ne peuvent pas justifier seules le verdict"` ;
  - textes « expirée » et « toutes expirées » inchangés.

- [ ] **Étape 1 : tests** (`tests/test_crag.py`, `_generated(relevant, on=…)`)
  - `test_generate_version_a_venir_ecartee_sans_constat` : `[passage(L441,
    valid_from=D27), passage(L441, id=2, valid_until=D27, valid_from=D19)]` au
    31/12/2026 : `retained == [L441]`, `expired == []`, `findings == []` ; au
    01/01/2027 : idem ;
  - `test_generate_reference_a_venir_signalee` : `[passage(L441, valid_from=D19,
    valid_until=D27), passage(L441, id=2, valid_from=D27)]` au 01/01/2019 :
    `retained == []`, `expired == []`, `findings == [NOT_YET(on=2019-01-01,
    start=2019-04-26), OUT_OF_FORCE]` ;
  - `test_generate_cloture_mixte` : `[passage(L441, valid_until=D19),
    passage(FICHE, id=2, valid_from=D27)]` au 01/06/2026 : constat « expirée » pour
    L441, `NOT_YET` pour la fiche, puis `OUT_OF_FORCE` ;
  - `test_generate_expiree_cite_la_fin_de_la_derniere_version` : deux versions
    expirées (fins 2019-04-26 et 2027-01-01) au 01/03/2027 : le constat cite
    `2027-01-01` ;
  - `test_generate_sans_extrait_pertinent` : ajouter `assert result.findings == []` ;
  - `tests/test_web_demo.py` : `test_reference_expiree_signalee_jamais_retenue` passe
    sur le corpus synthétique au 01/03/2027 **après** retrait de la version 2027
    (`later_versions` vidé, fichier supprimé).
- [ ] **Étape 2 :** `uv run pytest tests/test_crag.py tests/test_web_demo.py -q` →
  échecs attendus.
- [ ] **Étape 3 :** réécrire `generate` : `drop_superseded`, puis par référence (ordre
  de première apparition) retenue, expirée (fin max) ou à venir (début min), puis
  clôture si au moins une référence et aucune en vigueur.
- [ ] **Étape 4 :** `uv run pytest -q` → vert.
- [ ] **Étape 5 :** commit « CRAG : versions à venir signalées, constat de clôture
  précisé ».

### Tâche 6 : fenêtre des fiches, échéances par référence, texte daté

**Fichiers :**
- Modifier : `src/cdg/domain/corpus.py` (`Fiche.verified`, `fiche_window`)
- Modifier : `src/cdg/application/ingestion.py` (`load_fiches`, `pending_chunks`,
  `source_validities`, `reference_texts`, retrait de `_article_validity` et
  `_fiche_validity`)
- Modifier : `src/cdg/application/service.py` (`dossier` : `reference_texts(
  status["analysis_date"])`)
- Test : `tests/test_versions.py`, `tests/test_echeances_corpus.py`,
  `tests/test_corpus.py`, `tests/test_service.py`,
  `tests/test_evaluation_recherche.py:36`

**Interfaces :**
- Produit :
  - `Fiche.verified: dict[tuple[str, str], list[date]]` (défaut vide), lu de
    `versions_verifiees` ;
  - `fiche_window(fiche_id: str, cited: set[tuple[str, str]], verified:
    Mapping[tuple[str, str], Sequence[date]], versions: Mapping[tuple[str, str],
    Sequence[tuple[date | None, date | None]]]) -> tuple[date | None, date | None]`
    (domaine ; erreurs de la spec, section 4, chacune nommant la fiche) ;
  - extraits de fiche : `valid_from` et `valid_until` de la fenêtre ;
  - `source_validities()` : une entrée par référence ; article : fin de sa dernière
    version ;
  - `ingestion.reference_texts(on: date | None) -> dict[str, dict[str, str]]` (sans
    défaut ; cache par date) : texte de la version en vigueur à `on` (articles et
    fiches) ; `None` : seules les références à une seule version.

- [ ] **Étape 1 : tests** (corpus synthétique)
  - `test_fiche_couvre_les_versions_declarees` : fenêtre `(2019-04-26, None)` ;
  - `test_fiche_sans_declaration_expire_a_la_seconde_version` : `versions_verifiees`
    retiré : fenêtre `(2019-04-26, 2027-01-01)` ;
  - `test_fiche_qui_ne_couvre_que_la_seconde_version` : `[2027-01-01]` seul :
    `(2027-01-01, None)` ;
  - `test_fiche_declaration_invalide_refusee` (paramétré) : article non cité, date
    inconnue (`2020-01-01`), versions non consécutives (fixture à trois versions),
    intersection vide ;
  - `test_echeances_par_reference` : `source_validities()` du corpus synthétique ==
    `{"C. test, art. T1": None, "Fiche projet : Fiche de test": None}` ;
  - `test_script_echoue_sur_une_fiche_non_relue` : sans déclaration, script au
    02/11/2026 : code 1, `erreur == "sources du corpus qui expirent dans les 60 jours"`,
    `echeances == [{"source": "Fiche projet : Fiche de test", "fin_de_validite":
    "2027-01-01"}]` ;
  - corpus réel, `tests/test_corpus.py` : fenêtre de « Plafonds et clauses de
    responsabilité » commence le `2026-08-20` ; « Délais de paiement » garde
    `(2019-04-26, 2027-01-01)` (une seule version au corpus à ce stade) ;
  - `test_textes_dates_par_reference` (corpus synthétique) :
    `reference_texts(date(2026, 12, 31))["C. test, art. T1"]["texte"]` contient
    « première version », au `2027-01-01` « seconde version » ; `reference_texts(None)`
    n'a pas `C. test, art. T1` et a `Fiche projet : Fiche de test` ;
  - `tests/test_service.py`, `test_textes_du_corpus_par_reference` :
    `reference_texts(date(2026, 9, 25))` ;
  - `tests/test_evaluation_recherche.py` : `TEXTS =
    ingestion.reference_texts(date(2026, 10, 1))` ;
  - analyses antidatées sur le corpus réel (`tests/test_web_demo.py`), qui exigent les
    fenêtres des fiches :
    `test_demo_responsabilite_avant_l442_1` : `DeclaredCrag` au 01/06/2026, clause
    `responsabilite_fournisseur` (`value=50`) : `retained` contient `C. civ., art.
    1170`, `1171` et `1231-3` ; un constat `NOT_YET` cite `C. com., art. L442-1` et un
    autre `Fiche projet : Plafonds et clauses de responsabilité` ;
    `test_demo_delai_avant_2019_insuffisant` : fixture `demo`, POST `/analyse` de
    `demo-06-no-go-conseil` avec `"date": "2019-01-01"` ; verdict `financier` :
    `retrieval_status == "INSUFFISANT"`, un constat contient « hors de leur période de
    vigueur ».
- [ ] **Étape 2 :** `uv run pytest tests/test_versions.py tests/test_echeances_corpus.py
  tests/test_corpus.py tests/test_service.py tests/test_evaluation_recherche.py
  tests/test_web_demo.py -q` → échecs attendus.
- [ ] **Étape 3 :** implémenter.
- [ ] **Étape 4 :** `uv run pytest -q` → vert.
- [ ] **Étape 5 :** commit « Fiches : versions relues déclarées, fenêtre de validité ;
  échéances par référence ; texte daté des références ».

### Tâche 7 : version 2027 de L441-10 au corpus

**Fichiers :**
- Créer (propriétaire) : `data/corpus/raw/code-commerce/L441-10_2027-01-01.txt`
- Modifier : `data/corpus/manifest.yaml`, `data/corpus/SOURCES.md`,
  `data/corpus/fiches/delais-paiement.md`
- Test : `tests/test_corpus.py`, `tests/test_echeances_corpus.py`,
  `tests/test_ingest.py`, `tests/test_retrieval.py`, `tests/test_web_demo.py`

- [ ] **Étape 1 :** demander au propriétaire de copier la page
  https://www.legifrance.gouv.fr/codes/article_lc/LEGIARTI000054567689/2027-01-01
  (bloc de l'article, du titre « Article L441-10 » à la note) dans le fichier, en UTF-8,
  et la date de sa copie. **Attendre le fichier.** Le lire en entier avant tout usage ;
  une ligne d'interface imprévue (autre que celles déjà traitées) reçoit sa règle de
  nettoyage testée dans `domain/corpus.py`, jamais une retouche du fichier.
- [ ] **Étape 2 : tests**
  - `test_l441_10_seul_le_quatrieme_alinea_change` : paragraphes nettoyés des deux
    versions ; une seule paire diffère, l'ancienne contient « article 289 du code
    général des impôts », la nouvelle « article L. 216-34 du code des impositions sur
    les biens et services » ; version 2027 : `(2027-01-01, None)`, `amendment ==
    ("modification", "Ordonnance n°2026-671 du 27 juillet 2026 - art. 2")`, note
    commençant par « Conformément à l'article 49 » ;
  - `tests/test_echeances_corpus.py` : `test_script_reussit_hors_horizon` paramétré sur
    `2026-09-27`, `2026-11-02`, `2027-03-01` ; `test_fins_de_validite_des_sources_du_
    corpus` : aucune fin, 29 références ;
    `test_script_echoue_avec_la_source_et_la_date` est retiré : son cas (format de
    l'erreur compris) est couvert par `test_script_echoue_sur_une_fiche_non_relue`
    (tâche 6) ;
  - `test_demo_l441_10_selon_la_date` : `DeclaredCrag` aux 31/12/2026 et 01/01/2027 :
    `retained == ["C. com., art. L441-10", "Fiche projet : Délais de paiement entre
    professionnels"]` (ordre de `pending_chunks`), `findings == []` ; `drop_superseded`
    sur les extraits déclarés garde pour L441-10 les seuls débuts `2019-04-26` puis
    `2027-01-01` ;
  - `test_deux_versions_de_l441_10_stockees` (`pg`, `tests/test_ingest.py`) : lignes
    de L441-10 par domaine == extraits des deux versions ; `test_version_bornee_stockee`
    attend deux couples (début, amendement) ;
  - `test_parite_sql_python_corpus_reel` (`pg`) : la parité de la tâche 4 sur le corpus
    réel, `financier`, `delai_paiement`, aux 19/08/2026, 31/12/2026, 01/01/2027 ;
  - fiche : `test_une_fiche_herite_la_fin_de_validite_des_articles_qu_elle_cite` attend
    `{None}` pour « Délais de paiement » ;
  - `test_dossier_texte_de_la_version_de_sa_date` (`tests/test_web_demo.py`, fixture
    `demo`) : `demo-06-no-go-conseil` analysé au `2026-12-31` puis, sous un autre
    identifiant, au `2027-01-01` : le texte du dossier de `C. com., art. L441-10`
    contient « code général des impôts », puis « code des impositions sur les biens et
    services ».
- [ ] **Étape 3 :** `uv run pytest -m "not pg" -q` puis `uv run pytest -q` → échecs
  attendus.
- [ ] **Étape 4 :** manifeste (`later_versions` de la spec, date de la copie) ;
  `SOURCES.md` (ligne de la version 2027, colonne « Article » = `L441-10`, ligne
  existante « Version en vigueur du 26/04/2019 au 01/01/2027 », rubrique « Versions
  successives », date de copie dans « Récupération », date de relecture des
  paraphrases) ; fiche : en-tête `versions_verifiees`, puces de la spec section 4, mot
  pour mot :
  - « - En cas de facture périodique, le délai convenu entre les parties ne peut
    dépasser quarante-cinq jours après la date d'émission de la facture (source :
    code-commerce, art. L441-10, I). »
  - « - La version de cet article en vigueur du 26 avril 2019 au 1er janvier 2027 vise
    la facture périodique au sens du code général des impôts (source : code-commerce,
    art. L441-10, I). »
  - « - La version en vigueur à partir du 1er janvier 2027 vise la facture périodique
    permise en application du code des impositions sur les biens et services ; le reste
    de l'article est inchangé (source : code-commerce, art. L441-10). »
  - dernière puce de « Comment le projet l'applique » : « - La date d'analyse choisit la
    version de l'article en vigueur ; cette fiche a été vérifiée contre ses deux
    versions, dont les délais sont identiques. »
- [ ] **Étape 5 :** `uv run pytest -q` → vert ;
  `uv run --no-sync python scripts/echeances_corpus.py --jours 60 --date 2026-11-02`
  et `--date 2027-03-01` → code 0, `"echeances": []`.
- [ ] **Étape 6 :** commit « Corpus : version de L441-10 en vigueur au 01/01/2027
  (ordonnance n° 2026-671), fiche relue ».

### Tâche 8 : documentation et vérification complète

**Fichiers :**
- Créer : `docs/adr-009-versions-du-corpus.md`
- Modifier : `docs/spec-phase1.md`, `docs/exploitation.md`,
  `docs/mise-en-production.md`, `docs/journal.md`, `CLAUDE.md` (ligne `setup-db`)
- Brouillon non commité : `docs/pr-versions-corpus.md` (exclu par
  `.git/info/exclude`)

- [ ] **Étape 1 :** documentation (spec, section 5) : ADR 009 (avec le retour arrière
  et la conduite à tenir) ; `spec-phase1.md` (signature de `Retriever.search`,
  `generate`, validité des fiches, fin de validité rendue par la recherche, renvoi à la
  spec) ; `exploitation.md` (exemple L441-10, procédure de mise à jour, liste des
  migrations jusqu'à `009`, rubrique « Migrations », retour arrière) ;
  `mise-en-production.md` (« Processus de mise à jour », « Première échéance ») ;
  `journal.md` (entrée du chantier : tests d'abord, réalisation, écarts, pistes « veille
  des versions à venir » et « mesures datées ») ; `CLAUDE.md` (`009` dans la ligne
  `setup-db`).
- [ ] **Étape 2 :** `./scripts/check.sh` lancé seul, au premier plan ; lire sa
  dernière ligne : « check.sh : SUCCÈS ». Sinon, diagnostiquer (skill
  systematic-debugging), corriger, relancer.
- [ ] **Étape 3 :** commit « Documentation des versions successives du corpus » ;
  brouillon de PR dans `docs/pr-versions-corpus.md`, non commité ; rappeler au
  propriétaire `setup-db` puis `ingest` sur son poste.
