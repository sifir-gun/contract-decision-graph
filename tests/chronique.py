"""Chronique d'un scénario du cluster (enquête du 05/10 : une requête perdue pendant la
mise à jour progressive, 2 fois sur 11 exécutions depuis le 03/10, chaque fois un refus de
connexion dans les premières secondes de la sonde). Instrumenter avant de corriger : la
sonde dit quel pod a répondu, et date chaque échec ; pendant le scénario sont relevés,
horodatés à l'horloge du runner (celle des nœuds k3d et de la sonde : même noyau) :

- l'état des pods de l'espace (nœud, adresse, prêt, arrêt) et ses événements Kubernetes ;
- la tranche d'adresses (EndpointSlice) du service visé par la sonde ;
- sur chaque nœud, deux fois par seconde, les règles de refus (REJECT de kube-router et de
  kube-proxy) dont le compteur augmente, et les adresses qui entrent dans les ensembles
  d'adresses (ipset) de kube-router ou en sortent.

kube-router (2.6.3-k3s1, celui de k3s 1.36.4) refuse par REJECT (ICMP « port
injoignable » : « Connection refused » pour le client), dans une chaîne par pod renommée
à chaque synchronisation (`pod.go`, `podFirewallChainName`) : un compteur neuf part de
zéro, et un refus suivi d'une synchronisation en moins d'un relevé peut échapper au
relevé. L'entrée d'une adresse dans un ensemble, elle, reste visible, datée à un relevé
près.
"""

import json
import re
import subprocess
import threading
import time
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Self

# la sonde, lancée par `python -c` dans son pod : une requête toutes les 0,2 s pendant
# `duree` secondes ; chaque réponse nomme le pod qui l'a rendue (`instance`) ; chaque
# échec est daté (horloge du nœud), numéroté et typé (errno, code HTTP, pod s'il répond)
SONDE = """
import json, socket, time, urllib.error, urllib.parse, urllib.request
url, end = "{url}", time.monotonic() + {duree}
debut, ok, ko, n, echecs, instances = time.time(), 0, 0, 0, [], {{}}
while time.monotonic() < end:
    n += 1
    instant = time.time()
    try:
        with urllib.request.urlopen(url, timeout=2) as reponse:
            statut, corps = reponse.status, reponse.read()
        if statut != 200:
            raise urllib.error.HTTPError(url, statut, "statut", None, None)
        ok += 1
        nom = json.loads(corps).get("instance", "?")
        vu = instances.setdefault(nom, [instant, instant, 0])
        vu[1], vu[2] = instant, vu[2] + 1
    except Exception as exc:
        ko += 1
        # le refus d'une connexion est la raison d'une URLError ; une HTTPError, elle,
        # porte le code et la réponse du pod
        http = isinstance(exc, urllib.error.HTTPError)
        raison = exc if http else getattr(exc, "reason", exc)
        echec = {{"t": instant, "n": n, "type": type(raison).__name__,
                 "errno": getattr(raison, "errno", None),
                 "code": getattr(exc, "code", None)}}
        if http and exc.fp is not None:
            try:
                echec["instance"] = json.loads(exc.read()).get("instance")
            except ValueError:
                echec["instance"] = "illisible"
        echecs.append(echec)
    time.sleep(0.2)
# adresse de la sonde, relevée après la boucle pour ne rien changer à son début : une
# socket UDP connectée n'envoie aucun paquet
# (en échec : None, que le rapport dit « inconnue » ; le bilan est écrit quoi qu'il arrive)
cible, adresse = urllib.parse.urlsplit(url), None
try:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.connect((cible.hostname, cible.port))
        adresse = udp.getsockname()[0]
except OSError:
    pass
print(json.dumps({{"ok": ok, "ko": ko, "debut": debut, "adresse": adresse,
                  "echecs": echecs[:20], "instances": instances}}))
"""

# relevé d'un nœud k3d : règles du filtre avec leurs compteurs, puis ensembles d'adresses
# (outils de k3s, dans /bin/aux, sur le PATH de l'image)
RELEVE = "iptables-save -c -t filter && echo '#ipset' && ipset save"


