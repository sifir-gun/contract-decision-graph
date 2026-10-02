"""Corpus : nettoyage des fichiers réels, versions, découpage, manifeste, fiches."""

import re
from datetime import date
from pathlib import Path

import pytest
from doubles import HashEmbedder

from cdg.application import ingestion
from cdg.domain import corpus, explanation
from cdg.domain.config import load_config
from cdg.domain.models import REQUIRED_KINDS

RAW = Path(__file__).resolve().parents[1] / "data" / "corpus" / "raw"
CONFIG = load_config()


def real(path: str) -> str:
    return (RAW / path).read_text(encoding="utf-8")


# --- 1. Légifrance : version et texte modificateur deviennent des métadonnées -----------


def test_version_bornee_et_texte_modificateur_en_metadonnees():
    article = corpus.parse_legifrance(real("code-commerce/L441-10.txt"))
    assert article.article == "L441-10"
    assert (article.valid_from, article.valid_until) == (
        date(2019, 4, 26),
        date(2027, 1, 1),
    )
    assert article.amendment == (
        "modification",
        "Ordonnance n°2019-359 du 24 avril 2019 - art. 1",
    )
    assert (
        "Version en vigueur" not in article.text and "Modifié par" not in article.text
    )
    assert article.text.startswith("I.-Sauf dispositions contraires")


def test_version_ouverte_et_creation():
    article = corpus.parse_legifrance(real("code-civil/1231-3.txt"))
    assert (article.valid_from, article.valid_until) == (date(2016, 10, 1), None)
    assert article.amendment == (
        "création",
        "Ordonnance n°2016-131 du 10 février 2016 - art. 2",
    )
    assert article.text.startswith("Le débiteur n'est tenu")


# --- 2. Lignes d'interface en fin de fichier : supprimées ------------------------------


def test_lignes_d_interface_supprimees():
    # aucune ligne d'interface dans le corpus récupéré : ajoutées ici à un fichier réel
    raw = (
        real("code-commerce/L441-10.txt")
        + "\nVoir les versions\n\nComparer les versions\nTextes liés\n"
    )
    article = corpus.parse_legifrance(raw)
    assert (
        article.text == corpus.parse_legifrance(real("code-commerce/L441-10.txt")).text
    )
    for line in ("Voir les versions", "Comparer les versions", "Textes liés"):
        assert line not in article.text


# --- 3. Notes « Conformément à ... » : hors du texte, en métadonnée --------------------


def test_note_conformement_sortie_du_texte():
    article = corpus.parse_legifrance(real("code-civil/1171.txt"))
    assert article.note.startswith("Conformément aux dispositions du I de l'article 16")
    assert "Conformément" not in article.text
    assert article.text.endswith("l'adéquation du prix à la prestation.")


# --- 4. Validité à la date d'analyse -------------------------------------------------


def test_validite_a_la_date_d_analyse():
    article = corpus.parse_legifrance(real("code-commerce/L441-10.txt"))
    assert not corpus.expired(article.valid_until, date(2026, 12, 31))
    assert corpus.expired(
        article.valid_until, date(2027, 1, 1)
    )  # fin de validité atteinte
    assert not corpus.expired(None, date(2099, 1, 1))  # version ouverte


def test_date_francaise():
    assert corpus.french_date("1er janvier 2027") == date(2027, 1, 1)
    assert corpus.french_date("20 août 2026") == date(2026, 8, 20)
    with pytest.raises(ValueError, match="date"):
        corpus.french_date("20 agosto 2026")


# --- EUR-Lex (RGPD) : titre, intitulé, texte ------------------------------------------


@pytest.mark.parametrize(
    "path,number,heading",
    [
        ("rgpd/art-44.txt", "44", "Principe général applicable aux transferts"),
        ("rgpd/art-4.txt", "4", "Définitions"),  # ligne vide avant l'intitulé
    ],
)
def test_eurlex_intitule(path, number, heading):
    article = corpus.parse_eurlex(real(path))
    assert (article.article, article.heading) == (number, heading)
    assert heading not in article.text.splitlines()[0]
    assert article.valid_until is None and article.note is None


