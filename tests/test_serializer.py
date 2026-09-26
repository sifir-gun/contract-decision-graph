"""Sérialiseur des checkpoints : types limités à nos modèles, jamais de dégradation silencieuse."""

import threading

import pytest
from doubles import clauses, usage, verdict
from langgraph.types import Interrupt, Send
from pydantic import BaseModel

from cdg.adapters.langgraph.checkpointer import BlockedDeserialization, strict_serializer
from cdg.domain.models import HumanDecision


class Intrus(BaseModel):
    x: int


@pytest.fixture
def serde():
    return strict_serializer()


def roundtrip(serde, obj):
    return serde.loads_typed(serde.dumps_typed(obj))


@pytest.mark.parametrize(
    "obj",
    [
        clauses()[0],
        verdict("juridique", score=0.5),
        usage(10, 5),
        HumanDecision(decision="NO_GO", reviewer="relecteur-synth", reason="motif"),
        {"verdicts": [verdict("financier")], "failure_report": {"stage": "budget"}},
    ],
)
def test_nos_modeles_passent_intacts(serde, obj):
    back = roundtrip(serde, obj)
    assert back == obj and type(back) is type(obj)


def test_types_langgraph_surs_passent(serde):
    assert roundtrip(serde, Send("analyst", {"domain": "juridique"})).node == "analyst"
    assert roundtrip(serde, Interrupt(value={"a": 1})).value == {"a": 1}


@pytest.mark.parametrize("obj", [Intrus(x=1), [Intrus(x=2)], {"v": {"w": Intrus(x=3)}}])
def test_type_hors_liste_leve_une_erreur_au_lieu_d_un_dict(serde, obj):
    with pytest.raises(BlockedDeserialization, match="Intrus"):
        roundtrip(serde, obj)


def test_blocage_sans_effet_sur_la_lecture_suivante(serde):
    with pytest.raises(BlockedDeserialization):
        roundtrip(serde, Intrus(x=1))
    assert roundtrip(serde, usage(1, 1)) == usage(1, 1)


def test_blocage_dans_un_autre_fil_ne_contamine_pas_ce_fil(serde):
    errors = []

    def blocked():
        try:
            roundtrip(serde, Intrus(x=4))
        except BlockedDeserialization as exc:
            errors.append(exc)

    t = threading.Thread(target=blocked)
    t.start()
    t.join()
    assert len(errors) == 1
    assert roundtrip(serde, verdict("conformite")) == verdict("conformite")
