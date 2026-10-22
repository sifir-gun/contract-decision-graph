"""Scan des images de test sans signature (ADR 008, décision du 04/10) : les images de la
composition de Langfuse, celles de l'exception de signatures, scannées à chaque pull
request par le job `cluster`, avant la création du cluster. Bloquant, comme pour le
produit, mais contre un fichier d'exceptions propre aux tests, qui ne couvre jamais une
image du produit. Chaque image est tirée, inventoriée, scannée, puis retirée si elle
n'était pas déjà là (le disque du runner sert ensuite au cluster). Toutes les images
passent avant l'échec, qui les nomme toutes (décision du 06/10).
"""

import subprocess
from datetime import date
from pathlib import Path

import yaml
from test_chaine_approvisionnement import chaine
from test_ci import ci_commands

ROOT = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 13)
COMMAND = 'uv run --no-sync python scripts/chaine.py images-de-test --dossier "$RUNNER_TEMP/images-de-test"'


class Runner:
    """Exécutant factice : note chaque commande ; une image est « présente » si son nom
    est dans `present`, un scan « échoue » sur les images de `failing`, un tirage sur
    celles de `unpullable`."""

    def __init__(self, present=(), failing=(), unpullable=()):
        self.commands: list[list[str]] = []
        self.present, self.failing = set(present), set(failing)
        self.unpullable = set(unpullable)
        self.scanned: list[str] = []

    def __call__(self, command, **kwargs):
        self.commands.append(command)
        if command[:3] == ["docker", "image", "inspect"]:
            code = 0 if command[3] in self.present else 1
            return subprocess.CompletedProcess(command, code, "", "")
        if "sbom:/travail/sbom.syft.json" in command:
            image = self.scanned[-1]
            code = 2 if image in self.failing else 0
            return subprocess.CompletedProcess(command, code, "", "")
        if command[:2] == ["docker", "pull"] and command[2] in self.unpullable:
            return subprocess.CompletedProcess(command, 1, "", "registre injoignable")
        if command[:2] == ["docker", "save"]:
            self.scanned.append(command[2])
        return subprocess.CompletedProcess(command, 0, "", "")


def images() -> list[str]:
    module = chaine()
    return sorted(module.signature_exceptions(module.SIGNATURE_EXCEPTIONS, TODAY))


def test_chaque_image_tiree_inventoriee_scannee_puis_retiree(tmp_path):
    module = chaine()
    runner = Runner(present={images()[0]})
    assert module.scan_test_images(tmp_path, today=TODAY, run=runner) == 0
    assert runner.scanned == images()
    pulled = [c[2] for c in runner.commands if c[:2] == ["docker", "pull"]]
    removed = [c[3] for c in runner.commands if c[:3] == ["docker", "image", "rm"]]
    # une image déjà présente n'est ni tirée ni retirée : seulement ce qui est créé
    assert pulled == removed == images()[1:]
    grype = [c for c in runner.commands if "sbom:/travail/sbom.syft.json" in c]
    assert len(grype) == len(images())


def test_faille_non_couverte_n_arrete_pas_les_autres_images(tmp_path, capsys):
    """Décision du 06/10 : la liste complète des images refusées, pas la première
    seulement (le 06/10, l'arrêt sur le worker de Langfuse laissait l'image web non
    scannée)."""
    module = chaine()
    first, *_, last = images()
    runner = Runner(failing={first, last})
    assert module.scan_test_images(tmp_path, today=TODAY, run=runner) == 1
    assert runner.scanned == images()
    err = capsys.readouterr().err
    assert f"image de test refusée : {first}" in err
    assert f"image de test refusée : {last}" in err
    assert err.rstrip().endswith(
        f"images de test refusées : 2 sur {len(images())} ({first}, {last})"
    )


def test_tirage_impossible_n_arrete_pas_les_autres_images(tmp_path, capsys):
    module = chaine()
    first = images()[0]
    runner = Runner(unpullable={first})
    assert module.scan_test_images(tmp_path, today=TODAY, run=runner) == 1
    assert runner.scanned == images()[1:]
    err = capsys.readouterr().err
    assert f"tirage impossible : {first}\nregistre injoignable" in err
    assert err.rstrip().endswith(
        f"images de test refusées : 1 sur {len(images())} ({first})"
    )


