"""Corpus : nettoyage des fichiers réels, versions, découpage, manifeste, fiches."""

import re
from datetime import date
from pathlib import Path

import pytest
from corpus_de_test import (
    CODE_A,
    CODE_A_REF,
    CODE_B,
    CODE_B_REF,
    FICHE,
    HOMONYM,
    HOMONYM_NAMES,
    NBSP,
    POINT_A,
    POINT_A_REF,
    POINT_B,
    POINT_B_REF,
    POINTS,
    T1,
    add_points,
    point_raw,
    point_source,
    replace_line,
    write_homonyms,
)
from doubles import HashEmbedder

from cdg.application import ingestion
from cdg.domain import corpus, explanation, profiles
from cdg.domain.config import load_config

RAW = Path(__file__).resolve().parents[1] / "data" / "corpus" / "raw"
CONFIG = load_config()
FOURNISSEUR = profiles.spec("fournisseur")


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


def test_cree_par_lu_comme_une_creation_hors_du_texte():
    # page de L122-4 (CPI), enregistrée le 21/10/2026 : « Créé par … », et non
    # « Création … » ; texte modificateur gardé tel qu'affiché
    article = corpus.parse_legifrance(real("code-propriete-intellectuelle/L122-4.txt"))
    assert article.amendment == (
        "création",
        "Loi 92-597 1992-07-01 annexe JORF 3 juillet 1992",
    )
    assert "Créé par" not in article.text
    assert article.text.startswith("Toute représentation ou reproduction intégrale")


@pytest.mark.parametrize(
    "path,number,start,amendment",
    [
        (
            "code-propriete-intellectuelle/L111-1.txt",
            "L111-1",
            date(2020, 12, 27),
            ("modification", "LOI n°2020-1674 du 24 décembre 2020 - art. 35 (V)"),
        ),
        (
            "code-propriete-intellectuelle/L112-2.txt",
            "L112-2",
            date(1994, 5, 11),
            # tel qu'affiché, parenthèses vides comprises
            (
                "modification",
                "Loi n°94-361 du 10 mai 1994 - art. 1 () JORF 11 mai 1994",
            ),
        ),
        (
            "code-propriete-intellectuelle/L122-4.txt",
            "L122-4",
            date(1992, 7, 3),
            ("création", "Loi 92-597 1992-07-01 annexe JORF 3 juillet 1992"),
        ),
        (
            "code-propriete-intellectuelle/L131-3.txt",
            "L131-3",
            date(1992, 7, 3),
            ("création", "Loi 92-597 1992-07-01 annexe JORF 3 juillet 1992"),
        ),
        (
            "code-civil/1231.txt",
            "1231",
            date(2016, 10, 1),
            ("modification", "Ordonnance n°2016-131 du 10 février 2016 - art. 2"),
        ),
        (
            "code-civil/1231-1.txt",
            "1231-1",
            date(2016, 10, 1),
            ("création", "Ordonnance n°2016-131 du 10 février 2016 - art. 2"),
        ),
        (
            "code-assurances/L124-3.txt",
            "L124-3",
            date(2007, 12, 19),
            ("modification", "LOI n°2007-1774 du 17 décembre 2007 - art. 1"),
        ),
    ],
    ids=["L111-1", "L112-2", "L122-4", "L131-3", "1231", "1231-1", "L124-3"],
)
def test_articles_legifrance_de_la_sous_traitance(path, number, start, amendment):
    # débuts de validité de la spec (« Nouvelles sources Légifrance »), versions ouvertes ;
    # aucune ligne de métadonnée dans le texte indexé
    article = corpus.parse_legifrance(real(path))
    assert (article.article, article.valid_from, article.valid_until) == (
        number,
        start,
        None,
    )
    assert (article.amendment, article.note) == (amendment, None)
    for line in article.text.splitlines():
        assert not line.startswith(
            ("Article ", "Version en vigueur", "Modifié par", "Création", "Créé par")
        ), (path, line)


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


def test_l441_10_seul_le_quatrieme_alinea_change():
    """Version en vigueur au 01/01/2027 (ordonnance n° 2026-671, art. 2) : la
    comparaison officielle de Légifrance ne relève qu'une modification, au 4e alinéa du I
    (fondement de la facture périodique)."""
    old = corpus.parse_legifrance(real("code-commerce/L441-10.txt"))
    new = corpus.parse_legifrance(real("code-commerce/L441-10_2027-01-01.txt"))
    assert (new.valid_from, new.valid_until) == (date(2027, 1, 1), None)
    assert new.amendment == (
        "modification",
        "Ordonnance n°2026-671 du 27 juillet 2026 - art. 2",
    )
    assert new.note.startswith(
        "Conformément à l'article 49 de l'ordonnance n° 2025-1247"
    )
    before, after = old.text.split("\n\n"), new.text.split("\n\n")
    assert len(before) == len(after) == 6
    changed = [(b, a) for b, a in zip(before, after, strict=True) if b != a]
    assert len(changed) == 1 and changed[0][0] == before[3]
    assert "article 289 du code général des impôts" in changed[0][0]
    assert (
        "article L. 216-34 du code des impositions sur les biens et services"
        in changed[0][1]
    )


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


