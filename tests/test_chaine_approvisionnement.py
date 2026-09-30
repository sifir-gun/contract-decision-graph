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


def test_une_etape_du_meme_dockerfile_n_est_pas_une_base(tmp_path):
    """`FROM etape` reprend une étape précédente, y compris choisie par une variable
    (`go-${TARGETARCH}`) : seules les images sont des bases, chacune une fois."""
    dockerfile = tmp_path / "Dockerfile"
    uv = "ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:" + "a" * 64
    static = "gcr.io/distroless/static-debian13:nonroot@sha256:" + "b" * 64
    dockerfile.write_text(
        "ARG TARGETARCH\n"
        f"FROM {uv} AS go-amd64\n"
        f"FROM --platform=$BUILDPLATFORM {uv} AS go-arm64\n"
        "FROM go-${TARGETARCH} AS construction\n"
        "FROM construction AS verification\n"
        f"FROM {static}\n"
    )
    assert chaine().bases(dockerfile) == [uv, static]


def test_base_choisie_par_une_variable_hors_des_etapes_refusee(tmp_path):
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("ARG BASE\nFROM ${BASE}\n")
    with pytest.raises(ValueError, match="BASE"):
        chaine().bases(dockerfile)


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


DISTROLESS = "gcr.io/distroless/cc-debian13:nonroot@sha256:" + "a" * 64
UV = "ghcr.io/astral-sh/uv:0.12.19-trixie-slim@sha256:" + "b" * 64
# messages d'échec des outils de vérification : un problème d'environnement, reconnu, ne
# dit rien de la signature ; toute autre cause est une signature invalide
UNVERIFIABLE = {
    # gh attestation verify, 29/09/2026 : stockage des attestations injoignable
    "réseau (DNS)": "Error: failed to fetch bundle with URL: request to fetch bundle from"
    ' URL failed: Get "https://tmaproduction.blob.core.windows.net/attestations/1.json":'
    " dial tcp: lookup tmaproduction.blob.core.windows.net: no such host",
    "réseau (délai)": 'Error: Get "https://api.github.com/orgs/astral-sh/attestations":'
    " dial tcp 140.82.121.6:443: i/o timeout",
    "réseau (TLS)": 'Error: getting signatures: Get "https://ghcr.io/v2/": net/http: TLS'
    " handshake timeout",
    "service indisponible": "HTTP 502: Bad Gateway (https://api.github.com/orgs/x)",
    "service indisponible (quota)": "Error: GET https://ghcr.io/v2/x: TOOMANYREQUESTS",
    "gh non authentifié": "To get started with GitHub CLI, please run:  gh auth login",
    "Docker indisponible": "docker: Cannot connect to the Docker daemon at"
    " unix:///var/run/docker.sock. Is the docker daemon running?",
}
INVALID = {
    "cosign : aucune signature attendue": "Error: no matching signatures: none of the"
    " expected identities matched what was in the certificate",
    "gh : aucune attestation": "Error: HTTP 404: Not Found (https://api.github.com/x)",
    "cause inconnue": "erreur inattendue de l'outil de vérification",
}


def verify_one(tmp_path, image: str, stderr: str) -> int:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text(f"FROM {image}\n")

    def failed(command, **_):
        return subprocess.CompletedProcess(command, 1, "", stderr)

    return chaine().verify_bases(dockerfile, run=failed)


@pytest.mark.parametrize("stderr", UNVERIFIABLE.values(), ids=UNVERIFIABLE.keys())
def test_verification_impossible_n_est_pas_une_signature_invalide(
    tmp_path, capsys, stderr
):
    """Le 29/09, un échec réseau s'est annoncé « signature refusée ». Une vérification
    impossible le dit, avec sa cause ; elle fait échouer la vérification comme une
    signature invalide, sans en être une."""
    assert verify_one(tmp_path, UV, stderr) == 1
    err = capsys.readouterr().err
    assert f"vérification impossible : {UV}" in err
    assert "signature invalide" not in err and "erreur de sécurité" not in err
    assert stderr in err  # le message de l'outil, tel quel
    assert (
        "bases non vérifiées : signatures invalides 0, vérifications impossibles 1"
        in err
    )


