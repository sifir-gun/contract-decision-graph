"""Chaîne d'approvisionnement de l'image (phase Kubernetes, PR B ; ADR 005).

- Dependabot propose les mises à jour des images de base du Dockerfile, qui passent par la
  CI ; uv et l'image PostgreSQL restent mis à jour à la main.
- Toute action des workflows est épinglée par empreinte de commit, version en commentaire.
- `scripts/chaine.py` : outils dans leurs images officielles, figées par version et
  empreinte ; la signature de chaque image de base est vérifiée avant la construction, en
  CI comme dans `scripts/check.sh`.
"""

import importlib.util
import re
import subprocess
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = sorted((ROOT / ".github" / "workflows").glob("*.yml"))
PINNED_ACTION = re.compile(
    r"uses:\s*(?P<action>[\w.-]+/[\w./-]+)@(?P<sha>[0-9a-f]{40})\s+#\s+v\d+(\.\d+)*$"
)


def dependabot() -> list[dict]:
    text = (ROOT / ".github" / "dependabot.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)["updates"]


# --- Dependabot ---------------------------------------------------------------------------


def test_dependabot_suit_les_images_de_base_du_dockerfile():
    [docker] = [u for u in dependabot() if u["package-ecosystem"] == "docker"]
    assert docker["directory"] == "/"
    others = [u for u in dependabot() if u["package-ecosystem"] != "docker"]
    assert all(docker["schedule"] == u["schedule"] for u in others)  # lundi, 6 h
    assert docker["commit-message"]["prefix"] == "Image : "


def test_uv_reste_mis_a_jour_a_la_main():
    # uv : celui du poste, de la CI et de l'étape de construction, ensemble
    # (tests/test_image.py) ; l'étape de construction ne part pas dans l'image finale
    [docker] = [u for u in dependabot() if u["package-ecosystem"] == "docker"]
    assert {"dependency-name": "astral-sh/uv"} in docker["ignore"]


def test_image_postgres_hors_de_dependabot():
    # l'écosystème docker lit les Dockerfile et les manifestes Kubernetes (YAML avec
    # apiVersion et kind, dependabot-core) : docker-compose.yml n'en est pas un, et
    # l'écosystème docker-compose n'est pas configuré
    ecosystems = {u["package-ecosystem"] for u in dependabot()}
    assert "docker-compose" not in ecosystems
    for path in ROOT.glob("*.y*ml"):
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert not {"apiVersion", "kind"} <= set(document), path.name


# --- actions épinglées ------------------------------------------------------------------------


def test_actions_epinglees_par_empreinte_version_en_commentaire():
    assert WORKFLOWS
    for workflow in WORKFLOWS:
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(("uses:", "- uses:")):
                used = line.strip().removeprefix("- ")
                assert PINNED_ACTION.fullmatch(used), f"{workflow.name} : {used}"


# --- scripts/chaine.py : outils figés, bases vérifiées avant la construction -----------------

TOOL = re.compile(r"(?P<name>[^@\s]+):v(?P<version>\d+\.\d+\.\d+)@sha256:[0-9a-f]{64}")


def chaine():
    path = ROOT / "scripts" / "chaine.py"
    spec = importlib.util.spec_from_file_location("chaine", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version(image: str) -> tuple[int, ...]:
    match = TOOL.fullmatch(image)
    assert match, f"outil non figé par version et empreinte : {image}"
    return tuple(int(n) for n in match["version"].split("."))


def test_outils_figes_et_au_dela_des_failles_publiees():
    module = chaine()
    # avis de sécurité GitHub, relevés le 28/09/2026 (ADR 005)
    assert version(module.COSIGN) >= (3, 1, 3)  # GHSA-fx35-mq7g-6g98, haute
    assert version(module.SYFT) >= (1, 52, 0)  # GHSA-mw2c-m758-9v5q
    assert version(module.GRYPE) >= (0, 104, 1)  # GHSA-6gxw-85q2-q646, haute


def test_chaque_base_du_dockerfile_a_sa_verification():
    module = chaine()
    bases = module.bases(ROOT / "Dockerfile")
    assert [b.split("@")[0] for b in bases] == [
        "ghcr.io/astral-sh/uv:0.12.19-trixie-slim",
        "gcr.io/distroless/cc-debian13:nonroot",
    ]
    distroless = module.command(bases[1])
    assert distroless[:4] == ["docker", "run", "--rm", module.COSIGN]
    assert distroless[4:6] == ["verify", bases[1]]
    assert distroless[6:] == [
        "--certificate-identity",
        "keyless@distroless.iam.gserviceaccount.com",
        "--certificate-oidc-issuer",
        "https://accounts.google.com",
    ]
    assert module.command(bases[0]) == [
        "gh",
        "attestation",
        "verify",
        f"oci://{bases[0]}",
        "--owner",
        "astral-sh",
        "--signer-workflow",
        "astral-sh/uv/.github/workflows/publish-docker-image.yml",
    ]


def test_base_sans_verification_refusee():
    with pytest.raises(ValueError, match="docker.io/library/python"):
        chaine().command("docker.io/library/python:3.12-slim@sha256:" + "0" * 64)


def test_verification_echouee_arrete_avec_la_base_en_cause(tmp_path, capsys):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(
        "FROM gcr.io/distroless/cc-debian13:nonroot@sha256:" + "a" * 64 + "\n"
    )
    module = chaine()

    def refused(command, **_):
        return subprocess.CompletedProcess(command, 12, "", "no matching signatures")

    def accepted(command, **_):
        return subprocess.CompletedProcess(command, 0, "[]", "")

    assert module.verify_bases(dockerfile, run=refused) == 1
    err = capsys.readouterr().err
    assert "gcr.io/distroless/cc-debian13" in err and "no matching signatures" in err
    assert module.verify_bases(dockerfile, run=accepted) == 0


def test_bases_verifiees_avant_la_construction_en_ci_et_en_local():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    runs = [s.get("run", "") for s in workflow["jobs"]["image"]["steps"]]
    verify = "uv run --no-sync python scripts/chaine.py bases"
    build = next(i for i, r in enumerate(runs) if r.startswith("docker build"))
    assert runs.index(verify) < build
    [step] = [s for s in workflow["jobs"]["image"]["steps"] if s.get("run") == verify]
    assert step["env"]["GH_TOKEN"] == "${{ github.token }}"
    script = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8").splitlines()
    assert script.index(verify) < next(
        i for i, line in enumerate(script) if line.startswith("docker build")
    )


# --- inventaire (Syft) et scan (Grype), exceptions datées -------------------------------------

EXCEPTIONS = ROOT / "securite" / "exceptions-vulnerabilites.yaml"
TODAY = date(2026, 9, 28)


def ok(command, **_):
    return subprocess.CompletedProcess(command, 0, "", "")


class Recorder:
    def __init__(self, code=0):
        self.commands, self.code = [], code

    def __call__(self, command, **_):
        self.commands.append(command)
        return subprocess.CompletedProcess(command, self.code, "", "sortie de l'outil")


def test_inventaire_de_l_image_par_syft(tmp_path):
    module, run = chaine(), Recorder()
    assert module.inventory("cdg:verification", tmp_path, run=run) == 0
    save, syft = run.commands
    assert save == [
        "docker",
        "save",
        "cdg:verification",
        "-o",
        str(tmp_path / "image.tar"),
    ]
    assert syft == [
        "docker",
        "run",
        "--rm",
        "-v",
        f"{tmp_path}:/travail",
        module.SYFT,
        "docker-archive:/travail/image.tar",
        "-o",
        "syft-json=/travail/sbom.syft.json",
        "-o",
        "spdx-json=/travail/sbom.spdx.json",
        "-q",
    ]


def test_scan_par_grype_echec_sur_faille_haute_corrigeable(tmp_path):
    module, run = chaine(), Recorder()
    assert module.scan(tmp_path, EXCEPTIONS, today=TODAY, run=run) == 0
    [grype] = run.commands
    assert grype[:4] == ["docker", "run", "--rm", "-v"]
    assert module.GRYPE in grype
    tail = grype[grype.index(module.GRYPE) + 1 :]
    assert tail[0] == "sbom:/travail/sbom.syft.json"
    options = " ".join(tail[1:])
    assert "--only-fixed --fail-on high" in options
    assert "-c /travail/grype.yaml" in options
    assert "-o json=/travail/grype.json" in options
    assert "cdg-grype-db:/cache" in grype  # base de failles gardée entre deux scans


def test_scan_rend_1_si_grype_trouve_une_faille(tmp_path, capsys):
    module = chaine()
    assert module.scan(tmp_path, EXCEPTIONS, today=TODAY, run=Recorder(code=2)) == 1
    assert "critique ou haute" in capsys.readouterr().err
    assert module.scan(tmp_path, EXCEPTIONS, today=TODAY, run=Recorder(code=1)) == 1


def test_exceptions_justifiees_datees_et_passees_a_grype(tmp_path):
    module = chaine()
    module.scan(tmp_path, EXCEPTIONS, today=TODAY, run=ok)
    config = yaml.safe_load((tmp_path / "grype.yaml").read_text(encoding="utf-8"))
    [rule] = config["ignore"]
    assert rule["vulnerability"] == "CVE-2026-82049"
    assert rule["package"] == {"name": "python", "type": "binary", "version": "3.12.14"}
    assert "157454" in rule["reason"]  # report sur 3.12 non fusionné


def exceptions(tmp_path, **changes) -> Path:
    entry = {
        "vulnerabilite": "CVE-2026-1",
        "paquet": {"nom": "zlib1g", "type": "deb", "version": "1.3"},
        "motif": "justification",
        "decidee": date(2026, 9, 1),
        "expire": date(2026, 10, 1),
    } | changes
    path = tmp_path / "exceptions.yaml"
    path.write_text(yaml.safe_dump({"exceptions": [entry]}), encoding="utf-8")
    return path


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"expire": date(2026, 9, 27)}, "expirée le 2026-09-27"),
        ({"motif": ""}, "motif"),
        ({"expire": date(2027, 1, 1)}, "90 jours"),
        ({"paquet": {"nom": "zlib1g", "type": "deb"}}, "version"),
    ],
)
def test_exception_expiree_ou_incomplete_refusee(tmp_path, changes, message):
    module = chaine()
    with pytest.raises(ValueError, match=message):
        module.scan(tmp_path, exceptions(tmp_path, **changes), today=TODAY, run=ok)


