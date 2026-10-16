"""Versions successives d'un texte du corpus (docs/spec-versions-corpus.md) : la date
d'analyse choisit la version en vigueur ; une version supplantée est écartée."""

from dataclasses import dataclass
from datetime import date

import pytest
from corpus_de_test import (
    FICHE,
    SECOND_FILE,
    SECOND_TEXT,
    T1,
    edit_manifest,
    raw,
    replace_line,
)

from cdg.application import ingestion
from cdg.domain import corpus

D19, D27 = date(2019, 4, 26), date(2027, 1, 1)


@dataclass(frozen=True)
class V:
    reference: str
    valid_from: date | None
    valid_until: date | None


OLD, NEW = V("A", D19, D27), V("A", D27, None)


# --- règle pure ---------------------------------------------------------------------------


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


# --- fichiers : version à venir, manifeste, enchaînement ----------------------------------


def _second(root):
    return corpus.parse_legifrance(raw(root, SECOND_FILE).read_text(encoding="utf-8"))


def test_version_a_venir_ouverte(corpus_synthetique):
    second = _second(corpus_synthetique)
    assert (second.valid_from, second.valid_until) == (date(2027, 1, 1), None)
    assert second.amendment == ("modification", "Texte de test n°2 - art. 2")
    assert second.note.startswith("Conformément à l'article 9")


def test_etiquette_nota_hors_du_texte(corpus_synthetique):
    second = _second(corpus_synthetique)
    assert "NOTA" not in second.text
    assert second.text.endswith("Alinéa propre à la seconde version.")


def test_une_article_par_version_meme_reference(corpus_synthetique):
    found = list(ingestion.articles())
    assert [(a.reference, a.valid_from, a.retrieved_at) for a, _ in found] == [
        (T1, date(2019, 4, 26), date(2026, 10, 1)),
        (T1, date(2027, 1, 1), date(2026, 10, 16)),
    ]
    assert {tuple(k) for _, k in found} == {("delai_paiement", "preavis_resiliation")}


def test_manifeste_liste_les_versions_suivantes(corpus_synthetique):
    manifest = ingestion.load_manifest()
    assert manifest.files() == [
        ("code-test/T1.txt", ("code-test", "T1")),
        (f"code-test/{SECOND_FILE}", ("code-test", "T1")),
    ]
    [later] = manifest.later("code-test", "T1")
    assert later["url"] == "https://example.org/T1"


def _gap(root):
    renamed = "T1_2027-01-02.txt"
    raw(root, SECOND_FILE).rename(raw(root, renamed))
    replace_line(raw(root, renamed), "à partir du 01 janvier", "à partir du 02 janvier")
    edit_manifest(root, lambda s: s["later_versions"]["T1"][0].update(file=renamed))


def _overlap(root):
    replace_line(raw(root, "T1.txt"), "au 01 janvier 2027", "au 01 février 2027")


def _first_open(root):
    replace_line(
        raw(root, "T1.txt"),
        "du 26 avril 2019 au 01 janvier 2027",
        "depuis le 26 avril 2019",
    )


def _suffix(root):
    replace_line(
        raw(root, SECOND_FILE), "à partir du 01 janvier", "à partir du 02 janvier"
    )


def _no_retrieved_at(root):
    edit_manifest(root, lambda s: s["later_versions"]["T1"][0].pop("retrieved_at"))


def _unknown_article(root):
    edit_manifest(root, lambda s: s["later_versions"].update(T2=[]))


@pytest.mark.parametrize(
    "change,message",
    [
        (_gap, r"T1_2027-01-02\.txt.*mal enchaînées"),
        (_overlap, r"T1_2027-01-01\.txt.*mal enchaînées"),
        (_first_open, r"T1\.txt : version ouverte avant la dernière"),
        (_suffix, r"T1_2027-01-01\.txt : suffixe"),
        (_no_retrieved_at, r"T1_2027-01-01\.txt : date de récupération"),
        (_unknown_article, r"versions suivantes d'un article non admis : T2"),
    ],
    ids=[
        "trou",
        "chevauchement",
        "premiere-ouverte",
        "suffixe",
        "sans-date",
        "article",
    ],
)
def test_versions_mal_enchainees_refusees(corpus_synthetique, change, message):
    change(corpus_synthetique)
    with pytest.raises(ValueError, match=message):
        list(ingestion.articles())


