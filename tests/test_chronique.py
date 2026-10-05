"""Chronique du scénario de mise à jour (enquête du 05/10 : une requête perdue pendant la
mise à jour progressive, 2 fois sur 11 depuis le 03/10), vérifiée sans cluster : la sonde,
la lecture des flux de kubectl, des compteurs de refus et des ensembles d'adresses de
kube-router, et le rapport horodaté."""

import errno
import json
import socket
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from chronique import (
    SONDE,
    Evenement,
    compteurs_de_refus,
    etat_pod,
    membres,
    nouveaux_refus,
    objets_json,
    rapport,
    resume_tranche,
    transitions,
)

# --- la sonde, lancée ici comme dans son pod -------------------------------------------


def serveur(code: int, instance: str):
    class Sante(BaseHTTPRequestHandler):
        def do_GET(self):
            body = json.dumps({"statut": "pret", "instance": instance}).encode()
            self.send_response(code)
            self.send_header("content-type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Sante)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


def sonder(url: str) -> dict:
    result = subprocess.run(
        [sys.executable, "-c", SONDE.format(url=url, duree=1)],
        capture_output=True,
        text=True,
        timeout=30,
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def test_sonde_note_l_instance_qui_repond():
    httpd = serveur(200, "pod-a")
    try:
        counts = sonder(f"http://127.0.0.1:{httpd.server_address[1]}/sante/pret")
    finally:
        httpd.shutdown()
    assert counts["ok"] >= 3 and counts["ko"] == 0
    first, last, answered = counts["instances"]["pod-a"]
    assert answered == counts["ok"] and counts["debut"] <= first <= last


def test_sonde_note_chaque_refus_horodate_avec_son_errno():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]  # libéré : plus personne n'écoute
    counts = sonder(f"http://127.0.0.1:{port}/sante/pret")
    assert counts["ok"] == 0 and counts["ko"] >= 3
    failure = counts["echecs"][0]
    assert failure["type"] == "ConnectionRefusedError"
    assert failure["errno"] == errno.ECONNREFUSED
    assert failure["n"] == 1 and failure["t"] >= counts["debut"]


def test_sonde_nomme_le_pod_d_une_reponse_en_erreur():
    httpd = serveur(503, "pod-en-arret")
    try:
        counts = sonder(f"http://127.0.0.1:{httpd.server_address[1]}/sante/pret")
    finally:
        httpd.shutdown()
    failure = counts["echecs"][0]
    assert (failure["type"], failure["code"]) == ("HTTPError", 503)
    assert failure["instance"] == "pod-en-arret"


# --- flux de kubectl ------------------------------------------------------------------


def test_flux_de_kubectl_objet_par_objet():
    first = {"type": "ADDED", "object": {"metadata": {"name": "a"}}}
    second = {"type": "MODIFIED", "object": {"metadata": {"name": "b"}}}
    text = json.dumps(first, indent=4) + "\n" + json.dumps(second, indent=4) + "\n"
    assert list(objets_json(text.splitlines(keepends=True))) == [first, second]


# --- pods -----------------------------------------------------------------------------


def pod(node=None, ip=None, ready=None, stopping=False, component="web"):
    conditions = [] if ready is None else [{"type": "Ready", "status": str(ready)}]
    metadata = {
        "name": "web-a",
        "labels": {"app.kubernetes.io/component": component},
    }
    if stopping:
        metadata["deletionTimestamp"] = "2026-10-04T22:40:00Z"
    return {
        "metadata": metadata,
        "spec": {"nodeName": node} if node else {},
        "status": {"podIP": ip, "conditions": conditions}
        if ip
        else {"conditions": conditions},
    }


def test_transitions_d_un_pod_dans_l_ordre_de_sa_vie():
    states = [
        ("ADDED", pod()),
        ("MODIFIED", pod("k3d-cdg-agent-0")),
        ("MODIFIED", pod("k3d-cdg-agent-0", "10.42.1.9", ready=False)),
        ("MODIFIED", pod("k3d-cdg-agent-0", "10.42.1.9", ready=True)),
        ("MODIFIED", pod("k3d-cdg-agent-0", "10.42.1.9", ready=True)),
        ("MODIFIED", pod("k3d-cdg-agent-0", "10.42.1.9", ready=True, stopping=True)),
        ("MODIFIED", pod("k3d-cdg-agent-0", "10.42.1.9", ready=False, stopping=True)),
        ("DELETED", pod("k3d-cdg-agent-0", "10.42.1.9", ready=False, stopping=True)),
    ]
    seen, before = [], None
    for kind, obj in states:
        seen.append(transitions(before, kind, etat_pod(obj)))
        before = etat_pod(obj)
    assert seen == [
        ["apparu (web)"],
        ["sur le nœud k3d-cdg-agent-0"],
        ["adresse 10.42.1.9"],
        ["prêt"],
        [],
        ["arrêt demandé"],
        ["plus prêt"],
        ["supprimé"],
    ]


def test_pod_deja_la_au_debut_de_la_chronique():
    state = etat_pod(pod("k3d-cdg-server-0", "10.42.0.5", ready=True))
    assert transitions(None, "ADDED", state) == [
        "apparu (web)",
        "sur le nœud k3d-cdg-server-0",
        "adresse 10.42.0.5",
        "prêt",
    ]


# --- tranche d'adresses du service ----------------------------------------------------


def test_tranche_resumee_pod_par_pod():
    endpoint_slice = {
        "endpoints": [
            {
                "addresses": ["10.42.1.6"],
                "targetRef": {"name": "web-b"},
                "conditions": {"ready": False, "serving": True, "terminating": True},
            },
            {
                "addresses": ["10.42.0.5"],
                "targetRef": {"name": "web-a"},
                "conditions": {"ready": True, "serving": True, "terminating": False},
            },
        ]
    }
    assert resume_tranche(endpoint_slice) == (
        "web-a 10.42.0.5 prêt ; web-b 10.42.1.6 en arrêt, sert encore"
    )
    assert resume_tranche({"endpoints": None}) == "aucune adresse"


# --- règles réseau des nœuds ----------------------------------------------------------

IPTABLES = """# Generated by iptables-save v1.8.10 (nf_tables)
*filter
:INPUT ACCEPT [0:0]
:KUBE-POD-FW-ABCDEF - [0:0]
[0:0] -A KUBE-POD-FW-ABCDEF -m comment --comment "rule to log dropped traffic POD \
name:web-a namespace: cdg" -m mark ! --mark 0x10000/0x10000 -j NFLOG --nflog-group 100
[3:180] -A KUBE-POD-FW-ABCDEF -m comment --comment "rule to REJECT traffic destined \
for POD name:web-a namespace: cdg" -m mark ! --mark 0x10000/0x10000 -j REJECT
[0:0] -A KUBE-SERVICES -d 10.43.12.7/32 -p tcp -m comment --comment \
"cdg/cdg-contract-decision-graph-sante:sante has no endpoints" -m tcp --dport 8081 \
-j REJECT --reject-with icmp-port-unreachable
[5:300] -A KUBE-POD-FW-ABCDEF -j ACCEPT
COMMIT
""".replace("\\\n", "")


def test_compteurs_des_regles_de_refus_seulement():
    assert compteurs_de_refus(IPTABLES) == {
        (
            "KUBE-POD-FW-ABCDEF",
            "rule to REJECT traffic destined for POD name:web-a namespace: cdg",
        ): 3,
        (
            "KUBE-SERVICES",
            "cdg/cdg-contract-decision-graph-sante:sante has no endpoints",
        ): 0,
    }


def test_nouveaux_refus_y_compris_dans_une_chaine_renommee():
    # kube-router renomme ses chaînes à chaque synchronisation : un compteur neuf part de 0
    old = {("KUBE-POD-FW-A", "refus web-a"): 3, ("KUBE-SERVICES", "sans adresse"): 0}
    new = {
        ("KUBE-POD-FW-A", "refus web-a"): 4,
        ("KUBE-POD-FW-B", "refus web-a"): 2,
        ("KUBE-SERVICES", "sans adresse"): 0,
    }
    assert nouveaux_refus(old, new) == [
        (("KUBE-POD-FW-A", "refus web-a"), 1),
        (("KUBE-POD-FW-B", "refus web-a"), 2),
    ]


def test_membres_des_ensembles_d_adresses():
    text = (
        "create KUBE-SRC-AAAA hash:ip family inet hashsize 1024 maxelem 65536\n"
        "add KUBE-SRC-AAAA 10.42.2.9 timeout 0\n"
        "add KUBE-DST-BBBB 10.42.0.5\n"
    )
    assert membres(text) == {
        ("KUBE-SRC-AAAA", "10.42.2.9"),
        ("KUBE-DST-BBBB", "10.42.0.5"),
    }


# --- rapport ----------------------------------------------------------------------------

START = 1_791_153_561.0  # 2026-10-04 22:39:21 UTC


def test_rapport_horodate_relatif_a_la_sonde_avec_ses_echecs_et_le_resume():
    pods = {
        "sonde-abc": {"composant": "test", "ip": "10.42.2.9", "noeud": "agent-1"},
        "web-a": {"composant": "web", "ip": "10.42.0.5", "noeud": "server-0"},
        "pg-1": {"composant": None, "ip": "10.42.1.3", "noeud": "agent-0"},
    }
    events = [
        Evenement(START + 1.2, "ensemble", "server-0", "+ KUBE-SRC-AAAA", "10.42.2.9"),
        Evenement(START - 0.4, "pod", "sonde-abc", "adresse 10.42.2.9"),
        Evenement(START + 0.9, "ensemble", "agent-1", "+ KUBE-SRC-AAAA", "10.42.2.9"),
        Evenement(START + 2.0, "ensemble", "agent-0", "+ KUBE-SRC-CCCC", "10.42.1.3"),
        Evenement(
            START + 0.6,
            "refus",
            "server-0",
            "+1 KUBE-POD-FW-X « rule to REJECT traffic destined for POD name:web-a "
            "namespace: cdg »",
        ),
        Evenement(
            START + 3.0,
            "refus",
            "agent-0",
            "+2 KUBE-POD-FW-Y « rule to REJECT traffic destined for POD "
            "name:langfuse-web-1 namespace: cdg-observabilite »",
        ),
    ]
    probe = {
        "ok": 2072,
        "ko": 1,
        "debut": START,
        "echecs": [
            {
                "t": START + 0.6,
                "n": 3,
                "type": "ConnectionRefusedError",
                "errno": 111,
                "code": None,
            }
        ],
        "instances": {"web-a": [START + 0.2, START + 420, 2072]},
    }
    lines = rapport(events, pods, probe, "sonde-abc", ["agent-0 : relevé lent"])
    text = "\n".join(lines)
    assert "22:39:21.000 (+0.00 s) sonde : début de la boucle" in text
    assert "22:39:21.600 (+0.60 s) ÉCHEC n°3 : ConnectionRefusedError errno 111" in text
    # dans l'ordre du temps, relatif au début de la sonde
    assert text.index("(-0.40 s) pod sonde-abc : adresse 10.42.2.9") < text.index(
        "ÉCHEC n°3"
    )
    assert text.index("ÉCHEC n°3") < text.index("(+0.90 s) ensemble agent-1")
    # un refus dans l'espace cdg, en entier ; ailleurs, seulement compté
    assert "refus server-0 : +1 KUBE-POD-FW-X" in text
    assert "langfuse-web-1" not in text
    assert "refus hors de l'espace cdg : 2" in text
    # seules les adresses de la sonde et des pods de l'interface
    assert "10.42.1.3" not in text.split("--- chronique")[1]
    # résumé : l'adresse de la sonde admise, nœud par nœud
    assert "adresse de la sonde 10.42.2.9 dans les ensembles de kube-router" in text
    assert "agent-1 à +0.90 s ; server-0 à +1.20 s" in text
    assert "web-a : 2072 réponses, de +0.20 s à +420.00 s" in text
    assert "relevés incomplets : agent-0 : relevé lent" in text


def test_rapport_sans_adresse_de_sonde_le_dit():
    probe = {"ok": 1, "ko": 0, "debut": START, "echecs": [], "instances": {}}
    text = "\n".join(rapport([], {}, probe, "sonde-abc", []))
    assert "adresse de la sonde inconnue" in text


@pytest.mark.parametrize("seconds", [-1.0, 0.0, 12.34])
def test_rapport_ecart_signe(seconds):
    probe = {"ok": 0, "ko": 0, "debut": START, "echecs": [], "instances": {}}
    events = [Evenement(START + seconds, "pod", "web-a", "prêt")]
    text = "\n".join(rapport(events, {}, probe, "sonde", []))
    assert f"({seconds:+.2f} s) pod web-a : prêt" in text


def test_rapport_donne_la_fenetre_d_un_releve():
    # un relevé tous les demi-secondes : l'entrée dans un ensemble est datée par un
    # intervalle, entre le relevé précédent et celui qui la voit
    pods = {"sonde-abc": {"composant": "test", "ip": "10.42.2.9", "noeud": "agent-1"}}
    events = [
        Evenement(
            START + 0.9, "ensemble", "agent-1", "+ KUBE-SRC-A", "10.42.2.9", START + 0.4
        )
    ]
    probe = {"ok": 1, "ko": 0, "debut": START, "echecs": [], "instances": {}}
    text = "\n".join(rapport(events, pods, probe, "sonde-abc", []))
    assert "agent-1 entre +0.40 s et +0.90 s" in text
    assert "(+0.90 s) ensemble agent-1 : + KUBE-SRC-A 10.42.2.9 (sonde-abc)" in text
    assert "depuis +0.40 s" in text


# --- relevés en cours de scénario, avec un kubectl et un docker factices ----------------

KUBECTL = """
import json, sys, time
args = sys.argv[1:]
def watch(*events):
    for event in events:
        print(json.dumps(event, indent=4), flush=True)
    time.sleep(60)
if "nodes" in args:
    print(json.dumps({"items": [{"metadata": {"name": "noeud-1"}}]}))
elif "pods" in args:
    pod = {"metadata": {"name": "web-a", "labels": {"app.kubernetes.io/component": "web"}},
           "spec": {"nodeName": "noeud-1"}, "status": {"podIP": "10.42.0.5"}}
    ready = json.loads(json.dumps(pod))
    ready["status"]["conditions"] = [{"type": "Ready", "status": "True"}]
    watch({"type": "ADDED", "object": pod}, {"type": "MODIFIED", "object": ready})
elif "events" in args:
    watch({"type": "ADDED", "object": {"reason": "Killing", "message": "Stopping web",
           "involvedObject": {"kind": "Pod", "name": "web-a"}}})
elif "endpointslices" in args:
    watch({"type": "ADDED", "object": {"metadata": {"name": "sante-x"}, "endpoints": [
        {"addresses": ["10.42.0.5"], "targetRef": {"name": "web-a"},
         "conditions": {"ready": True}}]}})
"""

DOCKER = """
import sys
from pathlib import Path
counter = Path(sys.argv[1])
calls = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(calls))
print("*filter")
print(f'[{calls}:0] -A KUBE-POD-FW-A -m comment --comment "refus web-a" -j REJECT')
print("COMMIT")
print("#ipset")
if calls >= 2:
    print("add KUBE-SRC-A 10.42.0.5")
"""


def factices(tmp_path, docker_code: str) -> tuple[list[str], list[str]]:
    (tmp_path / "kubectl.py").write_text(KUBECTL, encoding="utf-8")
    (tmp_path / "docker.py").write_text(docker_code, encoding="utf-8")
    kubectl = [sys.executable, str(tmp_path / "kubectl.py")]
    docker = [sys.executable, str(tmp_path / "docker.py"), str(tmp_path / "appels")]
    return kubectl, docker


def attendre(condition, delai: float = 10):
    import time

    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        if condition():
            return
        time.sleep(0.05)
    raise AssertionError("délai dépassé")


def test_chronique_releve_pods_evenements_tranche_refus_et_ensembles(tmp_path):
    from chronique import Chronique

    kubectl, docker = factices(tmp_path, DOCKER)
    with Chronique(kubectl, "cdg", "sante", docker=docker, intervalle=0.1) as suivi:
        attendre(
            lambda: (
                {"pod", "evenement", "tranche", "refus", "ensemble"}
                <= {e.source for e in suivi.evenements}
            )
        )
    seen = {(e.source, e.objet, e.texte) for e in suivi.evenements}
    assert ("pod", "web-a", "prêt") in seen
    assert ("evenement", "Pod web-a", "Killing : Stopping web") in seen
    assert ("tranche", "sante-x", "web-a 10.42.0.5 prêt") in seen
    assert ("refus", "noeud-1", "+1 KUBE-POD-FW-A « refus web-a »") in seen
    [added] = [e for e in suivi.evenements if e.source == "ensemble"]
    assert (added.objet, added.adresse) == ("noeud-1", "10.42.0.5")
    assert added.depuis is not None and added.depuis < added.instant
    assert suivi.pods["web-a"]["ip"] == "10.42.0.5"
    assert suivi.erreurs == []


def test_chronique_dit_quand_un_noeud_ne_peut_etre_releve(tmp_path):
    from chronique import Chronique

    failing = (
        "import sys\nsys.stderr.write('No such container: noeud-1\\n')\nsys.exit(1)"
    )
    kubectl, docker = factices(tmp_path, failing)
    with Chronique(kubectl, "cdg", "sante", docker=docker, intervalle=0.1) as suivi:
        attendre(lambda: suivi.erreurs)
    assert "noeud-1 : No such container: noeud-1" in suivi.erreurs
    assert "noeud-1 : aucun relevé" in suivi.erreurs
    # chronique inutilisable : le scénario le dit
    assert suivi.lacunes() == ["noeud-1 : aucun relevé"]


def test_un_releve_manque_n_est_pas_une_lacune(tmp_path):
    from chronique import Chronique

    # premier relevé en échec, les suivants réussis : noté, mais la chronique tient
    flaky = DOCKER.replace(
        "counter.write_text(str(calls))\n",
        "counter.write_text(str(calls))\nif calls == 1:\n    sys.exit(3)\n",
    )
    kubectl, docker = factices(tmp_path, flaky)
    with Chronique(kubectl, "cdg", "sante", docker=docker, intervalle=0.1) as suivi:
        attendre(lambda: any(e.source == "refus" for e in suivi.evenements))
    assert suivi.erreurs == ["noeud-1 : code 3"]
    assert suivi.lacunes() == []


def test_objet_inattendu_dans_un_flux_note_et_suivi_poursuivi(tmp_path):
    from chronique import Chronique

    # kubectl peut glisser un événement ERROR (objet Status, sans nom) dans un flux
    broken = KUBECTL.replace(
        'watch({"type": "ADDED", "object": pod},',
        'watch({"type": "ERROR", "object": {"kind": "Status"}}, '
        '{"type": "ADDED", "object": pod},',
    )
    kubectl, docker = factices(tmp_path, DOCKER)
    (tmp_path / "kubectl.py").write_text(broken, encoding="utf-8")
    with Chronique(kubectl, "cdg", "sante", docker=docker, intervalle=0.1) as suivi:
        attendre(lambda: any(e.texte == "prêt" for e in suivi.evenements))
    assert "suivi des pods : objet illisible (KeyError)" in suivi.erreurs
    assert suivi.lacunes() == []


def test_tranche_notee_seulement_quand_elle_change():
    from chronique import Chronique

    suivi = Chronique([], "cdg", "sante")
    endpoint_slice = {
        "metadata": {"name": "sante-x"},
        "endpoints": [{"addresses": ["10.42.0.5"], "targetRef": {"name": "web-a"}}],
    }
    for kind in ("ADDED", "MODIFIED", "DELETED"):
        suivi._slice({"type": kind, "object": endpoint_slice}, START)
    assert [e.texte for e in suivi.evenements] == ["web-a 10.42.0.5 prêt", "supprimée"]