@dataclass(frozen=True)
class Evenement:
    instant: float  # secondes depuis l'époque, horloge du runner
    source: str  # pod, evenement, tranche, refus, ensemble
    objet: str  # pod, objet Kubernetes, tranche ou nœud concerné
    texte: str
    adresse: str | None = None  # adresse entrée dans un ensemble, ou sortie
    depuis: float | None = None  # relevé précédent : le fait date d'entre les deux


class FluxIllisible(Exception):
    """Fin d'un flux de kubectl sur un objet incomplet ou un texte qui n'est pas du
    JSON."""


def objets_json(lignes: Iterable[str]) -> Iterator[dict]:
    """Objets successifs de `kubectl get --watch --output-watch-events -o json` :
    kubectl 1.36.4 écrit chaque événement en JSON compact, sur une ligne (cli-runtime,
    `printers/json.go`, cas `WatchEvent`) ; un objet indenté sur plusieurs lignes est lu
    aussi. Un reste illisible à la fin du flux lève `FluxIllisible`, jamais tu."""
    decoder = json.JSONDecoder()
    tampon = ""
    for ligne in lignes:
        tampon += ligne
        if not ligne.rstrip().endswith("}"):
            continue
        texte = tampon.strip()
        try:
            obj, fin = decoder.raw_decode(texte)
        except json.JSONDecodeError:
            continue  # objet sur plusieurs lignes : la suite arrive
        yield obj
        tampon = texte[fin:]
    if tampon.strip():
        raise FluxIllisible(tampon.strip()[:80])


def etat_pod(pod: dict) -> dict[str, Any]:
    status = pod.get("status") or {}
    ready = None
    for condition in status.get("conditions") or []:
        if condition["type"] == "Ready":
            ready = condition["status"] == "True"
    return {
        "composant": (pod["metadata"].get("labels") or {}).get(
            "app.kubernetes.io/component"
        ),
        "noeud": (pod.get("spec") or {}).get("nodeName"),
        "ip": status.get("podIP"),
        "pret": ready,
        "arret": bool(pod["metadata"].get("deletionTimestamp")),
    }


def transitions(avant: dict | None, genre: str, etat: dict) -> list[str]:
    """Ce qui a changé pour un pod, d'un état (`etat_pod`) au suivant."""
    if genre == "DELETED":
        return ["supprimé"]
    changes = []
    if avant is None:
        changes.append(f"apparu ({etat['composant'] or 'sans composant'})")
        avant = {"noeud": None, "ip": None, "pret": None, "arret": False}
    if etat["noeud"] and etat["noeud"] != avant["noeud"]:
        changes.append(f"sur le nœud {etat['noeud']}")
    if etat["ip"] and etat["ip"] != avant["ip"]:
        changes.append(f"adresse {etat['ip']}")
    if etat["arret"] and not avant["arret"]:
        changes.append("arrêt demandé")
    if etat["pret"] and not avant["pret"]:
        changes.append("prêt")
    elif avant["pret"] and not etat["pret"]:
        changes.append("plus prêt")
    return changes


def resume_tranche(tranche: dict) -> str:
    """Les adresses d'une tranche et leur état, pod par pod."""
    parts = []
    for endpoint in tranche.get("endpoints") or []:
        name = (endpoint.get("targetRef") or {}).get("name", "?")
        addresses = ",".join(endpoint.get("addresses") or [])
        conditions = endpoint.get("conditions") or {}
        if conditions.get("terminating"):
            state = "en arrêt" + (", sert encore" if conditions.get("serving") else "")
        else:
            # API EndpointSlice : une disponibilité non renseignée vaut « prête »
            state = "prêt" if conditions.get("ready") is not False else "pas prêt"
        parts.append(f"{name} {addresses} {state}")
    return " ; ".join(sorted(parts)) or "aucune adresse"


_REGLE = re.compile(r"^\[(\d+):\d+\] -A (\S+) (.*)$")
_REFUS = re.compile(r"(^| )-j REJECT( |$)")
_COMMENTAIRE = re.compile(r'--comment (?:"([^"]*)"|(\S+))')