# --- EUR-Lex : points d'annexe, marques de consolidation (spec des profils, PR 2) --------
# Premières lignes relevées sur EUR-Lex le 20/10/2026 : numéro et intitulé sur une même
# ligne, séparés par des espaces insécables ; texte des paragraphes fictif.

BODY = f"1){NBSP}Premier alinéa.\n\n2){NBSP}Second alinéa :\n\n—{NBSP}\nun tiret.\n"


@pytest.mark.parametrize(
    "title,number,heading",
    [
        (
            f"4.2.10.2.1.{NBSP * 3}Exigences relatives aux matériaux",
            "4.2.10.2.1",
            "Exigences relatives aux matériaux",
        ),
        (
            (
                f"21.A.307{NBSP * 3} Conditions d’admissibilité pour l’installation "
                "applicables aux pièces et équipements"
            ),
            "21.A.307",
            (
                "Conditions d’admissibilité pour l’installation applicables aux pièces "
                "et équipements"
            ),
        ),
    ],
    ids=["point-final", "sans-point-final"],
)
def test_point_eurlex_numero_et_intitule_sur_une_ligne(title, number, heading):
    article = corpus.parse_eurlex(f"{title}\n\n{BODY}")
    assert (article.article, article.heading) == (number, heading)
    # aucun paragraphe pris pour l'intitulé : le texte les garde tous
    assert article.text.split("\n\n") == [
        f"1){NBSP}Premier alinéa.",
        f"2){NBSP}Second alinéa :",
        "—\nun tiret.",
    ]
    assert (article.valid_from, article.valid_until, article.note) == (None,) * 3


def test_point_sans_intitule_sur_sa_ligne_refuse():
    # forme que la page n'affiche pas : jamais un paragraphe pris pour l'intitulé
    with pytest.raises(ValueError, match="première ligne"):
        corpus.parse_eurlex(f"4.2.10.2.1.\nExigences\n\n{BODY}")


def test_marques_de_consolidation_sur_leur_ligne_retirees():
    raw = (
        f"▼B\n9.A.12{NBSP * 3} Conditions de test\n\na){NBSP}\n\nPremier.\n\n▼M12\n\n"
        f"b){NBSP}\n\nSecond{NBSP}( 9 ); et\n\n▼M7\n\nc){NBSP}\n\nTroisième.\n"
    )
    article = corpus.parse_eurlex(raw)
    assert (article.article, article.heading) == ("9.A.12", "Conditions de test")
    # l'appel de note reste, tel qu'affiché
    assert article.text.split("\n\n") == [
        "a)",
        "Premier.",
        "b)",
        f"Second{NBSP}( 9 ); et",
        "c)",
        "Troisième.",
    ]


@pytest.mark.parametrize(
    "line",
    [f"►M5{NBSP * 3}point ◄", "texte ►M5 modifié ◄ suite", "▼M12 texte", "◄"],
    ids=["seule", "dans-le-texte", "suivie-du-texte", "fermante"],
)
def test_marque_de_consolidation_dans_une_ligne_refusee_par_la_lecture(line):
    with pytest.raises(ValueError, match="marque de consolidation"):
        corpus.parse_eurlex(f"9.A.12{NBSP * 3} Conditions de test\n\n{line}\n")


def test_marque_de_consolidation_dans_une_ligne_refusee(corpus_synthetique):
    add_points(corpus_synthetique)
    replace_line(
        point_raw(corpus_synthetique, POINT_A),
        "Premier alinéa",
        f"►M5{NBSP * 3}point ◄ Premier alinéa",
    )
    with pytest.raises(
        ValueError,
        match=r"annexe-test/point-9\.1\.2\.1\.txt : marque de consolidation",
    ):
        list(ingestion.articles())


@pytest.mark.parametrize(
    "path,number,heading,first,last",
    [
        (
            "sti-materiel-roulant/point-4.2.10.2.1.txt",
            "4.2.10.2.1",
            "Exigences relatives aux matériaux",
            f"1){NBSP}La sélection des matériaux",
            f"Elle doit être renouvelée tous les 5{NBSP}ans.",
        ),
        (
            "certification-navigabilite/point-21.A.307.txt",
            "21.A.307",
            (
                "Conditions d’admissibilité pour l’installation applicables aux pièces "
                "et équipements"
            ),
            "a)",
            "avec les données de définition concernées.",
        ),
    ],
    ids=["1302-2014", "748-2012"],
)
def test_points_d_annexe_reels(path, number, heading, first, last):
    # copies du 20/10/2026 : numéro et intitulé sur la première ligne, aucun paragraphe
    # perdu ; marques de consolidation retirées, appel de note gardé
    raw = real(path)
    point = corpus.parse_eurlex(raw)
    assert (point.article, point.heading) == (number, heading)
    assert point.text.startswith(first) and point.text.endswith(last)
    assert (point.valid_from, point.valid_until, point.note) == (None,) * 3
    assert not any(mark in point.text for mark in ("▼", "►", "◄"))
    kept = [
        line.strip()
        for line in raw.splitlines()[1:]
        if line.strip() and not line.strip().startswith("▼")
    ]
    assert [line.strip() for line in point.text.splitlines() if line.strip()] == kept


