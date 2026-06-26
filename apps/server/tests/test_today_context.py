from __future__ import annotations

from datetime import date, timedelta

import pytest
from anima_server.schemas.chat import TodayContext
from pydantic import ValidationError


def test_accepts_server_today() -> None:
    ctx = TodayContext(date=date.today().isoformat(), mood="calm")
    assert ctx.mood == "calm"


@pytest.mark.parametrize("delta", [-1, 1])
def test_accepts_plus_or_minus_one_day_for_timezone_skew(delta: int) -> None:
    # A client a calendar day ahead/behind a differently-zoned server must
    # not get a 422 on an otherwise-valid today context.
    d = (date.today() + timedelta(days=delta)).isoformat()
    ctx = TodayContext(date=d, energy="high")
    assert ctx.energy == "high"


@pytest.mark.parametrize("delta", [-3, 2, 365])
def test_rejects_clearly_stale_dates(delta: int) -> None:
    d = (date.today() + timedelta(days=delta)).isoformat()
    with pytest.raises(ValidationError):
        TodayContext(date=d, mood="tired")


def test_rejects_non_iso_date() -> None:
    with pytest.raises(ValidationError):
        TodayContext(date="06/19/2026", mood="tired")


def test_requires_some_content() -> None:
    with pytest.raises(ValidationError):
        TodayContext(date=date.today().isoformat())
