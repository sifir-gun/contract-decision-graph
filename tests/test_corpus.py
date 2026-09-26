"""Corpus : nettoyage des fichiers réels, versions, découpage, manifeste, fiches."""

from datetime import date
from pathlib import Path

import pytest
from doubles import HashEmbedder

from cdg.application import ingestion
from cdg.domain import corpus
from cdg.domain.config import load_config

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


def test_date_de_recuperation_propre_a_un_article():
    by_ref = {a.reference: (a, domains) for a, domains in ingestion.articles()}
    clause_penale, domains = by_ref["C. civ., art. 1231-5"]
    assert (clause_penale.retrieved_at, domains) == (date(2026, 9, 25), ["financier"])
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
    rows = ingestion.rows(HashEmbedder(), CONFIG.corpus.chunk_max_words)
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