@pytest.mark.parametrize("stderr", INVALID.values(), ids=INVALID.keys())
def test_signature_invalide_erreur_de_securite_explicite(tmp_path, capsys, stderr):
    """Une signature qui ne correspond pas à l'identité attendue est une erreur de
    sécurité ; une cause non reconnue est traitée de même, le cas le plus prudent."""
    assert verify_one(tmp_path, DISTROLESS, stderr) == 1
    err = capsys.readouterr().err
    assert f"signature invalide : {DISTROLESS}" in err
    assert "erreur de sécurité" in err and "ne pas construire" in err
    assert "keyless@distroless.iam.gserviceaccount.com" in err  # l'identité attendue
    assert "vérification impossible" not in err
    assert stderr in err
    assert (
        "bases non vérifiées : signatures invalides 1, vérifications impossibles 0"
        in err
    )


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
    rules = {rule["vulnerability"]: rule for rule in config["ignore"]}
    rule = rules["CVE-2026-82049"]
    assert rule["package"] == {"name": "python", "type": "binary", "version": "3.12.14"}
    assert "157454" in rule["reason"]  # report sur 3.12 non fusionné
    # oauth2-proxy v7.15.4 (PR D1) : binaire publié, dépendances figées ; failles jugées
    # hors d'atteinte (aucun serveur gRPC, pas de SSH ; govulncheck)
    grpc = {"name": "google.golang.org/grpc", "type": "go-module", "version": "v1.83.0"}
    crypto = {"name": "golang.org/x/crypto", "type": "go-module", "version": "v0.55.0"}
    for name, package in (
        ("GHSA-2v4p-qf9q-27wj", grpc),
        ("GHSA-vp52-pcj8-j9qc", grpc),
        ("GO-2026-6354", crypto),
        ("GO-2026-6355", crypto),
    ):
        assert rules[name]["package"] == package
        assert "govulncheck" in rules[name]["reason"]
    assert len(rules) == 5


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

IMAGE = "ghcr.io/${{ github.repository }}${{ matrix.depot }}"
# images publiées : l'application et le proxy de sortie (PR C3), par la même chaîne
PUBLISHED = [
    {
        "image": "application",
        "artefact": "image",
        "locale": "cdg:verification",
        "depot": "",
    },
    {
        "image": "proxy de sortie",
        "artefact": "proxy",
        "locale": "cdg-proxy:verification",
        "depot": "/proxy-sortie",
    },
    {  # PR D1 : binaire de la release, vérifié par son empreinte publiée
        "image": "oauth2-proxy",
        "artefact": "oauth2-proxy",
        "locale": "cdg-oauth2-proxy:verification",
        "depot": "/oauth2-proxy",
    },
]
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


ARCHITECTURES = [
    {"arch": "amd64", "runner": "ubuntu-24.04"},
    {"arch": "arm64", "runner": "ubuntu-24.04-arm"},  # runner ARM de GitHub, natif
]
ONLY_MAIN = "github.event_name == 'push' && github.ref == 'refs/heads/main'"


def test_image_construite_et_verifiee_sur_chaque_architecture():
    job = ci()["jobs"]["image"]
    assert job["strategy"]["matrix"] == {"include": ARCHITECTURES}
    assert (
        job["strategy"]["fail-fast"] is False
    )  # une architecture n'éclipse pas l'autre
    assert job["runs-on"] == "${{ matrix.runner }}"
    assert "${{ matrix.arch }}" in job["name"]
    # construction native : ni émulation, ni plateforme forcée
    assert not [s for s in job["steps"] if "qemu" in s.get("uses", "")]
    builds = [
        s["run"] for s in job["steps"] if s.get("run", "").startswith("docker build")
    ]
    assert builds and not [b for b in builds if "--platform" in b]


def test_les_images_publiees_sont_celles_testees_et_scannees():
    image_steps = ci()["jobs"]["image"]["steps"]
    uploads = {
        s["with"]["name"]: s for s in image_steps if uses(s, "actions/upload-artifact")
    }
    assert set(uploads) == {
        "image-${{ matrix.arch }}",
        "proxy-${{ matrix.arch }}",
        "oauth2-proxy-${{ matrix.arch }}",
    }
    for folder, upload in (
        ("chaine", uploads["image-${{ matrix.arch }}"]),
        ("chaine-proxy", uploads["proxy-${{ matrix.arch }}"]),
        ("chaine-oauth2-proxy", uploads["oauth2-proxy-${{ matrix.arch }}"]),
    ):
        # toujours produit : le job cluster reprend les images testées (PR C3)
        assert "if" not in upload
        assert upload["with"]["path"].splitlines() == [
            f"${{{{ runner.temp }}}}/{folder}/image.tar",
            f"${{{{ runner.temp }}}}/{folder}/sbom.spdx.json",
        ]
        assert upload["with"]["if-no-files-found"] == "error"
    job = ci()["jobs"]["publication"]
    assert job["strategy"] == {"fail-fast": False, "matrix": {"include": PUBLISHED}}
    assert "${{ matrix.image }}" in job["name"]
    assert job["env"]["ARTEFACT"] == "${{ matrix.artefact }}"
    assert job["env"]["LOCALE"] == "${{ matrix.locale }}"
    steps = publication_steps()
    [download] = [s for s in steps if uses(s, "actions/download-artifact")]
    # un dossier par architecture
    assert download["with"]["pattern"] == "${{ matrix.artefact }}-*"
    runs = " ".join(s.get("run", "") for s in steps)
    assert 'docker load --input "$RUNNER_TEMP/chaine/$ARTEFACT-$arch/image.tar"' in runs
    assert 'docker tag "$LOCALE" "$IMAGE:sha-$GITHUB_SHA-$arch"' in runs
    assert "for arch in amd64 arm64" in runs
    assert not re.search(r"docker build\s", runs)  # jamais reconstruite


