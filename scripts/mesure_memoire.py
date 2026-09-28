"""Mémoire d'un réplica (ADR 005, dimensionnement du chart) : mesure reproductible sur le
poste, à partir des images construites et de la base locale.

  uv run python scripts/mesure_memoire.py --image cdg:verification \\
      --modele cdg-modele:verification

Trois mesures, chacune dans un conteneur lancé comme dans un pod (lecture seule, sans
privilège), relevées dans son cgroup (`memory.current`, `memory.peak`, `memory.stat`) :

1. réplica réel prêt : `web`, modèle chargé (sonde de démarrage à 200), base joignable ;
2. calcul des embeddings : un processus qui charge le modèle puis calcule des embeddings
   de requêtes et de passages, comme la recherche et l'ingestion ; l'écart entre le pic et
   la mémoire après chargement est ce qu'une analyse ajoute au réplica ;
3. réplica de démonstration prêt : `web --demo`, ni modèle ni base.

Les poids viennent d'un volume rempli depuis l'image du modèle, monté en lecture seule
comme le volume `image` d'un pod. Les mots de passe passent par l'environnement du
processus Docker, jamais par la ligne de commande. Résultat : JSON sur la sortie standard,
en Mio.
"""

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = (".dockerenv", "dev/", "etc/", "proc/")
HARDENED = ["--read-only", "--tmpfs", "/tmp", "--cap-drop", "ALL"]
HARDENED += ["--security-opt", "no-new-privileges"]
MIB = 1024 * 1024
EXTRACT = f"""
import sys, tarfile
with tarfile.open(fileobj=sys.stdin.buffer, mode="r|") as archive:
    for member in archive:
        if not member.name.startswith({RUNTIME!r}):
            archive.extract(member, "/modele", filter="tar")
"""
CGROUP = """
import json
stat = dict(line.split() for line in open("/sys/fs/cgroup/memory.stat"))
print(json.dumps({
    "courante": int(open("/sys/fs/cgroup/memory.current").read()),
    "pic": int(open("/sys/fs/cgroup/memory.peak").read()),
    "anonyme": int(stat["anon"]),
    "fichiers": int(stat["file"]),
    "cpu_ms": int(dict(l.split() for l in open("/sys/fs/cgroup/cpu.stat"))["usage_usec"]) // 1000,
}))
"""
EMBEDDINGS = """
import json
from pathlib import Path
from cdg.adapters.fastembed import FastembedEmbedder
from cdg.domain.config import load_config
def cgroup():
    stat = dict(line.split() for line in open("/sys/fs/cgroup/memory.stat"))
    return {"courante": int(open("/sys/fs/cgroup/memory.current").read()),
            "pic": int(open("/sys/fs/cgroup/memory.peak").read()),
            "anonyme": int(stat["anon"]), "fichiers": int(stat["file"]),
            "cpu_ms": int(dict(l.split() for l in open("/sys/fs/cgroup/cpu.stat"))["usage_usec"]) // 1000}
embedder = FastembedEmbedder(load_config().embedding, Path("/modele"))
loaded = cgroup()
query = "plafond de responsabilité du prestataire en cas de faute lourde " * 8
for _ in range(40):  # recherche d'une analyse : des requêtes, une à une
    embedder.embed_query(query)
queries = cgroup()
passage = "Le prestataire répond des dommages directs dans la limite du prix. " * 30
embedder.embed_passages([passage] * 16)  # ingestion : un lot de passages longs
print(json.dumps({"apres_chargement": loaded, "apres_requetes": queries,
                  "apres_passages": cgroup()}))
"""


def docker(*args: str, **kwargs) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, check=False, **kwargs
    )


def fill_volume(app: str, weights: str) -> str:
    name = f"cdg-mesure-{uuid.uuid4().hex[:12]}"
    created = docker("create", weights, "absent").stdout.strip()
    try:
        export = subprocess.Popen(["docker", "export", created], stdout=subprocess.PIPE)
        extract = subprocess.run(
            ["docker", "run", "--rm", "-i", "--user", "0", "--network", "none"]
            + ["-v", f"{name}:/modele", "--entrypoint", "python", app, "-c", EXTRACT],
            stdin=export.stdout,
            capture_output=True,
            text=True,
            check=False,
        )
        if export.wait() != 0 or extract.returncode != 0:
            raise RuntimeError(f"volume du modèle non rempli : {extract.stderr[-500:]}")
    finally:
        docker("rm", created)
    return name


