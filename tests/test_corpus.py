"""Corpus : nettoyage des fichiers réels, versions, découpage, manifeste, fiches."""

from datetime import date
from pathlib import Path

import pytest

from cdg import corpus
from cdg.config import load_config

RAW = Path(__file__).resolve().parents[1] / "data" / "corpus" / "raw"
CONFIG = load_config()


def real(path: str) -> str:
    return (RAW / path).read_text(encoding="utf-8")


# --- 1. Légifrance : version et texte modificateur deviennent des métadonnées -----------


def test_version_bornee_et_texte_modificateur_en_metadonnees():
    article = corpus.parse_legifrance(real("code-commerce/L441-10.txt"))
    assert article.article == "L441-10"
    assert (article.valid_from, article.valid_until) == (date(2019, 4, 26), date(2027, 1, 1))
    assert article.amendment == ("modification", "Ordonnance n°2019-359 du 24 avril 2019 - art. 1")
    assert "Version en vigueur" not in article.text and "Modifié par" not in article.text
    assert article.text.startswith("I.-Sauf dispositions contraires")


def test_version_ouverte_et_creation():
    article = corpus.parse_legifrance(real("code-civil/1231-3.txt"))
    assert (article.valid_from, article.valid_until) == (date(2016, 10, 1), None)
    assert article.amendment == ("création", "Ordonnance n°2016-131 du 10 février 2016 - art. 2")
    assert article.text.startswith("Le débiteur n'est tenu")


# --- 2. Lignes d'interface en fin de fichier : supprimées ------------------------------


def test_lignes_d_interface_supprimees():
    # aucune ligne d'interface dans le corpus récupéré : ajoutées ici à un fichier réel
    raw = (
        real("code-commerce/L441-10.txt")
        + "\nVoir les versions\n\nComparer les versions\nTextes liés\n"
    )
    article = corpus.parse_legifrance(raw)
    assert article.text == corpus.parse_legifrance(real("code-commerce/L441-10.txt")).text
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
    assert corpus.expired(article.valid_until, date(2027, 1, 1))  # fin de validité atteinte
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
    manifest = corpus.load_manifest()
    listed = {path for path, _ in manifest.files()} | set(manifest.excluded_files())
    on_disk = {p.relative_to(RAW).as_posix() for p in RAW.rglob("*.txt")}
    assert listed == on_disk
    assert "rgpd/art-79.txt" in manifest.excluded_files()


def test_articles_ingérés():
    rows = list(corpus.articles())
    refs = {a.reference for a, _ in rows}
    assert "C. com., art. L441-10" in refs and "RGPD, art. 28" in refs
    assert "RGPD, art. 79" not in refs
    assert all(domains for _, domains in rows)


# --- Fiches : avertissement, et aucune affirmation sans source du corpus ------------------


def test_fiches_avertissement_et_sources():
    fiches = corpus.load_fiches()
    assert len(fiches) >= 6
    manifest = corpus.load_manifest()
    for fiche in fiches:
        assert fiche.body.startswith(corpus.FICHE_DISCLAIMER)
        for line in corpus.claim_lines(fiche.body):
            cited = corpus.citations(line)
            assert cited, f"{fiche.id} : affirmation sans source : {line}"
            for source_id, number in cited:
                assert manifest.admits(source_id, number), (fiche.id, source_id, number)