def test_appel_de_note_du_point_21_a_307_garde():
    point = corpus.parse_eurlex(real("certification-navigabilite/point-21.A.307.txt"))
    assert f"règlement (UE) no{NBSP}965/2012 de la Commission{NBSP}( 9 ); et" in (
        point.text
    )


def test_reference_d_un_point(corpus_synthetique):
    add_points(corpus_synthetique)
    by_ref = {a.reference: (a, kinds) for a, kinds in ingestion.articles()}
    # unité déclarée au manifeste ; « art. » par défaut, références inchangées
    assert set(by_ref) == {T1, POINT_A_REF, POINT_B_REF}
    point, kinds = by_ref[POINT_A_REF]
    assert (point.source_id, point.article, point.heading, kinds) == (
        POINTS,
        POINT_A,
        "Exigences de test",
        ["delai_paiement"],
    )
    assert (point.valid_from, point.valid_until, point.retrieved_at) == (
        None,
        None,
        date(2026, 10, 20),
    )
    assert point.text.startswith(f"1){NBSP}Premier alinéa du point de test.")
    other = by_ref[POINT_B_REF][0]
    assert "▼" not in other.text and f"texte de test{NBSP}( 9 )" in other.text
    # la fiche qui cite les deux points garde sa fenêtre ; chaque point a son texte
    assert ingestion.source_validities() == {
        T1: None,
        POINT_A_REF: None,
        POINT_B_REF: None,
        FICHE: None,
    }
    assert {POINT_A_REF, POINT_B_REF, FICHE} <= set(ingestion.reference_texts(None))
    # numéro lu dans la référence par l'explication, points gardés (numéros partagés)
    assert [explanation.articles(r) for r in (POINT_A_REF, POINT_B_REF)] == [
        {POINT_A},
        {POINT_B},
    ]


def test_numero_de_point_different_du_manifeste_refuse(corpus_synthetique):
    add_points(corpus_synthetique)
    replace_line(point_raw(corpus_synthetique, POINT_A), "9.1.2.1.", "9.1.2.2.")
    with pytest.raises(
        ValueError,
        match=r"annexe-test/point-9\.1\.2\.1\.txt : numéro « 9\.1\.2\.2 » lu en "
        r"première ligne, attendu « 9\.1\.2\.1 »",
    ):
        list(ingestion.articles())


@pytest.mark.parametrize(
    "unit", ["", " ", None, 3], ids=["vide", "blanc", "nul", "nombre"]
)
def test_unite_de_reference_illisible_refusee(unit):
    with pytest.raises(ValueError, match="annexe-test : unité de référence illisible"):
        corpus.Manifest(sources={POINTS: {**point_source(), "unit": unit}})


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


def shared_numbers() -> dict[str, dict[str, str]]:
    """Numéros d'articles ou de points communs à plusieurs sources du corpus :
    {numéro : {source : référence}}."""
    by_number: dict[str, dict[str, str]] = {}
    for article, _ in ingestion.articles():
        by_number.setdefault(article.article, {})[article.source_id] = article.reference
    return {n: sources for n, sources in by_number.items() if len(sources) > 1}


def retaining(reference: str) -> explanation.ExplanationRequest:
    """Demande d'explication d'un constat dont seule `reference` est retenue ; son texte,
    écrit par les règles, ne cite aucun article."""
    finding = explanation.FindingToExplain(
        id="juridique-1",
        domain="juridique",
        kind="responsabilite_fournisseur",
        text="Constat des règles, sans article cité.",
        references=[reference],
    )
    return explanation.ExplanationRequest(
        decision="NO_GO",
        proposed_decision="NO_GO",
        margin=None,
        human=None,
        failure_stage=None,
        findings=[finding],
    )


def citing(req: explanation.ExplanationRequest, text: str) -> explanation.Draft:
    [finding] = req.findings
    return explanation.Draft(
        findings=[
            explanation.ExplainedFinding(
                id=finding.id,
                kind=finding.kind,
                references=finding.references,
                text=text,
            )
        ]
    )


def named_forms(name: str, unit: str, number: str) -> tuple[str, ...]:
    """Citations d'un numéro que touche un nom de sa source (`explanation.citations`) :
    avant lui, sous la forme des références, suivi d'une parenthèse, de deux-points ou de
    « en son » ; après lui, après une préposition ou entre parenthèses."""
    return (
        f"{name}, {unit} {number}",
        f"{name} ({unit} {number})",
        f"{name} : {unit} {number}",
        f"{name}, en son {unit} {number}",
        f"{unit} {number} du {name}",
        f"{unit} {number} ({name})",
    )


