from datetime import date, time

import pytest

from fakturobot.months import Month
from fakturobot.schedule import ScheduleConfig, default_target, evening_months, morning_months

CFG = ScheduleConfig(days_before_end=(3, 2, 1), time=time(10), evening_time=time(18), remind_until_day=15)


@pytest.mark.parametrize("today, expected", [
    (date(2026, 11, 26), []),
    (date(2026, 11, 27), [Month(2026, 11)]),
    (date(2026, 11, 29), [Month(2026, 11)]),
    (date(2026, 11, 30), [Month(2026, 11)]),          # ostatni dzień: wystawienie / przypomnienie
    (date(2026, 12, 1), [Month(2026, 11)]),           # zaległy poprzedni miesiąc
    (date(2026, 12, 15), [Month(2026, 11)]),
    (date(2026, 12, 16), []),
    (date(2026, 12, 28), [Month(2026, 12)]),          # grudzień ma 31 dni
    (date(2027, 1, 1), [Month(2026, 12)]),            # przełom roku
    (date(2027, 2, 25), [Month(2027, 2)]),            # luty 28 dni
])
def test_morning_months(today, expected):
    assert morning_months(today, CFG) == expected


def test_evening_only_last_day():
    assert evening_months(date(2026, 11, 30)) == [Month(2026, 11)]
    assert evening_months(date(2026, 11, 29)) == []


def test_default_target():
    assert default_target(date(2026, 12, 3), CFG, lambda m: False) == Month(2026, 11)
    assert default_target(date(2026, 12, 3), CFG, lambda m: True) == Month(2026, 12)
    assert default_target(date(2026, 11, 28), CFG, lambda m: False) == Month(2026, 11)


def test_month_helpers():
    assert Month(2026, 1).prev() == Month(2025, 12)
    assert Month(2026, 12).next() == Month(2027, 1)
    assert Month.parse("2026-11").label == "listopad 2026"
    assert Month(2026, 3).key == "2026-03"
    with pytest.raises(ValueError):
        Month.parse("listopad")
    with pytest.raises(ValueError):
        Month.parse("2026-13")
