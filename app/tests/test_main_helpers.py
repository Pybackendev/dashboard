from datetime import date, timedelta

import pytest
from fastapi import HTTPException

import main


@pytest.mark.parametrize("value, expected", [
    ("Іван Коваленко", "Іван Коваленко"),
    ("=HYPERLINK(\"http://evil\")", "'=HYPERLINK(\"http://evil\")"),
    ("+380501234567", "'+380501234567"),
    ("-2+3", "'-2+3"),
    ("@SUM(A1)", "'@SUM(A1)"),
    ("\tcmd", "'\tcmd"),
    ("Complete", "Complete"),
    (None, ""),
])
def test_csv_safe_escapes_formula_prefixes(value, expected):
    assert main._csv_safe(value) == expected


def hours(start, end):
    return (end - start).total_seconds() / 3600


def test_date_window_handles_dst_days_in_kyiv():
    start, end, *_ = main._date_window(7, date(2026, 10, 25), date(2026, 10, 25))  # осінній перехід
    assert hours(start, end) == 25
    start, end, *_ = main._date_window(7, date(2026, 3, 29), date(2026, 3, 29))    # весняний перехід
    assert hours(start, end) == 23
    start, end, *_ = main._date_window(7, date(2026, 10, 13), date(2026, 10, 13))
    assert hours(start, end) == 24


def test_date_window_day_starts_at_kyiv_midnight():
    start, *_ = main._date_window(7, date(2026, 10, 13), date(2026, 10, 13))
    assert (start.hour, start.minute, start.day) == (21, 0, 12)  # 00:00 Київ = 21:00 UTC (UTC+3)


def test_previous_window_has_same_length_and_follows_directly():
    since, end, prev_since, start_day, last_day = main._date_window(7, date(2026, 10, 10), date(2026, 10, 16))
    assert (last_day - start_day).days + 1 == 7
    assert since - prev_since == end - since


def test_range_longer_than_a_year_is_rejected():
    assert main._date_window(7, date(2024, 1, 1), date(2024, 12, 31))  # 366 днів дозволено
    with pytest.raises(HTTPException) as err:
        main._date_window(7, date(2024, 1, 1), date(2025, 1, 1))        # 367 днів
    assert err.value.status_code == 422


def test_range_validation_errors():
    with pytest.raises(HTTPException) as err:
        main._date_window(7, date(2026, 10, 13), date(2026, 10, 12))
    assert err.value.status_code == 422
    with pytest.raises(HTTPException) as err:
        main._date_window(7, date(2026, 10, 13), None)
    assert err.value.status_code == 422


def test_days_without_dates_are_clamped_to_a_year():
    since, end, *_ = main._date_window(100000)
    assert end - since <= timedelta(days=366)