@pytest.mark.parametrize("corpus_lu", ["depot", "paire_synthetique"])
def test_numeros_partages_du_corpus_compares_par_source(corpus_lu, request):
    # l'explication rattache une citation à la source dont un nom la touche (citation ou
    # titre du manifeste) et la compare par source et numéro ; sans nom, par son seul
    # numéro (domain/explanation.py). Au dépôt : CPI et C. mon. fin., art. L112-2 ; la
    # paire synthétique garde le test indépendant du corpus du dépôt.
    if corpus_lu == "paire_synthetique":
        write_homonyms(request.getfixturevalue("corpus_synthetique"))
    manifest = ingestion.load_manifest()
    names = ingestion.source_names()
    assert set(names) == set(manifest.sources)
    # chaque source a des noms que le contrôle reconnaît, avant et après le numéro de
    # chacun de ses articles, lu dans sa référence
    for article, _ in ingestion.articles():
        source, number = article.source_id, article.article
        unit = manifest.unit(source)
        assert explanation.citations(article.reference, names) == {(source, number)}
        for name in names[source]:
            for form in named_forms(name, unit, number):
                assert explanation.citations(form, names) == {(source, number)}, form
    shared = shared_numbers()
    if corpus_lu == "paire_synthetique":
        assert names == HOMONYM_NAMES
        assert shared == {HOMONYM: {CODE_A: CODE_A_REF, CODE_B: CODE_B_REF}}
    else:
        assert shared == {
            "L112-2": {
                "code-monetaire-financier": "C. mon. fin., art. L112-2",
                "code-propriete-intellectuelle": "CPI, art. L112-2",
            }
        }
    # chaque numéro partagé : la source retenue acceptée, l'autre refusée, sous chaque
    # forme et par chacun de ses noms ; sans nom de source, accepté comme aujourd'hui
    for number, sources in shared.items():
        for retained, reference in sources.items():
            req = retaining(reference)
            for source in sources:
                unit = manifest.unit(source)
                for name in names[source]:
                    for form in named_forms(name, unit, number):
                        reasons = explanation.refusals(
                            citing(req, f"Voir {form}."), req, names=names
                        )
                        assert (reasons == []) == (source == retained), (form, reasons)
            unnamed = citing(req, f"Voir {manifest.unit(retained)} {number}.")
            assert explanation.refusals(unnamed, req, names=names) == []


def test_nom_commun_a_deux_sources_refuse_a_la_lecture():
    # validés à la lecture du manifeste : le graphe ne se construit pas (orchestrateur)
    sources = {
        sid: {"citation": "C. x", "title": f"Code {sid}", "articles": {}}
        for sid in ("code-x", "code-y")
    }
    with pytest.raises(ValueError, match="nom de source commun à code-x et code-y"):
        ingestion.source_names(corpus.Manifest(sources=sources))


def test_source_sans_nom_refusee():
    sources = {"code-x": {"citation": "C. x", "articles": {}}}
    with pytest.raises(
        ValueError, match="code-x : nom de source absent ou illisible .clé title."
    ):
        ingestion.source_names(corpus.Manifest(sources=sources))


def test_date_de_recuperation_propre_a_un_article():
    by_ref = {a.reference: (a, kinds) for a, kinds in ingestion.articles()}
    clause_penale, kinds = by_ref["C. civ., art. 1231-5"]
    assert (clause_penale.retrieved_at, kinds) == (
        date(2026, 9, 25),
        ["penalites_execution", "penalites_retard_execution"],
    )
    assert by_ref["C. civ., art. 1231-3"][0].retrieved_at == date(
        2026, 9, 24
    )  # date de la source
    # articles ajoutés pour la sous-traitance : copies du 21/10/2026 (Légifrance) et du
    # 20/10/2026 (EUR-Lex)
    retrieved = {ref: a.retrieved_at for ref, (a, _) in by_ref.items()}
    assert {ref: retrieved[ref] for ref in NOUVELLES_REFERENCES} == {
        ref: date(2026, 10, 20) if "Règl." in ref else date(2026, 10, 21)
        for ref in NOUVELLES_REFERENCES
    }


# --- Fiches : avertissement, et aucune affirmation sans source du corpus ------------------


FICHES_FOURNISSEUR = {
    "fiche-sous-traitance-rgpd",
    "fiche-transferts-hors-ue",
    "fiche-responsabilite-plafonds",
    "fiche-penalites-execution",
    "fiche-delais-paiement",
    "fiche-revision-prix",
    "fiche-duree-preavis",
}
# fiches propres au profil sous_traitance (spec, « Nouvelles fiches ») : début de leur
# période de validité, d'après les articles qu'elles citent ; aucune fin
FICHES_SOUS_TRAITANCE = {
    "fiche-protection-design": date(2020, 12, 27),  # CPI, art. L111-1
    "fiche-certification-preuves": None,  # points EUR-Lex, sans borne
    "fiche-assurance-sous-traitant": date(2007, 12, 19),  # C. assur., art. L124-3
    "fiche-delais-execution": date(2016, 10, 1),  # C. civ., art. 1231 et 1231-1
    "fiche-responsabilite-sous-traitance": date(2018, 10, 1),  # C. civ., art. 1171
    "fiche-penalites-retard-execution": date(2016, 10, 1),  # C. civ., art. 1231-5
}
FICHES = FICHES_FOURNISSEUR | set(FICHES_SOUS_TRAITANCE)


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


