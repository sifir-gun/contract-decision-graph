"""Langfuse sur le poste (ADR 008, PR 2) : `compose.observabilite.yaml`, à part de
`docker-compose.yml` (docker compose exige les variables d'un profil même inactif : un
profil dans le fichier de la base obligerait à poser les secrets de Langfuse pour lancer
la base seule, ou à leur donner des valeurs vides).

Garanties : images figées par empreinte, les mêmes que dans le cluster de test (base du
projet, SeaweedFS) ; partie libre de Langfuse seule ; télémétrie, assistant et inscription
coupés ; aucun secret par défaut ; seule l'interface de Langfuse publiée, sur le poste ;
magasins de données sur un réseau interne, sans sortie.
"""

import importlib.util
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.observabilite.yaml"
DIGEST = re.compile(r"^[a-z0-9.-]+(:\d+)?(/[a-z0-9._-]+)+:[\w.-]+@sha256:[0-9a-f]{64}$")
SECRET = re.compile(r"(PASSWORD|SECRET|SALT|ENCRYPTION_KEY|REDIS_AUTH|ACCESS_KEY)")
LANGFUSE = {"langfuse-web", "langfuse-worker"}
STORES = {"langfuse-postgres", "langfuse-clickhouse", "langfuse-valkey"}


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def services() -> dict[str, dict]:
    return compose()["services"]


def cluster_script():
    spec = importlib.util.spec_from_file_location(
        "cluster_script", ROOT / "scripts" / "cluster.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_projet_a_part_et_services_attendus():
    assert compose()["name"] == "cdg-observabilite"
    assert set(services()) == {
        *LANGFUSE,
        *STORES,
        "langfuse-seaweedfs",
        "langfuse-seau",
    }


def test_images_figees_et_communes_au_projet():
    images = {name: s["image"] for name, s in services().items()}
    for name, image in images.items():
        assert DIGEST.match(image), name
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert images["langfuse-postgres"] == base["services"]["db"]["image"].replace(
        "pgvector/", "docker.io/pgvector/"
    )
    seaweedfs = cluster_script().SEAWEEDFS
    assert images["langfuse-seaweedfs"] == images["langfuse-seau"] == seaweedfs
    assert images["langfuse-web"].startswith("docker.io/langfuse/langfuse:4.50.0@")
    assert images["langfuse-worker"].startswith(
        "docker.io/langfuse/langfuse-worker:4.50.0@"
    )


def test_langfuse_sans_licence_commerciale_ni_appel_sortant():
    """Partie libre seule (MIT hors `ee/`) : aucune clé de licence ; télémétrie et
    assistant coupés, inscription fermée, aucun mode cloud ni fournisseur d'IA."""
    assert "LANGFUSE_EE_LICENSE_KEY" not in COMPOSE.read_text(encoding="utf-8")
    for name in LANGFUSE:
        variables = services()[name]["environment"]
        assert variables["TELEMETRY_ENABLED"] == "false", name
        assert variables["LANGFUSE_IN_APP_AGENT_ENABLED"] == "false", name
        assert variables["CLICKHOUSE_CLUSTER_ENABLED"] == "false", name
        assert not [v for v in variables if v.startswith("LANGFUSE_AI_")], name
        assert "NEXT_PUBLIC_LANGFUSE_CLOUD_REGION" not in variables, name
        assert variables["LANGFUSE_S3_EVENT_UPLOAD_ENDPOINT"] == (
            "http://langfuse-seaweedfs:8333"
        )
    assert services()["langfuse-web"]["environment"]["AUTH_DISABLE_SIGNUP"] == "true"


def test_secrets_exiges_jamais_par_defaut():
    """Chaque secret vient de `.env` et doit y être : `${NOM:?…}`, jamais de valeur par
    défaut ni de valeur en clair."""
    found = 0
    for name, service in services().items():
        for key, value in (service.get("environment") or {}).items():
            if SECRET.search(key) and "_ID" not in key.removesuffix("_ACCESS_KEY_ID"):
                found += 1
                assert re.fullmatch(r"\$\{[A-Z0-9_]+:\?[^}]+\}", str(value)), (
                    name,
                    key,
                )
    assert found >= 10


def test_seule_l_interface_publiee_sur_le_poste():
    published = {name: s["ports"] for name, s in services().items() if s.get("ports")}
    assert published == {"langfuse-web": ["127.0.0.1:3100:3000"]}


def test_magasins_sur_un_reseau_interne_sans_sortie():
    networks = compose()["networks"]
    assert networks["interne"] == {"internal": True}
    for name, service in services().items():
        attached = set(service["networks"])
        if name == "langfuse-web":
            assert attached == {"interne", "poste"}
        else:
            assert attached == {"interne"}, name


def test_valkey_et_clickhouse_sans_root():
    assert services()["langfuse-valkey"]["user"] == "999:999"
    assert services()["langfuse-clickhouse"]["user"] == "101:101"


def test_web_ecoute_sur_toutes_les_adresses_du_conteneur():
    """Next.js écoute sur $HOSTNAME, que Docker pose au nom du conteneur : la sonde
    (127.0.0.1) ne le joindrait pas."""
    assert services()["langfuse-web"]["environment"]["HOSTNAME"] == "0.0.0.0"


def test_interface_publiee_sans_sortie_vers_internet():
    """Langfuse web vérifie ses mises à jour auprès de langfuse.com à chaque page
    authentifiée (`checkUpdate`, hors mode cloud, non réglable). Seul service sur un
    réseau publié, il n'en sort pas : réseau sans traduction d'adresse (le port publié
    reste joignable du poste), et aucun résolveur pour les noms d'Internet."""
    networks = compose()["networks"]
    assert networks["poste"] == {
        "driver_opts": {"com.docker.network.bridge.enable_ip_masquerade": "false"}
    }
    assert services()["langfuse-web"]["dns"] == ["127.0.0.1"]