# --- Découpage -------------------------------------------------------------------------


def test_decoupage_d_un_long_article_reel():
    article = corpus.parse_eurlex(real("rgpd/art-28.txt"))
    chunks = corpus.chunk(article.text, CONFIG.corpus.chunk_max_words)
    assert len(chunks) > 1
    assert all(len(c.split()) <= CONFIG.corpus.chunk_max_words for c in chunks)
    joined = " ".join(" ".join(chunks).split())
    assert joined == " ".join(article.text.split())  # rien de perdu, ordre conservé


# --- Manifeste : chaque fichier est ingéré ou explicitement exclu ------------------------


def test_manifeste_couvre_tout_le_corpus():
    manifest = ingestion.load_manifest()
    listed = {path for path, _ in manifest.files()} | set(manifest.excluded_files())
    on_disk = {p.relative_to(RAW).as_posix() for p in RAW.rglob("*.txt")}
    assert listed == on_disk
    assert "rgpd/art-79.txt" in manifest.excluded_files()


def test_articles_ingérés():
    rows = list(ingestion.articles())
    refs = {a.reference for a, _ in rows}
    assert "C. com., art. L441-10" in refs and "RGPD, art. 28" in refs
    assert "RGPD, art. 79" not in refs
    assert all(domains for _, domains in rows)


def test_aucun_numero_d_article_commun_a_deux_sources():
    # l'explication compare les articles cités par leur seul numéro normalisé
    # (domain/explanation.py) : un numéro partagé par deux sources rendrait citable
    # l'article d'une source quand seul celui de l'autre est retenu
    sources: dict[str, set[str]] = {}
    for article, _ in ingestion.articles():
        [number] = explanation.articles(article.reference)
        sources.setdefault(number, set()).add(article.source_id)
    shared = {n: sorted(s) for n, s in sources.items() if len(s) > 1}
    assert not shared, (
        f"numéros d'articles communs à plusieurs sources : {shared}. Passer, dans "
        "domain/explanation.py, à une comparaison des articles cités par source et "
        "numéro, puis retirer ce test."
    )


def test_date_de_recuperation_propre_a_un_article():
    by_ref = {a.reference: (a, kinds) for a, kinds in ingestion.articles()}
    clause_penale, kinds = by_ref["C. civ., art. 1231-5"]
    assert (clause_penale.retrieved_at, kinds) == (
        date(2026, 9, 25),
        ["penalites_execution"],
    )
    assert by_ref["C. civ., art. 1231-3"][0].retrieved_at == date(
        2026, 9, 24
    )  # date de la source


# --- Fiches : avertissement, et aucune affirmation sans source du corpus ------------------


FICHES = {
    "fiche-sous-traitance-rgpd",
    "fiche-transferts-hors-ue",
    "fiche-responsabilite-plafonds",
    "fiche-penalites-execution",
    "fiche-delais-paiement",
    "fiche-revision-prix",
    "fiche-duree-preavis",
}


def test_fiches_du_corpus():
    assert {f.id for f in ingestion.load_fiches()} == FICHES


def test_fiches_avertissement_et_deux_sections():
    for fiche in ingestion.load_fiches():
        assert fiche.body.startswith(corpus.FICHE_DISCLAIMER), fiche.id
        sections = corpus.fiche_sections(fiche.body)
        assert list(sections) == [corpus.TEXT_SECTION, corpus.APPLICATION_SECTION], (
            fiche.id
        )
        assert all(sections.values()), f"{fiche.id} : section vide"
        # aucune affirmation hors des deux sections
        assert sum(map(len, sections.values())) == len(
            corpus.claim_lines(fiche.body)
        ), fiche.id


def test_ce_que_dit_le_texte_chaque_paraphrase_cite_un_article_admis():
    manifest = ingestion.load_manifest()
    for fiche in ingestion.load_fiches():
        for line in corpus.fiche_sections(fiche.body)[corpus.TEXT_SECTION]:
            cited = corpus.citations(line)
            assert cited, f"{fiche.id} : paraphrase sans source : {line}"
            for source_id, number in cited:
                assert manifest.admits(source_id, number), (fiche.id, source_id, number)