@pytest.mark.parametrize(
    "line,cited",
    [
        (
            "- Texte (source : sti-materiel-roulant, point 4.2.10.2.1).",
            [("sti-materiel-roulant", "4.2.10.2.1")],
        ),
        (
            (
                "- Texte (sources : certification-navigabilite, point 21.A.307 ; "
                "rgpd, art. 28, par. 3)."
            ),
            [("certification-navigabilite", "21.A.307"), ("rgpd", "28")],
        ),
    ],
    ids=["seul", "avec-un-article"],
)
def test_citation_d_un_point(line, cited):
    assert corpus.citations(line) == cited


def test_citation_d_un_point_avec_sous_point():
    line = "- Texte (source : sti-materiel-roulant, point 4.2.10.2.1, 2))."
    assert corpus.citations(line) == [("sti-materiel-roulant", "4.2.10.2.1")]


@pytest.mark.parametrize(
    "block,cited",
    [
        ("rgpd, art. 4, point 8", [("rgpd", "4")]),
        ("rgpd, art. 83, par. 5, point c", [("rgpd", "83")]),
        ("rgpd, art. 46, par. 2, points b, c et d", [("rgpd", "46")]),
    ],
)
def test_suites_rgpd_non_lues_comme_point(block, cited):
    # un point n'est lu qu'avec un numéro à points : ceux-ci précisent l'article
    assert corpus.citations(f"- Texte (source : {block}).") == cited


@pytest.mark.parametrize(
    "line,entry",
    [
        ("(sources : code-civil, art. 1171 ; ailleurs)", "ailleurs"),
        ("(source : le code civil, art. 1171)", "le code civil, art. 1171"),
        ("(sources : code-civil, art. 1171 ; )", ""),
        ("(source : code-civil, point 8)", "code-civil, point 8"),
    ],
    ids=["seconde-entree", "premiere-entree", "entree-vide", "point-sans-points"],
)
def test_entree_de_citation_illisible_refusee(line, entry):
    message = re.escape(f"entrée de citation illisible : {entry!r}")
    with pytest.raises(ValueError, match=message):
        corpus.citations(f"- Texte {line}.")


def test_entree_de_citation_hors_du_bloc_refusee():
    # la parenthèse d'un sous-point ferme le bloc : l'entrée suivante en sortirait, en
    # silence ; parenthèse finale présente, celle-ci reste sans ouvrante : garde des
    # blocs fermés trop tôt
    line = (
        "- Texte (sources : sti-materiel-roulant, point 4.2.10.2.1, 2) ; "
        "certification-navigabilite, point 21.A.307)."
    )
    message = (
        "entrée de citation hors de son bloc, fermé par la parenthèse d'un sous-point ; "
        "suite non lue : "
    )
    with pytest.raises(ValueError, match=re.escape(message)):
        corpus.citations(line)


@pytest.mark.parametrize(
    "line,outside",
    [
        (
            (
                "- Texte (sources : sti-materiel-roulant, point 4.2.10.2.1, 2) ; "
                "certification-navigabilite, point 21.A.307."
            ),
            "certification-navigabilite, point 21.A.307.",
        ),
        (
            "- Texte (sources : rgpd, art. 46, par. 3, a), rgpd, art. 28.",
            "rgpd, art. 28.",
        ),
        (
            "- Texte (sources : rgpd, art. 46, par. 3, a) et rgpd, art. 28.",
            "rgpd, art. 28.",
        ),
    ],
    ids=["point-virgule", "virgule", "et"],
)
def test_entree_apres_un_bloc_sans_parenthese_finale_refusee(line, outside):
    # parenthèse finale oubliée : aucune ne reste à fermer, mais une citation suit le
    # bloc, quel que soit le séparateur ; garde de l'entrée qui suit le bloc
    message = re.escape(
        "entrée de citation hors de son bloc, après la parenthèse d'un sous-point : "
        f"{outside!r}"
    )
    with pytest.raises(ValueError, match=message):
        corpus.citations(line)


@pytest.mark.parametrize(
    "prose,cited",
    [
        # une phrase qui suit un bloc reste admise (fiche des plafonds, du design)
        (
            "- Texte (source : code-civil, art. 1231-3) ; l'article ne dit pas tout.",
            [("code-civil", "1231-3")],
        ),
        (
            "- Texte (source : code-civil, art. 1231-3), dont le texte ne dit pas tout.",
            [("code-civil", "1231-3")],
        ),
        (
            "- Texte (source : code-civil, art. 1171) et du déséquilibre significatif.",
            [("code-civil", "1171")],
        ),
    ],
    ids=["point-virgule", "virgule", "et"],
)
def test_phrase_apres_un_bloc_admise(prose, cited):
    assert corpus.citations(prose) == cited


