"""Synthetic identifiers that cannot belong to a real company or bank account.

- **VAT IDs** carry a deliberately *wrong* check digit, so they fail the
  official checksum algorithm and can never be a real, issued VAT ID.
  The EN 16931 / XRechnung rules don't verify VAT ID checksums, so the
  invoices still validate.
- **IBANs** carry a *valid* ISO 13616 checksum (XRechnung's BR-DE-19 checks it
  for SEPA transfers) but use an all-zero bank code.
"""

from __future__ import annotations

import random

from stdnum import iban as std_iban
from stdnum.at import uid as at_uid
from stdnum.iso7064 import mod_11_10


def fake_vat_id(country: str, rng: random.Random) -> str:
    if country == "DE":
        body = "".join(rng.choice("0123456789") for _ in range(8))
        body = str(rng.randint(1, 9)) + body[1:]
        good = mod_11_10.calc_check_digit(body)
        return f"DE{body}{(int(good) + 1 + rng.randint(0, 8)) % 10}"
    if country == "AT":
        body = "".join(rng.choice("0123456789") for _ in range(7))
        good = at_uid.calc_check_digit("U" + body)
        return f"ATU{body}{(int(good) + 1 + rng.randint(0, 8)) % 10}"
    raise ValueError(f"unsupported country {country}")


def fake_iban(country: str, rng: random.Random) -> str:
    if country == "DE":
        bban = "00000000" + "".join(rng.choice("0123456789") for _ in range(10))
    elif country == "AT":
        bban = "00000" + "".join(rng.choice("0123456789") for _ in range(11))
    else:
        raise ValueError(f"unsupported country {country}")
    check = std_iban.calc_check_digits(f"{country}00{bban}")
    return f"{country}{check}{bban}"