def test_comment_le_projet_l_applique_citations_facultatives_mais_admises():
    manifest = ingestion.load_manifest()
    for fiche in ingestion.load_fiches():
        for line in corpus.fiche_sections(fiche.body)[corpus.APPLICATION_SECTION]:
            for source_id, number in corpus.citations(line):
                assert manifest.admits(source_id, number), (fiche.id, source_id, number)


def test_sections_d_une_fiche():
    body = (
        f"{corpus.FICHE_DISCLAIMER}\n\n# Titre\n\n## {corpus.TEXT_SECTION}\n\n- a (source : x, art. 1)\n"
        f"\n## {corpus.APPLICATION_SECTION}\n\n- b\n- c\n"
    )
    assert corpus.fiche_sections(body) == {
        corpus.TEXT_SECTION: ["- a (source : x, art. 1)"],
        corpus.APPLICATION_SECTION: ["- b", "- c"],
    }


def test_une_fiche_herite_la_fin_de_validite_des_articles_qu_elle_cite():
    # la fiche paraphrase L441-10 : quand la version expire, la paraphrase aussi
    rows = ingestion.rows(
        HashEmbedder(), CONFIG.corpus.chunk_max_words, CONFIG.embedding.passage_prefix
    )
    validity = {}
    for row in rows:
        validity.setdefault(row.source_id, set()).add(row.valid_until)
    assert validity["fiche-delais-paiement"] == {date(2027, 1, 1)}
    assert validity["fiche-penalites-execution"] == {
        None
    }  # C. civ. 1231-5, version ouverte
    assert validity["fiche-sous-traitance-rgpd"] == {
        None
    }  # articles sans fin de validité
    assert validity["code-commerce"] >= {date(2027, 1, 1)}


def test_aucun_fichier_brut_ne_repete_sa_ligne_de_titre():
    # un titre répété (« Article 32 » deux fois) deviendrait l'intitulé à l'ingestion
    repeated = []
    for path in sorted(RAW.rglob("*.txt")):
        lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines()]
        lines = [line for line in lines if line]
        if lines and lines.count(lines[0]) > 1:
            repeated.append(path.relative_to(RAW).as_posix())
    assert repeated == []


# --- Rattachement déclaré des sources aux types de clause (J4) ---------------------------


def declared():
    """(source, types de clause déclarés) : articles du manifeste, puis fiches."""
    articles = [(a.reference, kinds) for a, kinds in ingestion.articles()]
    fiches = [(f.id, f.kinds) for f in ingestion.load_fiches()]
    return articles, fiches


def test_chaque_source_declare_des_types_de_clause_connus():
    articles, fiches = declared()
    for source, kinds in articles + fiches:
        assert kinds, f"{source} : aucun type de clause déclaré"
        assert len(set(kinds)) == len(kinds), f"{source} : type répété"
        assert set(kinds) <= set(REQUIRED_KINDS), (source, kinds)


def test_chaque_type_de_clause_a_au_moins_un_article_et_une_fiche():
    articles, fiches = declared()
    for kind in REQUIRED_KINDS:
        assert any(kind in kinds for _, kinds in articles), f"{kind} : aucun article"
        assert any(kind in kinds for _, kinds in fiches), f"{kind} : aucune fiche"


def test_article_cite_par_une_fiche_rattache_a_une_de_ses_clauses():
    # une fiche paraphrase ses articles pour son sujet : chacun peut justifier au moins
    # une des clauses de la fiche
    kinds_of = {(a.source_id, a.article): set(k) for a, k in ingestion.articles()}
    for fiche in ingestion.load_fiches():
        cited = {
            c for line in corpus.claim_lines(fiche.body) for c in corpus.citations(line)
        }
        for article in cited:
            assert kinds_of[article] & set(fiche.kinds), (fiche.id, article)


def test_rattachements_de_la_mesure_du_j3_corriges():
    # rattachements lâches relevés par la mesure du J3 : l'art. 28 et la fiche sur la
    # sous-traitance ne justifient pas un transfert, la fiche transferts pas un accord
    by_ref = {a.reference: kinds for a, kinds in ingestion.articles()}
    fiches = {f.id: f.kinds for f in ingestion.load_fiches()}
    assert "transfert_hors_ue" not in by_ref["RGPD, art. 28"]
    assert "transfert_hors_ue" not in fiches["fiche-sous-traitance-rgpd"]
    assert "accord_traitement_donnees" not in fiches["fiche-transferts-hors-ue"]