@pytest.mark.parametrize(
    "line,unread",
    [
        (
            "- Texte (sources : rgpd, art. 46, par. 3, a) et b) ; rgpd, art. 28).",
            " et b) ; rgpd, art. 28).",
        ),
        (
            "- Texte (sources : rgpd, art. 46, par. 3, a), rgpd, art. 28).",
            ", rgpd, art. 28).",
        ),
        (
            "- Texte (sources : rgpd, art. 46, par. 3, a) ; rgpd, art. 28).",
            " ; rgpd, art. 28).",
        ),
        # premier bloc fermé trop tôt : la suite citée s'arrête au bloc suivant
        (
            (
                "- A (sources : rgpd, art. 46, par. 3, a) et b) ; rgpd, art. 28) puis B "
                "(source : code-civil, art. 1231-3)."
            ),
            " et b) ; rgpd, art. 28) puis B ",
        ),
        # second bloc fermé trop tôt : c'est lui que la suite citée désigne
        (
            (
                "- A (source : code-civil, art. 1231-3) et B (sources : rgpd, art. 46, "
                "par. 3, a), rgpd, art. 28)."
            ),
            ", rgpd, art. 28).",
        ),
    ],
    ids=["et", "virgule", "point-virgule", "premier-bloc", "second-bloc"],
)
def test_bloc_ferme_par_un_sous_point_texte_non_lu_refuse(line, unread):
    # la parenthèse d'un sous-point (« a) ») ferme le bloc : ce qui la suit jusqu'à la
    # parenthèse qui ferme vraiment le bloc n'est pas lu, quel que soit le séparateur
    message = re.escape(
        "entrée de citation hors de son bloc, fermé par la parenthèse d'un sous-point ; "
        f"suite non lue : {unread!r}"
    )
    with pytest.raises(ValueError, match=message):
        corpus.citations(line)


@pytest.mark.parametrize(
    "line,cited",
    [
        # sous-point en fin de bloc : rien n'est laissé avant la parenthèse fermante
        (
            "- Texte (source : sti-materiel-roulant, point 4.2.10.2.1, 2)).",
            [("sti-materiel-roulant", "4.2.10.2.1")],
        ),
        (
            "- Texte (source : code-commerce, art. L441-10, I).",
            [("code-commerce", "L441-10")],
        ),
        # parenthèses équilibrées après le bloc
        (
            "- Texte (source : code-civil, art. 1231-3) ; voir aussi (hors corpus).",
            [("code-civil", "1231-3")],
        ),
        # deux blocs, le second fini par un sous-point : le premier n'est pas fermé
        # trop tôt, la parenthèse restée sans ouvrante est celle du second
        (
            (
                "- A (source : code-civil, art. 1231-3) et B (source : sti-materiel-roulant, "
                "point 4.2.10.2.1, 2))."
            ),
            [("code-civil", "1231-3"), ("sti-materiel-roulant", "4.2.10.2.1")],
        ),
    ],
    ids=["sous-point-final", "romain-final", "parentheses-apres", "deux-blocs"],
)
def test_bloc_ferme_sans_texte_non_lu_admis(line, cited):
    assert corpus.citations(line) == cited


def test_fiche_avec_un_bloc_ferme_par_un_sous_point_refusee(corpus_synthetique):
    replace_line(
        corpus_synthetique / "fiches" / "test.md",
        "(source : code-test, art. T1)",
        "(sources : code-test, art. T1, a) et b) ; code-test, art. T1)",
    )
    with pytest.raises(
        ValueError, match="fiche-test : entrée de citation hors de son bloc, fermé par"
    ):
        ingestion.load_fiches()


def test_fiche_avec_une_entree_de_citation_illisible_refusee(corpus_synthetique):
    replace_line(
        corpus_synthetique / "fiches" / "test.md",
        "(source : code-test, art. T1)",
        "(sources : code-test, art. T1 ; ailleurs)",
    )
    with pytest.raises(
        ValueError, match="fiche-test : entrée de citation illisible : 'ailleurs'"
    ):
        ingestion.load_fiches()


def entries_and_citations(fiches: list[corpus.Fiche]) -> tuple[int, int]:
    """Entrées des blocs de citation des fiches, et citations lues."""
    lines = [line for fiche in fiches for line in corpus.claim_lines(fiche.body)]
    entries = [
        entry
        for line in lines
        for block in re.findall(r"\(sources? : ([^)]+)\)", line)
        for entry in block.split(";")
    ]
    return len(entries), sum(len(corpus.citations(line)) for line in lines)


def test_entrees_de_citation_des_fiches_du_corpus_toutes_lues():
    # chaque entrée commence par une citation : aucune n'est ignorée en silence
    fiches = ingestion.load_fiches()
    entries, cited = entries_and_citations(fiches)
    assert entries and cited == entries
    # les 79 entrées des fiches du fournisseur, comptées le 20/10/2026, restent lues
    # malgré la garde des blocs fermés par un sous-point
    assert entries_and_citations([f for f in fiches if f.id in FICHES_FOURNISSEUR]) == (
        79,
        79,
    )


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
    validity, window = {}, {}
    for row in rows:
        validity.setdefault(row.source_id, set()).add(row.valid_until)
        window.setdefault(row.source_id, set()).add((row.valid_from, row.valid_until))
    # relue contre les deux versions de L441-10 (versions_verifiees)
    assert window["fiche-delais-paiement"] == {(date(2019, 4, 26), None)}
    # C. civ. 1231-5, version ouverte, en vigueur depuis le 01/10/2016
    assert window["fiche-penalites-execution"] == {(date(2016, 10, 1), None)}
    # articles sans dates (EUR-Lex)
    assert window["fiche-sous-traitance-rgpd"] == {(None, None)}
    # la plus tardive des versions citées : C. com. L442-1, en vigueur depuis le
    # 20/08/2026
    assert window["fiche-responsabilite-plafonds"] == {(date(2026, 8, 20), None)}
    assert validity["code-commerce"] >= {date(2027, 1, 1)}


