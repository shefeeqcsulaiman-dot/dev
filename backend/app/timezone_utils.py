"""Per-company UTC offset for biometric attendance timestamp parsing.

Devices (ZKTeco/Suprema/Hikvision/Anviz) and CSV imports report their own
local wall-clock time with no timezone info — a fixed offset used to be
hardcoded to UAE Standard Time (+4) in three separate places (attendance.py,
biotime_sync.py, app_data.py), silently corrupting every punch's date/time
for any non-UAE company (this app already supports several other GCC
countries — see auth.py's _COUNTRY_DEFAULTS for the equivalent
currency/VAT-rate pattern this mirrors).

Scoped to the GCC countries this app actually seeds currency/VAT defaults
for (excluding Qatar's UK sibling entry, United Kingdom, which observes
DST and needs real timezone-database handling, not a fixed offset — falls
through to the UAE default below, unchanged from previous behavior, not a
regression). All five GCC countries here have fixed offsets with no DST.
"""

from datetime import timedelta

_COUNTRY_UTC_OFFSET_HOURS = {
    "United Arab Emirates": 4,
    "Saudi Arabia": 3,
    "Bahrain": 3,
    "Kuwait": 3,
    "Oman": 4,
    "Qatar": 3,
}

_DEFAULT_OFFSET_HOURS = 4  # UAE — this app's original/primary market


def company_utc_offset(country: str | None) -> timedelta:
    return timedelta(hours=_COUNTRY_UTC_OFFSET_HOURS.get((country or "").strip(), _DEFAULT_OFFSET_HOURS))
