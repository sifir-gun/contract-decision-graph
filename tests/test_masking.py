"""Masquage des données personnelles, avant toute entrée dans le graphe."""

import pytest

from cdg.domain import masking


@pytest.mark.parametrize(
    "text,placeholder",
    [
        ("Écrire à jeanne.martin@example.com pour toute question.", "[EMAIL]"),
        (
            "Joindre le service au 01 99 00 45 67 ou au +33 6 39 98 56 78.",
            "[TELEPHONE]",
        ),
        ("Paiement sur le compte FR76 3000 6000 0112 3456 7890 189.", "[IBAN]"),
        ("Société immatriculée sous le numéro 732 829 320.", "[SIREN]"),
        ("Établissement 732 829 320 00074 situé à Lyon.", "[SIRET]"),
    ],
)
def test_motifs_masques(text, placeholder):
    result = masking.mask(text)
    assert placeholder in result.text
    assert masking.residual_pii(result.text) == []


def test_montants_et_durees_non_masques():
    text = "Plafond de 150 000 000 euros, préavis de 3 mois, pénalités de 5 %."
    assert masking.mask(text).text == text


def test_numero_sans_cle_de_luhn_non_pris_pour_un_siren():
    assert (
        masking.mask("Référence interne 123 456 789.").text
        == "Référence interne 123 456 789."
    )


def test_noms_des_parties_declares_masques_sans_tenir_compte_de_la_casse():
    text = "Entre ACME Industrie et la société Beta Conseil ; acme industrie s'engage."
    result = masking.mask(text, parties=["Acme Industrie", "Beta Conseil"])
    assert result.text == (
        "Entre [PARTIE_1] et la société [PARTIE_2] ; [PARTIE_1] s'engage."
    )
    assert result.counts == {"PARTIE": 3}


def test_partie_masquee_seulement_en_mot_entier():
    assert (
        masking.mask("La société Betaville.", parties=["Beta"]).text
        == "La société Betaville."
    )


def test_comptes_par_type():
    result = masking.mask("a@b.example, c@d.example, 01 99 00 45 67")
    assert result.counts == {"EMAIL": 2, "TELEPHONE": 1}


def test_masquage_idempotent():
    once = masking.mask("x@y.example, 01 99 00 45 67").text
    assert masking.mask(once).text == once


def test_residus_detectes():
    assert masking.residual_pii("contact : x@y.example, 01 99 00 45 67") == [
        "EMAIL",
        "TELEPHONE",
    ]