# --- Corpus de la sous-traitance (spec des profils, « Corpus ») --------------------------

NOUVELLES_REFERENCES = {
    "CPI, art. L111-1",
    "CPI, art. L112-2",
    "CPI, art. L122-4",
    "CPI, art. L131-3",
    "C. civ., art. 1231",
    "C. civ., art. 1231-1",
    "C. assur., art. L124-3",
    "Règl. (UE) 1302/2014, annexe, point 4.2.10.2.1",
    "Règl. (UE) 748/2012, annexe I, point 21.A.307",
}
TITRES_SOUS_TRAITANCE = {
    "fiche-protection-design": "Protection du design",
    "fiche-certification-preuves": "Certification et preuves",
    "fiche-assurance-sous-traitant": "Assurance du sous-traitant",
    "fiche-delais-execution": "Délais d'exécution",
    "fiche-responsabilite-sous-traitance": "Responsabilité dans la sous-traitance",
    "fiche-penalites-retard-execution": "Pénalités de retard d'exécution",
}


def test_fenetres_des_fiches_sous_traitance():
    windows: dict[str, set] = {}
    for meta, _, _ in ingestion.pending_chunks(CONFIG.corpus.chunk_max_words):
        if meta["source_id"] in FICHES_SOUS_TRAITANCE:
            windows.setdefault(meta["source_id"], set()).add(
                (meta["valid_from"], meta["valid_until"])
            )
    assert windows == {
        fiche: {(start, None)} for fiche, start in FICHES_SOUS_TRAITANCE.items()
    }


def test_nouvelles_references_dans_le_dossier():
    # chaque nouvelle référence a son texte à la date d'analyse du jeu, donc au dossier
    texts = ingestion.reference_texts(date(2026, 9, 25))
    fiches = {
        f"Fiche projet : {title}": fiche
        for fiche, title in TITRES_SOUS_TRAITANCE.items()
    }
    assert NOUVELLES_REFERENCES | set(fiches) <= set(texts)
    for reference in NOUVELLES_REFERENCES | set(fiches):
        assert texts[reference]["texte"].strip(), reference
    assert {r: texts[r]["source"] for r in fiches} == fiches


def test_fiches_sous_traitance_rattachees_aux_types_propres_au_profil():
    # les types communs (révision de prix, délai de paiement) gardent leurs fiches
    fiches = {f.id: f for f in ingestion.load_fiches()}
    sous_traitance = set(profiles.spec("sous_traitance").kinds)
    for fiche_id, title in TITRES_SOUS_TRAITANCE.items():
        fiche = fiches[fiche_id]
        assert fiche.title == title
        assert set(fiche.kinds) <= sous_traitance - set(FOURNISSEUR.kinds), fiche_id
        # aucun article cité à plusieurs versions : rien à déclarer relu
        assert fiche.verified == {}, fiche_id
    clauses = {k for f in TITRES_SOUS_TRAITANCE for k in fiches[f].kinds}
    assert clauses == sous_traitance - {"revision_prix", "delai_paiement"}


def test_rattachements_elargis_a_la_sous_traitance():
    # spec, « Sources existantes, rattachements élargis » : les types du fournisseur
    # restent, en tête
    kinds = {a.reference: k for a, k in ingestion.articles()}
    responsabilite = [
        "responsabilite_acheteur",
        "responsabilite_fournisseur",
        "responsabilite_donneur_ordre",
        "responsabilite_sous_traitant",
    ]
    for article in ("1170", "1171", "1231-3"):
        assert kinds[f"C. civ., art. {article}"] == responsabilite
    assert kinds["C. civ., art. 1231-5"] == [
        "penalites_execution",
        "penalites_retard_execution",
    ]
    assert kinds["C. com., art. L441-10"] == ["delai_paiement"]
    assert kinds["C. mon. fin., art. L112-2"] == ["revision_prix"]


