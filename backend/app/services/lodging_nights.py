"""Calendar nights shared by the cost report and departure checklist."""
from datetime import date, timedelta


def lodging_dates(plan):
    try:
        first,last=date.fromisoformat(plan.start_date),date.fromisoformat(plan.end_date)
        count=(last-first).days
        if not 0<=count<30:return None
    except (TypeError,ValueError):
        return None
    return [(first+timedelta(days=i)).isoformat() for i in range(count)]


def hotel_for_night(plan,night):
    matches=[day for day in plan.days if day.date==night]
    # Duplicate dates are invalid; do not silently pick one hotel's details.
    return matches[0].hotel if len(matches)==1 else None
