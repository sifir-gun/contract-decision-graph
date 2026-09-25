"""expire : sélection pure des threads en attente trop longtemps, décision système."""

from datetime import UTC, datetime, timedelta

import pytest

from cdg.domain import expiry
from cdg.domain.models import HumanDecision

NOW = datetime(2026, 9, 24, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "text,delta",
    [
        ("24h", timedelta(hours=24)),
        ("30m", timedelta(minutes=30)),
        ("2d", timedelta(days=2)),
        ("90s", timedelta(seconds=90)),
        ("0s", timedelta(0)),
    ],
)
def test_duree(text, delta):
    assert expiry.parse_duration(text) == delta


@pytest.mark.parametrize("text", ["24", "h", "-1h", "1.5h", "24 h", "1w", ""])
def test_duree_invalide(text):
    with pytest.raises(ValueError, match="durée"):
        expiry.parse_duration(text)


def test_selection_strictement_au_dela_du_delai():
    pending = [
        ("vieux", NOW - timedelta(hours=25)),
        ("pile", NOW - timedelta(hours=24)),
        ("recent", NOW - timedelta(hours=1)),
    ]
    assert expiry.expired(pending, timedelta(hours=24), NOW) == [pending[0]]


def test_horloge_naive_refusee():
    with pytest.raises(ValueError, match="fuseau"):
        expiry.expired([], timedelta(hours=1), datetime(2026, 9, 24, 12, 0))  # noqa: DTZ001


def test_decision_systeme_d_expiration():
    answer = expiry.system_decision(
        waited=timedelta(hours=25, minutes=5), older_than=timedelta(hours=24)
    )
    h = HumanDecision.model_validate(answer)
    assert (h.decision, h.reviewer, h.source, h.overrides_block) == (
        "NO_GO",
        "systeme:expire",
        "systeme",
        False,
    )
    assert h.reason == "timeout : en attente depuis 25 h 05 min, délai 24 h 00 min"