def test_nouvelles_sources_du_manifeste():
    manifest = ingestion.load_manifest()
    sources = manifest.sources
    assert {
        sid: (s["format"], s["citation"], manifest.unit(sid), s["file"])
        for sid, s in sources.items()
        if sid
        in {
            "code-propriete-intellectuelle",
            "code-assurances",
            "sti-materiel-roulant",
            "certification-navigabilite",
        }
    } == {
        "code-propriete-intellectuelle": ("legifrance", "CPI", "art.", "{article}.txt"),
        "code-assurances": ("legifrance", "C. assur.", "art.", "{article}.txt"),
        "sti-materiel-roulant": (
            "eurlex",
            "Règl. (UE) 1302/2014",
            "annexe, point",
            "point-{article}.txt",
        ),
        "certification-navigabilite": (
            "eurlex",
            "Règl. (UE) 748/2012",
            "annexe I, point",
            "point-{article}.txt",
        ),
    }
    # hiérarchies relues le 21/10/2026 : celles du Code civil, du CPI et du Code des
    # assurances ; lien de la version en vigueur de chaque nouvel article (spec)
    for sid in ("code-civil", "code-propriete-intellectuelle", "code-assurances"):
        assert sources[sid]["hierarchy_verified_at"] == date(2026, 10, 21), sid
    legiarti = {
        ("code-propriete-intellectuelle", "L111-1"): "LEGIARTI000042814694",
        ("code-propriete-intellectuelle", "L112-2"): "LEGIARTI000006278875",
        ("code-propriete-intellectuelle", "L122-4"): "LEGIARTI000006278911",
        ("code-propriete-intellectuelle", "L131-3"): "LEGIARTI000006278958",
        ("code-civil", "1231"): "LEGIARTI000032041443",
        ("code-civil", "1231-1"): "LEGIARTI000032010123",
        ("code-assurances", "L124-3"): "LEGIARTI000017735449",
    }
    for (sid, number), identifier in legiarti.items():
        assert sources[sid]["hierarchy"][number]["url"] == (
            f"https://www.legifrance.gouv.fr/codes/article_lc/{identifier}"
        ), number
    # intitulé en vigueur depuis le 01/07/2012, jamais l'intitulé abrogé
    assert (
        "Titre II : Règles relatives aux assurances de dommages"
        in sources["code-assurances"]["hierarchy"]["L124-3"]["levels"]
    )
    # aucune version suivante annoncée au 20/10/2026 (API) ; EUR-Lex n'en a jamais
    for sid in (
        "code-civil",
        "code-propriete-intellectuelle",
        "code-assurances",
        "sti-materiel-roulant",
        "certification-navigabilite",
    ):
        assert "later_versions" not in sources[sid], sid


def test_aucune_norme_payante_dans_le_corpus():
    # EN 45545-2 et les règles de l'OMI ne sont pas libres : jamais dans raw/
    for path in sorted(RAW.rglob("*.txt")):
        text = path.read_text(encoding="utf-8")
        assert "EN 45545" not in text, path
        assert "Organisation maritime internationale" not in text, path
        assert not re.search(r"\bOMI\b", text), path
    # dans une fiche, nommées seulement, dans « Comment le projet l'applique », sans
    # extrait entre guillemets
    named = []
    for fiche in ingestion.load_fiches():
        for section, lines in corpus.fiche_sections(fiche.body).items():
            for line in lines:
                if re.search(r"EN 45545|\bOMI\b|Organisation maritime", line):
                    assert section == corpus.APPLICATION_SECTION, (fiche.id, line)
                    assert "«" not in line and "»" not in line, (fiche.id, line)
                    named.append(fiche.id)
    assert "fiche-certification-preuves" in named
    assert set(named) == {"fiche-certification-preuves"}


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
        assert set(kinds) <= profiles.ALL_KINDS, (source, kinds)


def test_chaque_type_de_clause_a_au_moins_un_article_et_une_fiche():
    # types de tous les profils
    articles, fiches = declared()
    for kind in sorted(profiles.ALL_KINDS):
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
    manifest = ingestion.load_manifest()
    documented = {}
    for line in text.splitlines():
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) >= 4 and cells[0].startswith("`") and "`" in cells[-2]:
            kinds = [k.strip(" `") for k in cells[-2].split(",")]
            source = cells[0].strip("`")
            # « art. 4 », « L441-10 », « annexe, point 4.2.10.2.1 » : l'unité de la
            # source retirée
            key = (
                source
                if cells[0].startswith("`fiche-")
                else (source, cells[1].removeprefix(f"{manifest.unit(source)} "))
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
    r"^(Partie|Première partie|LIVRE|Livre|TITRE|Titre|Sous-titre|Chapitre|Section"
    r"|Sous-section) "
)


@pytest.mark.parametrize(
    "level,official",
    [
        ("Partie législative", True),
        ("Première partie : La propriété littéraire et artistique", True),
        ("Livre Ier : Le droit d'auteur", True),
        ("Chapitre IV : Les assurances de responsabilité.", True),
        ("première partie : en minuscules", False),
        ("Premièrepartie : collé", False),
        ("Annexe : hors des niveaux de Légifrance", False),
    ],
)
def test_motif_des_intitules(level, official):
    assert bool(LEVEL.match(level)) is official


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
            # un seul espace entre les mots, aucun en fin
            for level in entry["levels"]:
                assert level == " ".join(level.split()), (number, level)
            urls.append(entry["url"])
    # 9 articles du fournisseur, 7 de la sous-traitance
    assert len(urls) == len(set(urls)) == 16
