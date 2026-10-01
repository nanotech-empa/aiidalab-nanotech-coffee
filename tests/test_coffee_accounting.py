"""Small, hand-calculated ledgers; no AiiDA profile or database writes."""

from datetime import datetime

import pytest

from coffee_accounting import calculate_ledger, departure_balances


def timestamp(day):
    return datetime.fromisoformat(day).timestamp()


def member(name, started="2026-01-05", left=None):
    return {
        "name": name, "started": timestamp(started),
        "extras": {"left": timestamp(left)} if left else {},
    }


def event(person, kind, amount, day="2026-01-05", cups=None):
    record = {
        "person": person, "event": kind, "amount": amount,
        "datei": timestamp(day), "description": "bought 2kg coffee",
    }
    if cups is not None:
        record["Range_cups/day"] = cups
    return record


def test_cost_changes_with_purchases_and_consumption():
    people = [member("Buyer"), member("Drinker")]
    events = [event("Buyer", "Bought coffee", 40)]
    report = calculate_ledger(people, events, timestamp("2026-01-12"))
    assert report["total_cups"] == 10  # Five completed weekdays each.
    assert report["cost_per_cup"] == 4
    events.append(event("Buyer", "Bought coffee", 20))
    assert calculate_ledger(people, events, timestamp("2026-01-12"))["cost_per_cup"] == 6
    events.append(event("Drinker", "default_cups/day", 0, cups=2))
    assert calculate_ledger(people, events, timestamp("2026-01-12"))["cost_per_cup"] == 4


def test_latest_default_wins_even_if_query_order_is_reversed():
    events = [
        event("A", "default_cups/day", 0, "2026-01-09", cups=0.1),
        event("A", "default_cups/day", 0, "2026-01-06", cups=0.5),
    ]
    report = calculate_ledger([member("A")], events, timestamp("2026-01-12"))
    assert report["people"]["A"]["coeff"] == 0.1
    assert report["total_cups"] == pytest.approx(0.5)


def test_purchase_totals_do_not_include_cash_or_accessories():
    events = [
        event("A", "Bought coffee", 20), event("A", "Bought accessory", 5),
        event("A", "Bought cleaning stuff", 5), event("A", "Added cash", 10),
        event("A", "Donated cash", 7), event("A", "Requested cash", -2),
    ]
    report = calculate_ledger([member("A")], events, timestamp("2026-01-12"))
    assert report["total_coffee_cost"] == 20
    assert report["total_cost"] == 30
    assert report["cost_per_cup"] == 4
    assert report["cash"] == 15
    assert report["people"]["A"]["cash"] == 8


def test_ranges_exclude_today_and_stop_at_departure():
    people = [member("A", left="2026-01-08")]
    events = [event("A", "Range_cups/day", 10, cups=0.2)]
    assert calculate_ledger(people, events, timestamp("2026-01-07"))["total_cups"] == pytest.approx(0.4)
    assert calculate_ledger(people, events, timestamp("2026-02-01"))["total_cups"] == pytest.approx(0.6)


def test_absence_overrides_ranges_and_overlaps_are_not_double_counted():
    events = [
        event("A", "Range_cups/day", 10, cups=2),
        event("A", "Absence", 3, "2026-01-06"),
        event("A", "Absence", 3, "2026-01-07"),
    ]
    # Monday is the only non-absent day of the completed Mon-Fri week.
    assert calculate_ledger([member("A")], events, timestamp("2026-01-12"))["total_cups"] == 2
    # On Wednesday, only Monday and Tuesday have elapsed.
    assert calculate_ledger([member("A")], events, timestamp("2026-01-07"))["total_cups"] == 2


@pytest.mark.parametrize("buyer, expected", [("Leaver", 40), ("Stayer", -40)])
def test_departure_credit_and_debt_ignore_later_activity(buyer, expected):
    people = [member("Leaver", left="2026-01-09"), member("Stayer")]
    events = [event(buyer, "Bought coffee", 80, "2026-01-09")]
    first = departure_balances(people, events, timestamp("2026-01-10"))
    assert first == {"Leaver": expected}
    people.append(member("Newcomer", started="2026-01-15"))
    events += [
        event("Stayer", "Bought coffee", 100, "2026-01-16"),
        event("Stayer", "default_cups/day", 0, "2026-01-16", cups=5),
        event("Leaver", "Added cash", 200, "2026-01-16"),
    ]
    assert departure_balances(people, events, timestamp("2026-02-01")) == first


def test_departure_uses_the_existing_cost_correction_at_that_date():
    people = [
        member("Earlier", left="2026-01-09"), member("Leaver", left="2026-01-12"),
        member("Stayer"),
    ]
    events = [event("Leaver", "Bought coffee", 140)]
    # 4 + 5 + 5 cups, earlier member's unadjusted debt = 40 CHF.
    expected = 140 - (140 + 40) * 5 / 14
    assert departure_balances(people, events, timestamp("2026-02-01"))["Leaver"] == pytest.approx(expected)