def test_proxy_de_sortie_verifie_comme_l_application_avant_son_artefact():
    runs = [s.get("run", "") for s in ci()["jobs"]["image"]["steps"]]
    proxy = "docker/proxy-sortie/Dockerfile"
    order = [
        f"uv run --no-sync python scripts/chaine.py bases --dockerfile {proxy}",
        f"docker build --file {proxy} --tag cdg-proxy:verification docker/proxy-sortie",
        (
            "uv run --no-sync pytest -m proxy --proxy cdg-proxy:verification "
            "--image cdg:verification"
        ),
        (
            "uv run --no-sync python scripts/chaine.py inventaire cdg-proxy:verification "
            '--dossier "$RUNNER_TEMP/chaine-proxy"'
        ),
        'uv run --no-sync python scripts/chaine.py scan --dossier "$RUNNER_TEMP/chaine-proxy"',
    ]
    flat = [" ".join(r.split()) for r in runs]
    positions = [flat.index(command) for command in order]
    assert positions == sorted(positions)


def test_index_multi_architecture_verifie():
    runs = "\n".join(s.get("run", "") for s in publication_steps())
    assert "docker buildx imagetools create" in runs
    assert '"$IMAGE@$DIGEST_AMD64" "$IMAGE@$DIGEST_ARM64"' in runs
    # l'index publié ne désigne que les deux architectures attendues
    assert 'test "$platforms" = "linux/amd64,linux/arm64"' in runs


def test_signature_et_attestations_par_empreinte():
    steps = publication_steps()
    [installer] = [s for s in steps if uses(s, "sigstore/cosign-installer")]
    cosign = re.search(r":(v[\d.]+)@", chaine().COSIGN)[1]
    assert installer["with"]["cosign-release"] == cosign  # le cosign des vérifications
    runs = "\n".join(s.get("run", "") for s in steps)
    # l'index et chaque image qu'il désigne
    assert 'cosign sign --yes --recursive "$IMAGE@$DIGEST"' in runs
    attests = [s for s in steps if uses(s, "actions/attest")]
    for attest in attests:
        assert attest["with"]["subject-name"] == "${{ env.IMAGE }}"
        assert attest["with"]["push-to-registry"] is True
        # traces de stockage : dépôts d'organisation seulement (actions/attest 4.2.2)
        assert attest["with"]["create-storage-record"] is False
    subjects = [
        (a["with"]["subject-digest"], a["with"].get("sbom-path")) for a in attests
    ]
    assert subjects == [
        ("${{ env.DIGEST }}", None),  # provenance : l'index
        (
            "${{ env.DIGEST_AMD64 }}",
            "${{ runner.temp }}/chaine/${{ matrix.artefact }}-amd64/sbom.spdx.json",
        ),
        (
            "${{ env.DIGEST_ARM64 }}",
            "${{ runner.temp }}/chaine/${{ matrix.artefact }}-arm64/sbom.spdx.json",
        ),
    ]


def test_ce_qui_est_publie_est_verifie():
    steps = publication_steps()
    [check] = [s for s in steps if "cosign verify" in s.get("run", "")]
    run = " ".join(check["run"].split())
    assert 'for digest in "$DIGEST" "$DIGEST_AMD64" "$DIGEST_ARM64"' in run
    assert '--certificate-identity "$IDENTITY"' in run
    assert (
        "--certificate-oidc-issuer https://token.actions.githubusercontent.com" in run
    )
    assert check["env"]["IDENTITY"] == IDENTITY
    assert 'gh attestation verify "oci://$IMAGE@$DIGEST"' in run
    assert 'for digest in "$DIGEST_AMD64" "$DIGEST_ARM64"' in run
    assert "--predicate-type https://spdx.dev/Document/v2.3" in run
    assert '--signer-workflow "$WORKFLOW"' in run