# --- fiches : versions relues, fenêtre de validité ----------------------------------------

DECLARED = "T1: [2019-04-26, 2027-01-01]"


def _fiche(root):
    return root / "fiches" / "test.md"


def _window():
    """Début et fin de validité des extraits de la fiche synthétique."""
    [window] = {
        (meta["valid_from"], meta["valid_until"])
        for meta, _, _ in ingestion.pending_chunks(300)
        if meta["source_id"] == "fiche-test"
    }
    return window


def test_fiche_couvre_les_versions_declarees(corpus_synthetique):
    assert _window() == (date(2019, 4, 26), None)


def test_fiche_sans_declaration_expire_a_la_seconde_version(corpus_synthetique):
    replace_line(_fiche(corpus_synthetique), "versions_verifiees:\n  code-test:\n", "")
    replace_line(_fiche(corpus_synthetique), f"    {DECLARED}\n", "")
    assert _window() == (date(2019, 4, 26), date(2027, 1, 1))


def test_fiche_qui_ne_couvre_que_la_seconde_version(corpus_synthetique):
    replace_line(_fiche(corpus_synthetique), DECLARED, "T1: [2027-01-01]")
    assert _window() == (date(2027, 1, 1), None)


def _not_cited(root):
    replace_line(_fiche(root), DECLARED, DECLARED + "\n    T9: [2019-04-26]")


def _unknown_date(root):
    replace_line(_fiche(root), DECLARED, "T1: [2020-01-01]")


def _not_consecutive(root):
    later = "T1_2028-01-01.txt"
    replace_line(
        raw(root, SECOND_FILE),
        "à partir du 01 janvier 2027",
        "du 01 janvier 2027 au 01 janvier 2028",
    )
    raw(root, later).write_text(
        SECOND_TEXT.replace(
            "à partir du 01 janvier 2027", "à partir du 01 janvier 2028"
        ),
        encoding="utf-8",
    )
    entry = {
        "file": later,
        "url": "https://example.org/T1",
        "retrieved_at": date(2026, 10, 16),
    }
    edit_manifest(root, lambda s: s["later_versions"]["T1"].append(entry))
    replace_line(_fiche(root), DECLARED, "T1: [2019-04-26, 2028-01-01]")


def _empty(root):
    raw(root, "T2.txt").write_text(
        "Article T2\nVersion en vigueur du 01 janvier 2010 au 01 janvier 2015\n"
        "Création Texte de test n°0 - art. 1\n\nAlinéa d'un article ancien.\n",
        encoding="utf-8",
    )
    edit_manifest(root, lambda s: s["articles"].update(T2=["delai_paiement"]))
    replace_line(
        _fiche(root),
        "## Comment le projet",
        "- Alinéa d'un article ancien (source : code-test, art. T2).\n\n"
        "## Comment le projet",
    )


@pytest.mark.parametrize(
    "change,message",
    [
        (_not_cited, "fiche-test : versions déclarées pour un article non cité"),
        (_unknown_date, "fiche-test : version du 2020-01-01 de code-test T1 absente"),
        (_not_consecutive, "fiche-test : versions déclarées non consécutives"),
        (_empty, "fiche-test : fenêtre de validité vide"),
    ],
    ids=["non-cite", "date-inconnue", "non-consecutives", "intersection-vide"],
)
def test_fiche_declaration_invalide_refusee(corpus_synthetique, change, message):
    change(corpus_synthetique)
    with pytest.raises(ValueError, match=message):
        ingestion.source_validities()


def test_echeances_par_reference(corpus_synthetique):
    assert ingestion.source_validities() == {T1: None, FICHE: None}


# --- texte d'une référence à la date d'analyse (dossier) ----------------------------------


def test_textes_dates_par_reference(corpus_synthetique):
    assert (
        "première version" in ingestion.reference_texts(date(2026, 12, 31))[T1]["texte"]
    )
    assert "seconde version" in ingestion.reference_texts(date(2027, 1, 1))[T1]["texte"]
    undated = ingestion.reference_texts(None)
    assert T1 not in undated and FICHE in undated