def test_zero_cups_and_future_records():
    assert calculate_ledger([], [], timestamp("2026-01-05"))["cost_per_cup"] is None
    people = [member("A"), member("Future", started="2026-02-01")]
    events = [event("A", "Bought coffee", 20)]
    report = calculate_ledger(people, events, timestamp("2026-01-05"))
    assert report["total_cups"] == 0
    assert report["cost_per_cup"] is None
    assert report["people"]["A"]["balance"] is None
    assert "Future" not in report["people"]
    events.append(event("A", "Bought coffee", 90, "2026-02-02"))
    assert calculate_ledger(people, events, timestamp("2026-01-05"))["total_coffee_cost"] == 20


def test_weekend_start_reconstructs_business_days():
    # Original business-day counts from Saturday to Tuesday cover Monday only.
    events = [event("A", "Absence", 1, "2026-01-10")]
    report = calculate_ledger([member("A")], events, timestamp("2026-01-13"))
    assert report["people"]["A"]["days"] == 6
    assert report["total_cups"] == 5


def test_two_month_trend_uses_the_same_purchase_and_consumption_window():
    from coffee_accounting import coffee_averages

    people = [member("A", started="2025-12-01")]
    events = [
        event("A", "default_cups/day", 0, "2025-12-02", cups=0.5),
        event("A", "Range_cups/day", 15, "2025-12-29", cups=2),
        event("A", "Absence", 10, "2025-12-29"),
        event("A", "default_cups/day", 0, "2026-01-15", cups=1),
        event("A", "Bought coffee", 999, "2025-12-31"),
        event("A", "Bought coffee", 80, "2026-01-02"),
        event("A", "Bought coffee", 20, "2026-03-02"),
        event("A", "Bought coffee", 10000, "2026-03-03"),
    ]
    events[4]["description"] = "bought 100kg coffee"
    events[5]["description"] = "bought 4kg coffee"
    events[6]["description"] = "bought 1kg coffee"
    recent = coffee_averages(people, events, timestamp("2026-03-02"))["recent"]
    assert recent["start"].isoformat() == "2026-01-02"
    assert recent["end"].isoformat() == "2026-03-02"
    # Six absent weekdays, five at two cups/day and thirty at one cup/day.
    # Settings and intervals starting before the window still apply.
    assert recent["cups"] == 40
    assert recent["cost"] == 100
    assert recent["kg"] == 5
    assert recent["kg_per_month"] == 2.5
    assert recent["cost_per_cup"] == 2.5


@pytest.mark.parametrize(
    "as_of, expected_start",
    [("2026-04-30", "2026-02-28"), ("2024-04-30", "2024-02-29"),
     ("2026-01-15", "2025-11-15")],
)
def test_trend_window_uses_calendar_months(as_of, expected_start):
    from coffee_accounting import coffee_averages

    report = coffee_averages([], [], timestamp(as_of))
    assert report["recent"]["start"].isoformat() == expected_start
    assert report["recent"]["months"] == 2
    assert report["recent"]["cost_per_cup"] is None
    assert report["recent"]["kg_per_month"] == 0


def test_recent_zero_purchases_are_not_missing_data():
    from coffee_accounting import coffee_averages

    people = [member("A", started="2025-12-01")]
    events = [event("A", "Bought coffee", 20, "2025-12-01")]
    report = coffee_averages(people, events, timestamp("2026-03-02"))
    assert report["recent"]["cups"] == 41
    assert report["recent"]["cost_per_cup"] == 0
    assert report["recent"]["kg_per_month"] == 0
    assert report["lifetime"]["cost_per_cup"] > 0


def test_lifetime_average_reports_its_actual_elapsed_months():
    from coffee_accounting import coffee_averages

    purchase = event("A", "Bought coffee", 3929.9, "2021-11-15")
    purchase["description"] = "bought 160.75kg coffee"
    lifetime = coffee_averages([], [purchase], timestamp("2026-10-01"))["lifetime"]
    assert lifetime["months"] == pytest.approx(1781 / 30.4375)
    assert lifetime["kg_per_month"] == pytest.approx(2.7472364542391915)


def test_new_ledger_and_missing_quantities_do_not_invent_averages():
    from coffee_accounting import coffee_averages

    purchase = event("A", "Bought coffee", 20)
    same_day = coffee_averages([member("A")], [purchase], timestamp("2026-01-05"))
    assert same_day["lifetime"]["months"] == 0
    assert same_day["lifetime"]["kg_per_month"] is None
    assert same_day["recent"]["cost_per_cup"] is None
    purchase["description"] = "coffee without a recorded quantity"
    later = coffee_averages([member("A")], [purchase], timestamp("2026-01-12"))
    assert later["lifetime"]["kg_per_month"] is None
    assert later["recent"]["kg_per_month"] is None
    assert later["recent"]["cost_per_cup"] == 4


def test_trend_cups_respect_joining_and_departure_dates():
    from coffee_accounting import coffee_averages

    people = [
        member("Left before window", started="2025-12-01", left="2025-12-31"),
        member("Left in window", started="2026-01-05", left="2026-01-09"),
        member("Joined in window", started="2026-02-23"),
        member("Future", started="2026-03-03"),
    ]
    recent = coffee_averages(people, [], timestamp("2026-03-02"))["recent"]
    assert recent["cups"] == 9  # Four weekdays for the leaver, five for the joiner.
