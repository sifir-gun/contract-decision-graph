"""Versions successives d'un texte du corpus (docs/spec-versions-corpus.md) : la date
d'analyse choisit la version en vigueur ; une version supplantée est écartée."""

from dataclasses import dataclass
from datetime import date

import pytest
from corpus_de_test import SECOND_FILE, T1, edit_manifest, raw, replace_line

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