def test_aucune_expression_dans_les_scripts_de_publication():
    # injection de script : les valeurs passent par l'environnement, jamais dans `run`
    for step in publication_steps():
        assert "${{" not in step.get("run", ""), step.get("name")
    assert ci()["jobs"]["publication"]["env"]["IMAGE"] == IMAGE


# --- révision fournie à la construction (version du code scellée, PR D2) --------------------
#
# `chaine.py revision` donne le commit passé à l'image (CDG_COMMIT) : celui de HEAD si le
# contexte de construction est exactement celui du commit, sinon « inconnu », avec la raison
# sur la sortie d'erreur. Jamais un commit qui ne décrirait pas le code copié.

GIT_AUTHOR = ["-c", "user.name=test", "-c", "user.email=test@example.org"]


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *GIT_AUTHOR, "-c", "commit.gpgsign=false", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


@pytest.fixture
def depot(tmp_path):
    """Dépôt minimal : contexte de construction `src/` et `pyproject.toml`, comme le
    `.dockerignore` du projet (tout exclure, puis admettre ; caches exclus)."""
    repo = tmp_path / "depot"
    (repo / "src").mkdir(parents=True)
    (repo / "docs").mkdir()
    (repo / ".dockerignore").write_text(
        "# contexte\n*\n!src/\n!pyproject.toml\n**/__pycache__\n**/*.py[cod]\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("__pycache__/\n*.egg-info/\n", encoding="utf-8")
    (repo / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    (repo / "docs" / "notes.md").write_text("notes\n", encoding="utf-8")
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "initial")
    return repo


def test_revision_commit_complet_si_le_contexte_est_celui_du_commit(depot):
    head = git(depot, "rev-parse", "HEAD")
    assert len(head) == 40
    assert chaine().revision(depot) == head


def test_revision_hors_du_contexte_une_modification_ne_compte_pas(depot):
    (depot / "docs" / "notes.md").write_text("autre\n", encoding="utf-8")
    (depot / "docs" / "brouillon.md").write_text("pr\n", encoding="utf-8")
    assert chaine().revision(depot) == git(depot, "rev-parse", "HEAD")


def test_revision_caches_exclus_de_l_image_ne_comptent_pas(depot):
    cache = depot / "src" / "__pycache__"
    cache.mkdir()
    (cache / "a.cpython-312.pyc").write_bytes(b"\0")
    assert chaine().revision(depot) == git(depot, "rev-parse", "HEAD")


def _modifie(repo: Path) -> None:
    (repo / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")


def _ajoute(repo: Path) -> None:
    (repo / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")


def _supprime(repo: Path) -> None:
    (repo / "pyproject.toml").unlink()


def _ignore_mais_copie(repo: Path) -> None:
    # ignoré par git, mais admis dans le contexte : il entrerait dans l'image
    info = repo / "src" / "cdg.egg-info"
    info.mkdir()
    (info / "PKG-INFO").write_text("Name: cdg\n", encoding="utf-8")


@pytest.mark.parametrize("change", [_modifie, _ajoute, _supprime, _ignore_mais_copie])
def test_revision_inconnue_si_le_contexte_differe_du_commit(depot, capsys, change):
    change(depot)
    assert chaine().revision(depot) == "inconnu"
    err = capsys.readouterr().err
    assert "révision inconnue" in err and "contexte de construction" in err


def test_revision_inconnue_sans_commit(tmp_path, capsys):
    (tmp_path / ".dockerignore").write_text("*\n!src/\n", encoding="utf-8")
    git(tmp_path, "init", "-q")  # dépôt sans aucun commit
    assert chaine().revision(tmp_path) == "inconnu"
    assert "aucun commit" in capsys.readouterr().err


def test_revision_motif_du_contexte_non_pris_en_charge_refuse(depot):
    (depot / ".dockerignore").write_text("*\n!src/\nsrc/secret\n", encoding="utf-8")
    with pytest.raises(ValueError, match="src/secret"):
        chaine().revision(depot)


def test_revision_meme_valeur_que_le_modele_de_la_version_du_code():
    from cdg.domain.version import UNKNOWN

    assert chaine().UNKNOWN == UNKNOWN


def test_commande_revision_imprime_la_seule_revision(capsys):
    module = chaine()
    expected = module.revision(ROOT)
    capsys.readouterr()
    assert module.main(["revision"]) == 0
    assert capsys.readouterr().out == expected + "\n"
