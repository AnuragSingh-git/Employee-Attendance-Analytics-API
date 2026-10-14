"""Unit tests for deterministic business calculations; integration tests need MongoDB."""
from datetime import datetime, timezone
import pytest
from app.core.utils import epoch_dt, round_half_up

def test_epoch_milliseconds_rejects_seconds():
    with pytest.raises(Exception):
        epoch_dt(1_700_000_000)

def test_epoch_milliseconds_accepts_milliseconds():
    value = epoch_dt(1_700_000_000_000)
    assert value.tzinfo == timezone.utc

def test_round_half_up():
    assert round_half_up(1.225, 2) == 1.23