def mib(values: dict[str, int]) -> dict[str, int]:
    """Octets en Mio ; le temps CPU (cpu_ms) reste en millisecondes."""
    return {k: v if k == "cpu_ms" else round(v / MIB) for k, v in values.items()}


def ready_replica(
    app: str, extra: list[str], web: list[str], env: dict[str, str]
) -> dict:
    """Lance `web` (options Docker `extra`, options de `web` `web`), attend la sonde de
    démarrage, rend le cgroup."""
    started = docker(
        "run",
        "--detach",
        *HARDENED,
        "--publish",
        "127.0.0.1::8081",
        *extra,
        app,
        "web",
        *web,
        "--port-sante",
        "8081",
        "--hote-sante",
        "0.0.0.0",
        env=env,
    )
    if started.returncode != 0:
        raise RuntimeError(f"réplica non lancé : {started.stderr[-500:]}")
    container = started.stdout.strip()
    try:
        address = docker("port", container, "8081/tcp").stdout.splitlines()[0]
        begin, deadline = time.monotonic(), time.monotonic() + 300
        while time.monotonic() < deadline:
            try:
                url = f"http://{address}/sante/demarrage"
                if httpx.get(url, timeout=2).status_code == 200:
                    break
            except httpx.TransportError:
                pass
            time.sleep(1)
        else:
            raise RuntimeError(
                f"réplica jamais démarré : {docker('logs', container).stdout[-500:]}"
            )
        seconds = round(time.monotonic() - begin)
        time.sleep(5)  # mémoire stabilisée après le chargement
        ready = httpx.get(f"http://{address}/sante/pret", timeout=5).status_code
        measured = json.loads(docker("exec", container, "python", "-c", CGROUP).stdout)
        return {"demarrage_s": seconds, "pret": ready == 200, **mib(measured)}
    finally:
        docker("rm", "--force", container)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--image", required=True)
    parser.add_argument("--modele", required=True)
    args = parser.parse_args(argv)
    values = dotenv_values(ROOT / ".env")
    env = dict(os.environ)
    for name in ("POSTGRES_PORT", "POSTGRES_DB", "APP_DB_PASSWORD"):
        env[name] = os.environ.get(name) or values.get(name) or ""
    env["MISTRAL_API_KEY"] = "cle-fictive-de-mesure"  # aucun appel pendant la mesure
    volume = fill_volume(args.image, args.modele)
    try:
        real = ready_replica(
            args.image,
            ["-v", f"{volume}:/modele:ro", "-e", "EMBEDDING_CACHE_DIR=/modele"]
            + ["-e", "POSTGRES_HOST=host.docker.internal", "-e", "POSTGRES_PORT"]
            + ["-e", "POSTGRES_DB", "-e", "APP_DB_PASSWORD", "-e", "MISTRAL_API_KEY"],
            [],
            env,
        )
        print("réplica réel prêt :", real, file=sys.stderr)
        computed = docker(
            "run",
            "--rm",
            *HARDENED,
            "--network",
            "none",
            "-v",
            f"{volume}:/modele:ro",
            "-e",
            "EMBEDDING_CACHE_DIR=/modele",
            "--entrypoint",
            "python",
            args.image,
            "-c",
            EMBEDDINGS,
        )
        if computed.returncode != 0:
            raise RuntimeError(f"calcul des embeddings : {computed.stderr[-800:]}")
        embeddings = json.loads(computed.stdout.splitlines()[-1])
        print("embeddings :", embeddings, file=sys.stderr)
        demo = ready_replica(args.image, [], ["--demo"], env)
    finally:
        docker("volume", "rm", "--force", volume)
    loaded = embeddings["apres_chargement"]
    queries, passages = embeddings["apres_requetes"], embeddings["apres_passages"]
    print(
        json.dumps(
            {
                "replica_reel_pret": real,
                "embeddings": {
                    "apres_chargement": mib(loaded),
                    "apres_requetes": mib(queries),
                    "apres_passages": mib(passages),
                    "ajout_des_requetes": round(
                        (queries["pic"] - loaded["courante"]) / MIB
                    ),
                    "ajout_des_passages": round(
                        (passages["pic"] - loaded["courante"]) / MIB
                    ),
                },
                "replica_demo_pret": demo,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