def compteurs_de_refus(texte: str) -> dict[tuple[str, str], int]:
    """Règles REJECT de `iptables-save -c` : (chaîne, commentaire) → paquets refusés."""
    counts = {}
    for line in texte.splitlines():
        rule = _REGLE.match(line)
        if rule is None or not _REFUS.search(rule[3]):
            continue
        comment = _COMMENTAIRE.search(rule[3])
        label = (comment[1] or comment[2]) if comment else rule[3]
        counts[(rule[2], label)] = int(rule[1])
    return counts


def nouveaux_refus(
    avant: dict[tuple[str, str], int], apres: dict[tuple[str, str], int]
) -> list[tuple[tuple[str, str], int]]:
    """Refus survenus d'un relevé au suivant ; une chaîne nouvelle part de zéro."""
    return sorted(
        (key, count - avant.get(key, 0))
        for key, count in apres.items()
        if count > avant.get(key, 0)
    )


def membres(texte: str) -> set[tuple[str, str]]:
    """(ensemble, adresse) de `ipset save`."""
    return {
        (parts[1], parts[2])
        for parts in (line.split() for line in texte.splitlines())
        if len(parts) >= 3 and parts[0] == "add"
    }


def _heure(instant: float) -> str:
    return datetime.fromtimestamp(instant, UTC).strftime("%H:%M:%S.%f")[:-3]


_CDG = re.compile(r"namespace: cdg(?![\w-])|« cdg/")


def rapport(
    evenements: list[Evenement],
    pods: dict[str, dict],
    sonde: dict,
    nom_sonde: str,
    erreurs: list[str],
) -> list[str]:
    """Résumé, puis chronique triée, datée en UTC et relativement au début de la sonde.
    Ensembles d'adresses : seulement les adresses de la sonde et des pods de
    l'interface ; refus : en entier dans l'espace cdg, comptés ailleurs."""
    start = sonde["debut"]

    def when(instant: float) -> str:
        return f"{_heure(instant)} ({instant - start:+.2f} s)"

    owners = {
        p["ip"]: name
        for name, p in pods.items()
        if p.get("ip") and p.get("composant") in ("web", "test")
    }
    # l'adresse que la sonde donne d'elle-même, à défaut de celle de son pod
    probe_ip = (pods.get(nom_sonde) or {}).get("ip") or sonde.get("adresse")
    if probe_ip:
        owners[probe_ip] = nom_sonde
    rows: list[tuple[float, str]] = [(start, "sonde : début de la boucle")]
    outside, admitted = 0, []
    for event in evenements:
        if event.source == "ensemble":
            if event.adresse not in owners:
                continue
            text = (
                f"ensemble {event.objet} : {event.texte} {event.adresse} "
                f"({owners[event.adresse]})"
            )
            if event.depuis is not None:
                text += f", depuis {event.depuis - start:+.2f} s"
            if event.adresse == probe_ip and event.texte.startswith("+"):
                admitted.append(event)
        elif event.source == "refus":
            if not _CDG.search(event.texte):
                outside += int(event.texte.split()[0])
                continue
            text = f"refus {event.objet} : {event.texte}"
            if event.depuis is not None:
                text += f", depuis {event.depuis - start:+.2f} s"
        elif event.source == "evenement":
            text = f"événement {event.objet} : {event.texte}"
        else:
            text = f"{event.source} {event.objet} : {event.texte}"
        rows.append((event.instant, text))
    for failure in sonde["echecs"]:
        text = f"ÉCHEC n°{failure['n']} : {failure['type']}"
        if failure.get("errno") is not None:
            text += f" errno {failure['errno']}"
        if failure.get("code") is not None:
            text += f" code {failure['code']}"
        if failure.get("instance"):
            text += f" instance {failure['instance']}"
        rows.append((failure["t"], text))
    for name, (first, last, _) in sonde["instances"].items():
        rows.append((first, f"sonde : première réponse de {name}"))
        rows.append((last, f"sonde : dernière réponse de {name}"))

    lines = [
        (
            f"=== chronique : sonde {nom_sonde} (ok {sonde['ok']}, ko {sonde['ko']}), "
            f"début {_heure(start)} UTC ==="
        )
    ]
    if probe_ip is None:
        lines.append("adresse de la sonde inconnue")
    else:
        first_seen: dict[str, Evenement] = {}
        for event in sorted(admitted, key=lambda e: e.instant):
            first_seen.setdefault(event.objet, event)
        nodes = " ; ".join(
            f"{node} à {e.instant - start:+.2f} s"
            if e.depuis is None
            else f"{node} entre {e.depuis - start:+.2f} s et {e.instant - start:+.2f} s"
            for node, e in first_seen.items()
        )
        lines.append(
            f"adresse de la sonde {probe_ip} dans les ensembles de kube-router : "
            f"{nodes or 'jamais vue'}"
        )
    for name, (first, last, answered) in sorted(sonde["instances"].items()):
        lines.append(
            f"{name} : {answered} réponses, de {first - start:+.2f} s "
            f"à {last - start:+.2f} s"
        )
    lines.append(f"refus hors de l'espace cdg : {outside}")
    if erreurs:
        lines.append("relevés incomplets : " + " ; ".join(erreurs))
    lines.append("--- chronique")
    lines.extend(f"{when(instant)} {text}" for instant, text in sorted(rows))
    return lines