def test_exceptions_des_tests_a_part_justifiees_datees():
    """Les failles relevées le 04/10, revues le 13/10 avec Langfuse 4.58.0 (celle de
    source-map-js, du 06/10, sortie avec la mise à jour), celle de zlib dans Valkey
    (13/10), puis follow-redirects et le SDK MCP dans Langfuse (21/10), chacune justifiée
    et datée (90 jours au plus) ; lues par la même règle que celles du produit."""
    module = chaine()
    path = module.TEST_EXCEPTIONS
    rules = module.grype_config(path, TODAY)["ignore"]
    assert sorted((r["vulnerability"], r["package"]["name"]) for r in rules) == [
        ("CVE-2026-5450", "libc6"),
        ("CVE-2026-5928", "libc6"),
        ("CVE-2026-85091", "zlib"),
        ("GHSA-22jm-h49p-29qw", "@modelcontextprotocol/sdk"),
        ("GHSA-46jr-4wx8-v453", "@modelcontextprotocol/sdk"),
        ("GHSA-8r9p-f939-6h3c", "follow-redirects"),
        ("GHSA-ggr8-5vv4-36mx", "deepmerge-ts"),
        ("GHSA-q3jh-wrc6-jgq8", "follow-redirects"),
    ]
    assert path != module.EXCEPTIONS


def test_zlib_de_valkey_trente_jours_au_plus_jusqu_a_la_prochaine_image():
    """Décision du 13/10 : CVE-2026-85091 (zlib 1.3.2-r0, Valkey 8.1.10-alpine, image non
    republiée) admise 30 jours au plus ; valkey-server ne lie pas zlib (seul apk en
    dépend) ; elle sort avec la prochaine publication de l'image."""
    text = chaine().TEST_EXCEPTIONS.read_text(encoding="utf-8")
    [entry] = [
        e
        for e in yaml.safe_load(text)["exceptions"]
        if e["vulnerabilite"] == "CVE-2026-85091"
    ]
    assert entry["paquet"] == {"nom": "zlib", "type": "apk", "version": "1.3.2-r0"}
    assert (entry["expire"] - entry["decidee"]).days <= 30
    assert "prochaine publication" in " ".join(entry["motif"].split())


def test_follow_redirects_et_sdk_mcp_de_langfuse_trente_jours_au_plus():
    """Décision du 21/10 : les failles hautes publiées le 20/10 dans follow-redirects
    1.16.0 (corrigées en 1.16.1) et le SDK MCP 1.31.0 (corrigées en 1.32.0), que gardent
    Langfuse web et worker 4.58.0 comme la 4.67.0, admises 30 jours au plus ; elles
    sortent avec la première version de Langfuse qui embarque les deux correctifs."""
    attendues = {
        "GHSA-8r9p-f939-6h3c": ("follow-redirects", "1.16.0"),
        "GHSA-q3jh-wrc6-jgq8": ("follow-redirects", "1.16.0"),
        "GHSA-22jm-h49p-29qw": ("@modelcontextprotocol/sdk", "1.31.0"),
        "GHSA-46jr-4wx8-v453": ("@modelcontextprotocol/sdk", "1.31.0"),
    }
    text = chaine().TEST_EXCEPTIONS.read_text(encoding="utf-8")
    entries = {
        e["vulnerabilite"]: e
        for e in yaml.safe_load(text)["exceptions"]
        if e["vulnerabilite"] in attendues
    }
    assert sorted(entries) == sorted(attendues)
    for name, (package, version) in attendues.items():
        entry = entries[name]
        assert entry["paquet"] == {"nom": package, "type": "npm", "version": version}
        assert entry["decidee"] == date(2026, 10, 21)
        assert (entry["expire"] - entry["decidee"]).days <= 30
        motif = " ".join(entry["motif"].split())
        assert "première version de Langfuse" in motif, name
        assert "follow-redirects 1.16.1" in motif and "SDK MCP 1.32.0" in motif, name


def test_jamais_pour_le_produit_scanne_par_la_ci_avant_le_cluster():
    """Les scans du produit gardent leurs propres exceptions ; celles des tests ne servent
    qu'à cette commande, lancée par le job cluster avant la création du cluster."""
    commands = ci_commands()
    for command in commands:
        if "chaine.py scan" in command:
            assert "exceptions-vulnerabilites-tests" not in command, command
    assert COMMAND in commands
    create = commands.index("uv run --no-sync python scripts/cluster.py creer")
    assert commands.index(COMMAND) < create
    text = (ROOT / "securite" / "exceptions-vulnerabilites-tests.yaml").read_text(
        encoding="utf-8"
    )
    assert "tests" in yaml.safe_load(text)["portee"]
