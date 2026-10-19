"""Fictional master data: tenants (buyers), suppliers and a product catalogue.

All names are invented and carry an obvious fictional marker where a real
company could plausibly share the name. Addresses use invented streets in
invented towns; postcodes are random.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from decimal import Decimal

from rechnungsfluss.domain.invoice import Contact, Party, PostalAddress
from rechnungsfluss.formats import SPEC_EN16931, SPEC_XRECHNUNG_3_0
from rechnungsfluss.synthetic.identifiers import fake_iban, fake_vat_id


@dataclass(frozen=True, slots=True)
class TenantProfile:
    tenant: str
    country: str
    buyer_name: str
    standard_rate: Decimal
    reduced_rate: Decimal
    specification_id: str
    """BT-24 used by this tenant's suppliers for structured invoices."""
    buyer_reference: str
    """BT-10. For nordwerk-de modelled after a Leitweg-ID shape."""
    formats: tuple[tuple[str, int], ...]
    """(format, weight) for picking an input format."""


TENANTS: tuple[TenantProfile, ...] = (
    TenantProfile(
        tenant="nordwerk-de",
        country="DE",
        buyer_name="Nordwerk GmbH (fiktiv)",
        standard_rate=Decimal("19"),
        reduced_rate=Decimal("7"),
        specification_id=SPEC_XRECHNUNG_3_0,
        buyer_reference="99000000-NORDWERK-01",
        formats=(("cii", 30), ("ubl", 25), ("zugferd", 25), ("pdf", 20)),
    ),
    TenantProfile(
        tenant="nordwerk-at",
        country="AT",
        buyer_name="Nordwerk Handels GmbH (fiktiv)",
        standard_rate=Decimal("20"),
        reduced_rate=Decimal("10"),
        specification_id=SPEC_EN16931,
        buyer_reference="NWAT-EINKAUF",
        formats=(("cii", 25), ("ubl", 25), ("zugferd", 30), ("pdf", 20)),
    ),
)

_SUPPLIER_NAMES = {
    "DE": [
        "Schraubenwerk Lindenau KG (fiktiv)",
        "Hydraulik Brennecke GmbH (fiktiv)",
        "Arbeitsschutz Vogtmann e.K. (fiktiv)",
        "Kugellager Ostermeier GmbH (fiktiv)",
        "Werkzeughandel Quernheim OHG (fiktiv)",
        "Elektrobedarf Sandhagen GmbH (fiktiv)",
        "Druckluft Technik Wiesmoor GmbH (fiktiv)",
        "Verpackung Hollerbeck AG (fiktiv)",
    ],
    "AT": [
        "Stahlbau Kirchtaler GmbH (fiktiv)",
        "Alpen Hydraulik Prandstetter KG (fiktiv)",
        "Sicherheitstechnik Moosbrugger GmbH (fiktiv)",
        "Lager & Antrieb Hinterseer OG (fiktiv)",
        "Werkzeug Gruberhof GmbH (fiktiv)",
        "Elektro Steinwandner e.U. (fiktiv)",
    ],
}

_TOWNS = {
    "DE": ["Lindenau", "Brennstadt", "Vogtsberg", "Ostermark", "Quernfeld", "Sandhagen"],
    "AT": ["Kirchtal", "Prandau", "Moosbach am See", "Hinterberg", "Gruberdorf"],
}

_STREETS = ["Industriestraße", "Am Werkhof", "Gewerbering", "Lagerweg", "Hafenstraße"]


@dataclass(frozen=True, slots=True)
class Product:
    description: str
    unit_code: str
    """UN/ECE Rec 20: H87 piece, KGM kilogram, LTR litre, MTR metre, C62 one/unit."""
    net_price: Decimal
    reduced: bool = False
    """True: reduced VAT rate applies (e.g. printed manuals, food for the canteen)."""


CATALOGUE: tuple[Product, ...] = (
    Product("Sechskantschraube M8x40, verzinkt (100 Stk.)", "H87", Decimal("12.50")),
    Product("Sechskantmutter M8, verzinkt (100 Stk.)", "H87", Decimal("6.90")),
    Product("Rillenkugellager 6204-2RS", "H87", Decimal("4.35")),
    Product("Hydrauliköl HLP 46", "LTR", Decimal("3.15")),
    Product("Schutzhandschuhe Nitril, Gr. 9 (Paar)", "H87", Decimal("2.40")),
    Product("Gehörschutzstöpsel (Box à 200)", "H87", Decimal("38.00")),
    Product("Druckluftschlauch PU 8x6 mm", "MTR", Decimal("1.85")),
    Product("Kabelbinder 300x4,8 mm (100 Stk.)", "H87", Decimal("5.20")),
    Product("Stretchfolie 500 mm, 23 µ", "KGM", Decimal("4.75")),
    Product("Schweißdraht SG2 1,0 mm", "KGM", Decimal("3.95")),
    Product("Montagestunde Servicetechniker", "HUR", Decimal("78.00")),
    Product("Fachbuch Hydraulik-Grundlagen", "H87", Decimal("49.90"), reduced=True),
    Product("Kaffeebohnen für Kantine 1 kg", "KGM", Decimal("16.80"), reduced=True),
)


@dataclass(frozen=True, slots=True)
class Supplier:
    party: Party
    iban: str


def buyer_party(profile: TenantProfile) -> Party:
    return Party(
        name=profile.buyer_name,
        vat_id=fake_vat_id(profile.country, random.Random(f"buyer-{profile.tenant}")),
        address=PostalAddress(
            line1="Werkstraße 7",
            city="Nordstadt" if profile.country == "DE" else "Nordau",
            post_code="54321" if profile.country == "DE" else "5432",
            country_code=profile.country,
        ),
        electronic_address=f"rechnungseingang@{profile.tenant}.example",
    )


def suppliers(profile: TenantProfile) -> list[Supplier]:
    rng = random.Random(f"suppliers-{profile.tenant}")
    out = []
    for i, name in enumerate(_SUPPLIER_NAMES[profile.country]):
        slug = f"lieferant{i + 1}"
        out.append(
            Supplier(
                party=Party(
                    name=name,
                    vat_id=fake_vat_id(profile.country, rng),
                    address=PostalAddress(
                        line1=f"{rng.choice(_STREETS)} {rng.randint(1, 120)}",
                        city=rng.choice(_TOWNS[profile.country]),
                        post_code=f"{rng.randint(10000, 99999)}"
                        if profile.country == "DE"
                        else f"{rng.randint(1000, 9999)}",
                        country_code=profile.country,
                    ),
                    electronic_address=f"rechnung@{slug}.{profile.country.lower()}.example",
                    contact=Contact(
                        name="Buchhaltung",
                        phone="+49 30 0000000" if profile.country == "DE" else "+43 1 0000000",
                        email=f"buchhaltung@{slug}.{profile.country.lower()}.example",
                    ),
                ),
                iban=fake_iban(profile.country, rng),
            )
        )
    return out