def test_inventaire_et_scan_apres_la_construction_en_ci_et_en_local():
    workflow = yaml.safe_load(
        (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    )
    job = workflow["jobs"]["image"]
    runs = [s.get("run", "") for s in job["steps"]]
    inventory = (
        "uv run --no-sync python scripts/chaine.py inventaire cdg:verification "
        '--dossier "$RUNNER_TEMP/chaine"'
    )
    scan = (
        'uv run --no-sync python scripts/chaine.py scan --dossier "$RUNNER_TEMP/chaine"'
    )
    tests = "uv run --no-sync pytest -m image --image cdg:verification"
    assert runs.index(tests) < runs.index(inventory) < runs.index(scan)
    script = (ROOT / "scripts" / "check.sh").read_text(encoding="utf-8")
    assert inventory in script and scan in script
    # chaque lundi aussi : une faille publiée entre deux commits, une exception expirée
    assert "schedule" not in job.get("if", "")


# --- publication : ghcr.io par empreinte, signature sans clé, attestations --------------------

IMAGE = "ghcr.io/${{ github.repository }}"
IDENTITY = "https://github.com/${{ github.repository }}/.github/workflows/ci.yml@refs/heads/main"


def ci() -> dict:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    return yaml.safe_load(text)


def publication_steps() -> list[dict]:
    return ci()["jobs"]["publication"]["steps"]


def uses(step: dict, action: str) -> bool:
    return step.get("uses", "").startswith(f"{action}@")


def test_publication_apres_une_fusion_dans_main_seulement():
    job = ci()["jobs"]["publication"]
    assert job["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    others = set(ci()["jobs"]) - {"publication"}
    assert set(job["needs"]) == others  # tout est vert avant de publier


def test_publication_droits_minimaux():
    assert ci()["permissions"] == {"contents": "read"}
    assert ci()["jobs"]["publication"]["permissions"] == {
        "contents": "read",
        "packages": "write",  # pousser l'image sur ghcr.io
        "id-token": "write",  # identité OIDC : signature sans clé, attestations
        "attestations": "write",  # attestations de provenance et d'inventaire
    }
    for name, job in ci()["jobs"].items():
        if name != "publication":
            assert "permissions" not in job, name


def test_l_image_publiee_est_celle_testee_et_scannee():
    image_steps = ci()["jobs"]["image"]["steps"]
    [upload] = [s for s in image_steps if uses(s, "actions/upload-artifact")]
    assert (
        upload["if"] == "github.event_name == 'push' && github.ref == 'refs/heads/main'"
    )
    assert upload["with"]["name"] == "image"
    assert upload["with"]["path"].splitlines() == [
        "${{ runner.temp }}/chaine/image.tar",
        "${{ runner.temp }}/chaine/sbom.spdx.json",
    ]
    assert upload["with"]["if-no-files-found"] == "error"
    steps = publication_steps()
    [download] = [s for s in steps if uses(s, "actions/download-artifact")]
    assert download["with"]["name"] == "image"
    runs = " ".join(s.get("run", "") for s in steps)
    assert 'docker load --input "$RUNNER_TEMP/chaine/image.tar"' in runs
    assert not re.search(r"docker build\s", runs)  # jamais reconstruite


def test_signature_et_attestations_par_empreinte():
    steps = publication_steps()
    [installer] = [s for s in steps if uses(s, "sigstore/cosign-installer")]
    cosign = re.search(r":(v[\d.]+)@", chaine().COSIGN)[1]
    assert installer["with"]["cosign-release"] == cosign  # le cosign des vérifications
    runs = "\n".join(s.get("run", "") for s in steps)
    assert 'cosign sign --yes "$IMAGE@$DIGEST"' in runs
    attests = [s for s in steps if uses(s, "actions/attest")]
    assert len(attests) == 2
    for attest in attests:
        assert attest["with"]["subject-name"] == "${{ env.IMAGE }}"
        assert attest["with"]["subject-digest"] == "${{ env.DIGEST }}"
        assert attest["with"]["push-to-registry"] is True
        # traces de stockage : dépôts d'organisation seulement (actions/attest 4.2.2)
        assert attest["with"]["create-storage-record"] is False
    sbom = [a["with"].get("sbom-path") for a in attests]
    assert sbom == [None, "${{ runner.temp }}/chaine/sbom.spdx.json"]


def test_ce_qui_est_publie_est_verifie():
    steps = publication_steps()
    [check] = [s for s in steps if "cosign verify" in s.get("run", "")]
    run = " ".join(check["run"].split())
    assert '--certificate-identity "$IDENTITY"' in run
    assert (
        "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in run
    )
    assert check["env"]["IDENTITY"] == IDENTITY
    assert run.count("gh attestation verify") == 2
    assert "--predicate-type https://spdx.dev/Document/v2.3" in run
    assert '--signer-workflow "$WORKFLOW"' in run


def test_aucune_expression_dans_les_scripts_de_publication():
    # injection de script : les valeurs passent par l'environnement, jamais dans `run`
    for step in publication_steps():
        assert "${{" not in step.get("run", ""), step.get("name")
    assert ci()["jobs"]["publication"]["env"]["IMAGE"] == IMAGE
