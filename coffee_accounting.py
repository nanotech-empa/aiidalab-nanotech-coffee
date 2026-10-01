"""Read-only coffee reports from plain member and event records.

Dates follow the app's convention: membership and consumption intervals include
their start and exclude their end. Today's cups have not yet been consumed.
"""

from calendar import monthrange
from datetime import date, datetime
import re

import numpy as np


def record_date(value):
    """Convert stored local timestamps or dates to a calendar date."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromtimestamp(float(value)).date()


def consumption_interval(event):
    """Recover the exclusive end of a stored business-day interval."""
    start = np.datetime64(record_date(event["datei"]))
    end = np.busday_offset(start, int(event["amount"]), roll="forward")
    return start, end


def _member_consumption(member, entries, cutoff, since=None):
    """Estimate cups within a period using settings from the full history."""
    started = record_date(member["started"])
    left_value = member.get("extras", {}).get("left")
    left = record_date(left_value) if left_value is not None else None
    end = min(cutoff, left) if left is not None else cutoff
    start = max(started, since) if since is not None else started
    days = np.arange(np.datetime64(start), np.datetime64(max(start, end)))
    days = days[np.is_busday(days)]
    member_events = [e for e in entries if e["person"] == member["name"]]
    # Entries are chronological. Defaults apply retrospectively; latest wins.
    coeff = 1.0
    for entry in member_events:
        if entry["event"] == "default_cups/day":
            coeff = float(entry["Range_cups/day"])
    rates = np.full(len(days), coeff, dtype=float)
    for entry in member_events:
        if entry["event"] == "Range_cups/day":
            start_range, end_range = consumption_interval(entry)
            rates[(days >= start_range) & (days < end_range)] = entry["Range_cups/day"]
    # Absence takes precedence, including overlapping absences.
    for entry in member_events:
        if entry["event"] == "Absence":
            start_range, end_range = consumption_interval(entry)
            rates[(days >= start_range) & (days < end_range)] = 0.0
    return {"days": len(days), "cups": float(rates.sum()), "coeff": coeff}


def coffee_kg_from_description(description):
    """Read the kilogram quantity stored in existing purchase descriptions."""
    match = re.search(
        r"bought\s+([0-9]+(?:\.[0-9]+)?)\s*kg",
        description or "", re.IGNORECASE,
    )
    return float(match.group(1)) if match else None


def calculate_ledger(members, events, as_of=None, before_departures=False):
    """Calculate a dated report without loading or changing AiiDA nodes.

    The existing redistribution rule is retained: former members' unadjusted
    balances correct the total cost allocated to current members. Historical
    departure reports use this same rule immediately before departure.
    """
    cutoff = record_date(as_of) if as_of is not None else date.today()
    entries = sorted(
        (e for e in events if record_date(e["datei"]) <= cutoff),
        key=lambda e: (float(e["datei"]), e.get("created", "")),
    )
    people = {}
    for member in members:
        started = record_date(member["started"])
        if started > cutoff:
            continue
        left_value = member.get("extras", {}).get("left")
        left = record_date(left_value) if left_value is not None else None
        consumption = _member_consumption(member, entries, cutoff)
        has_left = left is not None and (
            left < cutoff if before_departures else left <= cutoff
        )
        people[member["name"]] = {
            "uuid": member.get("uuid"),
            "since": started.isoformat(),
            "left": left.isoformat() if has_left else "",
            **consumption,
            "coffee": 0.0,
            "other": 0.0,
            "cash": 0.0,
        }

    cash = total_cost = total_coffee_cost = total_coffee_kg = 0.0
    first_coffee_date = None
    for entry in entries:
        person = people.get(entry["person"])
        amount = float(entry["amount"])
        what = entry["event"]
        if "cash" in what:
            cash += amount
            if "Donated" not in what and person is not None:
                person["cash"] += amount
        elif "Bought" in what:
            total_cost += amount
            if "coffee" in what:
                total_coffee_cost += amount
                if person is not None:
                    person["coffee"] += amount
                kg = coffee_kg_from_description(entry.get("description", ""))
                if kg is not None:
                    total_coffee_kg += kg
                bought_on = record_date(entry["datei"])
                first_coffee_date = min(first_coffee_date, bought_on) if first_coffee_date else bought_on
            elif person is not None:
                person["other"] += amount

    total_cups = sum(p["cups"] for p in people.values())
    for person in people.values():
        paid = person["coffee"] + person["other"] + person["cash"]
        person["unadjusted_balance"] = (
            paid - total_cost * person["cups"] / total_cups if total_cups > 0
            else paid if total_cost == 0 else None
        )
    cost_correction = sum(
        p["unadjusted_balance"] for p in people.values()
        if p["left"] and p["unadjusted_balance"] is not None
    )
    for person in people.values():
        paid = person["coffee"] + person["other"] + person["cash"]
        person["balance"] = (
            0.0 if person["left"]
            else paid - (total_cost - cost_correction) * person["cups"] / total_cups
            if total_cups > 0 else paid if total_cost == 0 else None
        )
    return {
        "people": people,
        "events": entries[::-1],
        "cash": cash,
        "cost_correction": cost_correction,
        "total_cost": total_cost,
        "total_coffee_cost": total_coffee_cost,
        "total_coffee_kg": total_coffee_kg,
        "total_cups": total_cups,
        "first_coffee_date": first_coffee_date,
        "cost_per_cup": total_coffee_cost / total_cups if total_cups > 0 else None,
    }


def coffee_averages(members, events, as_of=None, ledger=None):
    """Return lifetime averages and trends over the last two calendar months.

    Purchases include the reporting date, matching the lifetime ledger. Cup
    estimates count completed weekdays, with membership boundaries, absences
    and the latest known retrospective consumption settings applied.
    """
    cutoff = record_date(as_of) if as_of is not None else date.today()
    if ledger is None:
        ledger = calculate_ledger(members, events, as_of=cutoff)
    year, month_index = divmod(cutoff.year * 12 + cutoff.month - 3, 12)
    month = month_index + 1
    window_start = cutoff.replace(
        year=year, month=month, day=min(cutoff.day, monthrange(year, month)[1])
    )
    entries = list(reversed(ledger["events"]))
    purchases = [e for e in entries if e["event"] == "Bought coffee"]
    recent = [e for e in purchases if record_date(e["datei"]) >= window_start]

    def purchase_totals(records):
        quantities = [coffee_kg_from_description(e.get("description", "")) for e in records]
        return {
            "cost": sum(float(e["amount"]) for e in records),
            "kg": sum(quantities) if all(kg is not None for kg in quantities) else None,
        }

    lifetime = purchase_totals(purchases)
    first_date = ledger["first_coffee_date"]
    months = (cutoff - first_date).days / 30.4375 if first_date is not None else 0.0
    lifetime.update(
        months=months, start=first_date, end=cutoff,
        cups=ledger["total_cups"], cost_per_cup=ledger["cost_per_cup"],
        kg_per_month=lifetime["kg"] / months if lifetime["kg"] is not None and months > 0 else None,
    )
    recent_totals = purchase_totals(recent)
    recent_cups = sum(
        _member_consumption(member, entries, cutoff, since=window_start)["cups"]
        for member in members
    )
    recent_totals.update(
        months=2.0, start=window_start, end=cutoff, cups=recent_cups,
        kg_per_month=recent_totals["kg"] / 2.0 if recent_totals["kg"] is not None else None,
        cost_per_cup=recent_totals["cost"] / recent_cups if recent_cups > 0 else None,
    )
    return {"lifetime": lifetime, "recent": recent_totals}


def departure_balances(members, events, as_of=None):
    """Reconstruct balances at departure from events dated through that day.

    These are reconstructed estimates, not stored settlement snapshots. Later
    purchases, consumption and membership starts cannot change these reports.
    Backdated corrections to the historical records can change them.
    """
    cutoff = record_date(as_of) if as_of is not None else date.today()
    reports = {}
    balances = {}
    for member in members:
        left = member.get("extras", {}).get("left")
        if left is None or record_date(left) > cutoff:
            continue
        left = record_date(left)
        if left not in reports:
            reports[left] = calculate_ledger(
                members, events, as_of=left, before_departures=True
            )
        balances[member["name"]] = reports[left]["people"][member["name"]]["balance"]
    return balances