class Chronique:
    """Relevés pendant un scénario : `with Chronique(...) as suivi:` ; puis
    `suivi.rapport(sonde, nom)`. Toute défaillance d'un relevé est notée dans
    `erreurs`, jamais tue."""

    def __init__(
        self,
        kubectl: list[str],
        espace: str,
        service: str,
        *,
        docker: list[str] | None = None,
        intervalle: float = 0.5,
    ):
        self._kubectl, self._espace, self._service = kubectl, espace, service
        self._docker = docker if docker is not None else ["docker"]
        self._intervalle = intervalle
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []
        self._watches: list[subprocess.Popen[str]] = []
        self._releves: dict[str, int] = {}
        self._slices: dict[str, str] = {}
        self._recus: dict[str, int] = {}  # objets reçus par suivi
        self.evenements: list[Evenement] = []
        self.pods: dict[str, dict] = {}
        self.erreurs: list[str] = []

    def __enter__(self) -> Self:
        nodes = json.loads(
            subprocess.run(
                self._kubectl + ["get", "nodes", "-o", "json"],
                capture_output=True,
                text=True,
                timeout=60,
                check=True,
            ).stdout
        )
        watched = [
            ("pods", ["pods", "--watch"], self._pod),
            ("événements", ["events", "--watch-only"], self._k8s_event),
            (
                "tranches",
                [
                    "endpointslices",
                    "-l",
                    f"kubernetes.io/service-name={self._service}",
                    "--watch",
                ],
                self._slice,
            ),
        ]
        for what, args, handle in watched:
            command = self._kubectl + [
                "get",
                *args,
                "-n",
                self._espace,
                "--output-watch-events",
                "-o",
                "json",
            ]
            process = subprocess.Popen(
                command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            self._watches.append(process)
            self._recus[what] = 0
            self._start(self._follow, what, process, handle)
        for item in nodes["items"]:
            node = item["metadata"]["name"]
            self._releves[node] = 0
            self._start(self._poll, node)
        return self

    def __exit__(self, *exc: object) -> None:
        self._stop.set()
        for process in self._watches:
            process.terminate()
        for thread in self._threads:
            thread.join(15)
        for node, count in self._releves.items():
            if count == 0:
                self._error(f"{node} : aucun relevé")
        for what, count in self._recus.items():
            if count == 0:
                self._error(f"suivi des {what} : aucun objet reçu")

    def lacunes(self) -> list[str]:
        """Ce qui rend la chronique inutilisable : un nœud jamais relevé, un suivi
        interrompu, muet ou illisible. Un relevé manqué de temps à autre, un objet
        inattendu, sont seulement notés."""
        fatal = ("aucun relevé", "interrompu", "aucun objet reçu", "flux illisible")
        return [e for e in self.erreurs if any(word in e for word in fatal)]

    def rapport(self, sonde: dict, nom_sonde: str) -> list[str]:
        with self._lock:
            events = list(self.evenements)
        return rapport(events, self.pods, sonde, nom_sonde, self.erreurs)

    def _start(self, target, *args) -> None:
        thread = threading.Thread(target=target, args=args, daemon=True)
        thread.start()
        self._threads.append(thread)

    def _add(self, event: Evenement) -> None:
        with self._lock:
            self.evenements.append(event)

    def _error(self, message: str) -> None:
        with self._lock:
            if message not in self.erreurs:
                self.erreurs.append(message)

    def _follow(self, what: str, process: subprocess.Popen[str], handle) -> None:
        assert process.stdout is not None and process.stderr is not None
        try:
            for obj in objets_json(process.stdout):
                self._recus[what] += 1
                try:
                    handle(obj, time.time())
                # un objet inattendu (événement ERROR de kubectl) : noté, suivi poursuivi
                except (KeyError, TypeError, AttributeError) as exc:
                    name = type(exc).__name__
                    self._error(f"suivi des {what} : objet illisible ({name})")
        except FluxIllisible as exc:
            self._error(f"suivi des {what} : flux illisible ({exc})")
        if not self._stop.is_set():
            detail = process.stderr.read().strip().splitlines()[:1]
            self._error(f"suivi des {what} interrompu : {' '.join(detail) or '?'}")

    def _pod(self, event: dict, instant: float) -> None:
        name = event["object"]["metadata"]["name"]
        state = etat_pod(event["object"])
        for change in transitions(self.pods.get(name), event["type"], state):
            self._add(Evenement(instant, "pod", name, change))
        with self._lock:
            self.pods[name] = state

    def _k8s_event(self, event: dict, instant: float) -> None:
        obj = event["object"]
        involved = obj.get("involvedObject") or {}
        text = f"{obj.get('reason', '?')} : {(obj.get('message') or '')[:160]}"
        objet = f"{involved.get('kind', '?')} {involved.get('name', '?')}"
        self._add(Evenement(instant, "evenement", objet, text))

    def _slice(self, event: dict, instant: float) -> None:
        name = event["object"]["metadata"]["name"]
        if event["type"] == "DELETED":
            text = "supprimée"
        else:
            text = resume_tranche(event["object"])
        if (
            self._slices.get(name) != text
        ):  # une modification sans effet sur les adresses
            self._slices[name] = text
            self._add(Evenement(instant, "tranche", name, text))

    def _poll(self, node: str) -> None:
        previous: tuple[float, dict, set] | None = None
        while not self._stop.is_set():
            begun = time.time()
            try:
                result = subprocess.run(
                    self._docker + ["exec", node, "sh", "-c", RELEVE],
                    capture_output=True,
                    text=True,
                    timeout=10,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                self._error(f"{node} : relevé de plus de 10 s")
                continue
            except (
                OSError
            ) as exc:  # docker absent : aucun relevé possible, dit à la fin
                self._error(f"{node} : {exc.strerror or exc}")
                return
            instant = time.time()
            if result.returncode != 0:
                first = result.stderr.strip().splitlines()[:1]
                self._error(
                    f"{node} : {' '.join(first) or f'code {result.returncode}'}"
                )
            else:
                rules, _, sets = result.stdout.partition("\n#ipset\n")
                counts, members = compteurs_de_refus(rules), membres(sets)
                if previous is not None:
                    self._compare(node, previous, instant, counts, members)
                previous = (instant, counts, members)
                self._releves[node] += 1
            self._stop.wait(max(0.0, self._intervalle - (time.time() - begun)))

    def _compare(self, node, previous, instant, counts, members) -> None:
        since, old_counts, old_members = previous
        for (chain, comment), added in nouveaux_refus(old_counts, counts):
            text = f"+{added} {chain} « {comment} »"
            self._add(Evenement(instant, "refus", node, text, None, since))
        for name, address in sorted(members - old_members):
            self._add(Evenement(instant, "ensemble", node, f"+ {name}", address, since))
        for name, address in sorted(old_members - members):
            self._add(Evenement(instant, "ensemble", node, f"- {name}", address, since))
