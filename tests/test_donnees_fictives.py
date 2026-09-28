"""Données fictives réservées (J5, décision du 26/09) : aucun courriel ni numéro de
téléphone du dépôt ne peut appartenir à quelqu'un.

- Courriels : domaines réservés par la RFC 2606 (`.test`, `.example`, `.invalid`,
  `.localhost` ; `example.com`, `example.net`, `example.org`).
- Téléphones : blocs réservés aux œuvres audiovisuelles par l'Arcep (plan national de
  numérotation, annexe de la décision n° 2018-0881 modifiée, version du 1er janvier 2026,
  « Numéros pour œuvres audiovisuelles ») : ces numéros ne peuvent ni appeler ni être
  appelés.

L'IBAN d'exemple de P2 reste (décision du 26/09) : voir `attendus.yaml`.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCANNED = [
    *ROOT.glob("data/contracts/*"),
    *ROOT.glob("data/corpus/fiches/*.md"),
    *ROOT.glob("tests/**/*.py"),
    *ROOT.glob("tests/fixtures/*"),
    *ROOT.glob("src/**/*.py"),
    *ROOT.glob("src/**/*.md"),
    *ROOT.glob("config/*.yaml"),
    ROOT / "README.md",
    ROOT / "docs" / "spec-phase1.md",
    ROOT / "docs" / "journal.md",
]
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})")
# même forme que le masquage (domain/masking.py) : national, +33 ou 0033
PHONE = re.compile(r"(?<![\d+])(?:(?:\+|00)33[ .-]?|0)[1-9](?:[ .-]?\d{2}){4}(?!\d)")
RESERVED_TLDS = (".test", ".example", ".invalid", ".localhost")
RESERVED_DOMAINS = ("example.com", "example.net", "example.org")
# identités publiques de signature, au format d'une adresse sans en être une : le compte de
# service qui signe les images distroless, publié par le projet (ADR 005, PR B)
SIGNING_DOMAINS = ("distroless.iam.gserviceaccount.com",)
AUDIOVISUAL_ROOTS = ("019900", "026191", "035301", "046571", "053649", "063998")


def found(pattern):
    return {
        (path.relative_to(ROOT).as_posix(), match)
        for path in SCANNED
        for match in pattern.findall(path.read_text(encoding="utf-8"))
    }


def national(phone: str) -> str:
    digits = re.sub(r"\D", "", phone)
    return "0" + digits.removeprefix("0033").removeprefix("33")[-9:]


def reserved_domain(domain: str) -> bool:
    domain = domain.lower()
    return domain.endswith(RESERVED_TLDS) or any(
        domain == d or domain.endswith("." + d) for d in RESERVED_DOMAINS
    )


def test_les_fichiers_examines_existent():
    assert all(path.is_file() for path in SCANNED)
    assert len(SCANNED) > 50


def test_courriels_sur_des_domaines_reserves():
    emails = found(EMAIL)
    assert emails, "aucun courriel trouvé : le motif ne voit plus rien"
    others = [e for e in emails if not reserved_domain(e[1])]
    assert [e for e in others if e[1] not in SIGNING_DOMAINS] == []


def test_identites_de_signature_toujours_utilisees():
    # une exception qui ne sert plus disparaît
    assert {domain for _, domain in found(EMAIL)} >= set(SIGNING_DOMAINS)


def test_telephones_dans_les_blocs_reserves_aux_oeuvres_audiovisuelles():
    phones = found(PHONE)
    assert phones, "aucun numéro trouvé : le motif ne voit plus rien"
    assert [p for p in phones if not national(p[1]).startswith(AUDIOVISUAL_ROOTS)] == []


def test_normalisation_des_numeros():
    assert national("+33 6 39 98 12 34") == "0639981234"
    assert national("0033 1 99 00 12 34") == "0199001234"
    assert national("01.99.00.12.34") == "0199001234"