def test_domaines_deduits_des_types_de_clause():
    assert corpus.by_domain(
        ["preavis_resiliation", "responsabilite_fournisseur", "responsabilite_acheteur"]
    ) == [
        ("juridique", ["responsabilite_acheteur", "responsabilite_fournisseur"]),
        ("operationnel", ["preavis_resiliation"]),
    ]
    with pytest.raises(ValueError, match="inconnu"):
        corpus.by_domain(["penalites_retard"])
    with pytest.raises(ValueError, match="aucun type"):
        corpus.by_domain([])


def test_fiche_domaines_deduits():
    [fiche] = [f for f in ingestion.load_fiches() if f.id == "fiche-duree-preavis"]
    assert fiche.kinds == ["duree_engagement", "preavis_resiliation"]
    assert fiche.domains == ["operationnel"]


def test_sources_md_donne_les_memes_rattachements_que_le_manifeste_et_les_fiches():
    text = (RAW.parent / "SOURCES.md").read_text(encoding="utf-8")
    documented = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 4 and cells[0].startswith("`") and "`" in cells[-2]:
            kinds = [k.strip(" `") for k in cells[-2].split(",")]
            key = (
                cells[0].strip("`")
                if cells[0].startswith("`fiche-")
                else (
                    cells[0].strip("`"),
                    cells[1].removeprefix("art. "),
                )
            )
            documented[key] = kinds
    expected = {(a.source_id, a.article): k for a, k in ingestion.articles()}
    expected |= {f.id: f.kinds for f in ingestion.load_fiches()}
    assert documented == expected


def test_texte_embarque_et_son_empreinte():
    """Texte embarqué : l'en-tête écrit par le code, puis le texte ; son empreinte couvre
    aussi le préfixe de passage du modèle, tout ce qui détermine le vecteur."""
    assert corpus.embedded_text("C. civ., art. 1170", "Toute clause…") == (
        "C. civ., art. 1170\nToute clause…"
    )
    a = corpus.embedded_hash("passage: ", "C. civ., art. 1170\nToute clause…")
    assert len(a) == 64
    assert a != corpus.embedded_hash("", "C. civ., art. 1170\nToute clause…")
    assert a != corpus.embedded_hash("passage: ", "C. civ., art. 1171\nToute clause…")


# --- intitulés officiels des articles Légifrance (ADR 006, PR 2) -------------------------
# Lus sur la page de chaque article, avec son lien : provenance de chaque article. La
# technique des en-têtes qui les reprenait a été mesurée puis abandonnée (journal).

LEGIFRANCE_ARTICLE = re.compile(
    r"^https://www\.legifrance\.gouv\.fr/codes/article_lc/LEGIARTI\d{12}$"
)
LEVEL = re.compile(
    r"^(Partie|LIVRE|Livre|TITRE|Titre|Sous-titre|Chapitre|Section|Sous-section) "
)


def test_intitules_officiels_de_chaque_article_legifrance_presents():
    """Chaque article admis d'une source Légifrance a sa hiérarchie officielle, lue sur sa
    page (lien) et datée ; aucune hiérarchie pour un article non admis."""
    manifest = ingestion.load_manifest()
    urls = []
    for source_id, source in manifest.sources.items():
        assert source["title"].strip(), source_id
        if source["format"] != "legifrance":
            assert "hierarchy" not in source, source_id
            continue
        assert isinstance(source["hierarchy_verified_at"], date)
        assert set(source["hierarchy"]) == set(source["articles"]), source_id
        for number, entry in source["hierarchy"].items():
            assert LEGIFRANCE_ARTICLE.match(entry["url"]), (number, entry["url"])
            assert entry["levels"], number
            assert all(LEVEL.match(level) for level in entry["levels"]), number
            urls.append(entry["url"])
    assert len(urls) == len(set(urls)) == 9
