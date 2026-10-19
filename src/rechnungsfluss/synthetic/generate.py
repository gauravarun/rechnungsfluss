"""Generate the labelled synthetic evaluation set.

    python -m rechnungsfluss.synthetic.generate --out eval/dataset [--seed 20261019]

Writes, under ``--out``:

- ``files/<tenant>/<doc_id>.<ext>``: the documents (gitignored; regenerate)
- ``manifest.jsonl``: one label per document (committed)
- ``purchase_orders.json``: POs the 2-way match runs against (committed)

Generation is deterministic for a given seed. Labels describe the expected
outcome of the *finished* pipeline; the validation part of every label is
checked against the real KoSIT validator in tests/integration.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import random
from collections.abc import Callable, Iterator
from dataclasses import asdict, dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from pathlib import Path

from rechnungsfluss.domain.invoice import Invoice, Party
from rechnungsfluss.formats import SPEC_EN16931, cii, ubl, zugferd
from rechnungsfluss.synthetic import defects
from rechnungsfluss.synthetic.builder import LineSpec, build_invoice
from rechnungsfluss.synthetic.masterdata import (
    CATALOGUE,
    TENANTS,
    Supplier,
    TenantProfile,
    buyer_party,
    suppliers,
)
from rechnungsfluss.synthetic.render import render_pdf

DEFAULT_SEED = 20261019
GENERATOR_VERSION = "1"


class Category(StrEnum):
    CLEAN = "clean"
    NOISY = "noisy"
    WRONG_TOTALS = "wrong_totals"
    MISSING_FIELDS = "missing_fields"
    PO_MISMATCH = "po_mismatch"
    DUPLICATE = "duplicate"


class Route(StrEnum):
    AUTO_APPROVE = "auto_approve"
    REVIEW = "review"
    SKIP_DUPLICATE_DELIVERY = "skip_duplicate_delivery"
    """Byte-identical re-delivery: dropped by ingest idempotency (ADR-0007)."""


# Our own review reasons (not EN 16931 rules); the W3 pipeline must produce them.
REASON_VALIDATION = "validation_failed"
REASON_NOT_E_INVOICE = "not_an_e_invoice"
REASON_NO_PO = "no_po_reference"
REASON_PO_PRICE = "po_price_mismatch"
REASON_PO_QTY = "po_quantity_mismatch"
REASON_DUPLICATE = "duplicate_suspected"
REASON_NO_DUE_DATE = "missing_due_date"

PER_TENANT = {
    Category.CLEAN: 22,
    Category.NOISY: 8,
    Category.WRONG_TOTALS: 9,
    Category.MISSING_FIELDS: 9,
    Category.PO_MISMATCH: 9,
}
DUPLICATES_PER_TENANT = {"exact_resend": 3, "other_format": 3, "fuzzy": 3}

_NOISY_EXTRAS = (
    "Sonderanfertigung lt. Zeichnung Nr. 4711-A & Rev. <B>",
    'Kleinteile-Sortiment "Werkstatt" – Größe XL, ölbeständig',  # noqa: RUF001 (deliberate noise)
    "Ersatzteil für Anlage Ä/Ö/Ü-Linie, Pos. 12½",
)


@dataclass
class PurchaseOrderLine:
    line_no: int
    description: str
    quantity: str
    unit_code: str
    net_price: str


@dataclass
class PurchaseOrder:
    tenant: str
    number: str
    supplier_vat_id: str | None
    currency: str
    lines: list[PurchaseOrderLine]


@dataclass
class Label:
    doc_id: str
    tenant: str
    file: str
    format: str
    """cii | ubl | zugferd | pdf"""
    category: str
    variant: str
    sha256: str
    """SHA-256 of the file; for ZUGFeRD of the embedded XML, because factur-x stamps the
    PDF metadata with the generation time."""
    specification_id: str | None
    """BT-24 written into the document (None for plain PDFs)."""
    expected_validation: str
    """accept | reject | not_an_e_invoice. For plain PDFs: the result for a perfect
    extraction serialized as EN 16931 CII."""
    expected_rule_ids: list[str]
    expected_route: str
    review_reasons: list[str]
    duplicate_of: str | None = None
    purchase_order: str | None = None
    truth: dict[str, object] | None = None
    """Canonical invoice (JSON) the document encodes; None for MINIMUM-profile files."""
    notes: list[str] = field(default_factory=list)


@dataclass
class _Draft:
    invoice: Invoice
    category: Category
    variant: str
    rule_ids: tuple[str, ...] = ()
    reasons: list[str] = field(default_factory=list)
    po: PurchaseOrder | None = None
    format: str | None = None
    minimum_profile: bool = False
    xrechnung_rule_ids: tuple[str, ...] = ()


def _money(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _pick_format(profile: TenantProfile, rng: random.Random) -> str:
    names = [f for f, _ in profile.formats]
    weights = [w for _, w in profile.formats]
    return rng.choices(names, weights=weights, k=1)[0]


def _lines(profile: TenantProfile, rng: random.Random, *, noisy: bool) -> list[LineSpec]:
    count = rng.randint(10, 25) if noisy else rng.randint(1, 5)
    specs = []
    for _ in range(count):
        product = rng.choice(CATALOGUE)
        desc = product.description
        if noisy and rng.random() < 0.4:
            desc = rng.choice(_NOISY_EXTRAS)
        qty = (
            Decimal(rng.randint(1, 400)) / Decimal(4)
            if product.unit_code in {"KGM", "LTR", "MTR", "HUR"}
            else Decimal(rng.randint(1, 60))
        )
        rate = profile.reduced_rate if product.reduced else profile.standard_rate
        specs.append(
            LineSpec(
                description=desc,
                quantity=qty,
                unit_code=product.unit_code,
                net_price=product.net_price,
                vat_category="S",
                vat_rate=rate,
            )
        )
    return specs


def _po_for(profile: TenantProfile, number: str, supplier: Party, inv: Invoice) -> PurchaseOrder:
    return PurchaseOrder(
        tenant=profile.tenant,
        number=number,
        supplier_vat_id=supplier.vat_id,
        currency=inv.currency,
        lines=[
            PurchaseOrderLine(
                line_no=int(line.line_id),
                description=line.description or "",
                quantity=f"{line.quantity:f}",
                unit_code=line.unit_code,
                net_price=f"{line.net_price:f}",
            )
            for line in inv.lines
        ],
    )


class _TenantGenerator:
    def __init__(self, profile: TenantProfile, seed: int) -> None:
        self.p = profile
        self.rng = random.Random(f"{seed}-{profile.tenant}")
        self.buyer = buyer_party(profile)
        self.suppliers = suppliers(profile)
        self.seq = 0

    def _base(self, *, noisy: bool = False) -> tuple[Invoice, Supplier, PurchaseOrder]:
        self.seq += 1
        supplier = self.rng.choice(self.suppliers)
        po_number = f"PO-{self.p.country}-{4000 + self.seq}"
        issue = dt.date(2026, 9, 1) + dt.timedelta(days=self.rng.randint(0, 44))
        prefix = "".join(w[0] for w in supplier.party.name.split()[:2]).upper()
        inv = build_invoice(
            number=f"{prefix}-2026-{self.rng.randint(10000, 99999)}",
            issue_date=issue,
            seller=supplier.party,
            buyer=self.buyer,
            lines=_lines(self.p, self.rng, noisy=noisy),
            due_days=self.rng.choice([14, 30, 30, 45]),
            buyer_reference=self.p.buyer_reference,
            purchase_order_ref=po_number,
            seller_iban=supplier.iban,
        )
        return inv, supplier, _po_for(self.p, po_number, supplier.party, inv)

    def drafts(self) -> Iterator[_Draft]:
        for _ in range(PER_TENANT[Category.CLEAN]):
            inv, _, po = self._base()
            yield _Draft(inv, Category.CLEAN, "clean", po=po)
        for _ in range(PER_TENANT[Category.NOISY]):
            inv, _, po = self._base(noisy=True)
            yield _Draft(inv, Category.NOISY, "many_lines_special_chars", po=po)
        yield from self._wrong_totals()
        yield from self._missing_fields()
        yield from self._po_mismatch()

    def _wrong_totals(self) -> Iterator[_Draft]:
        makers: list[Callable[[Invoice, Decimal], defects.Defect]] = [
            defects.wrong_grand_total,
            defects.wrong_line_sum,
            defects.wrong_vat_amount,
        ]
        for i in range(PER_TENANT[Category.WRONG_TOTALS]):
            inv, _, po = self._base()
            maker = makers[i % len(makers)]
            choices = ["2.00", "5.00", "10.00", "-5.00"]
            if maker is not defects.wrong_vat_amount:
                choices += ["0.01", "0.10"]  # exact-equality rules: any delta is detectable
            delta = Decimal(self.rng.choice(choices))
            d = maker(inv, delta)
            yield _Draft(
                d.invoice, Category.WRONG_TOTALS, d.name, d.rule_ids, [REASON_VALIDATION], po
            )

    def _missing_fields(self) -> Iterator[_Draft]:
        variants = ["seller_vat_id", "due_date", "po_reference", "minimum_profile"]
        if self.p.specification_id != SPEC_EN16931:
            variants.append("buyer_reference")
        for i in range(PER_TENANT[Category.MISSING_FIELDS]):
            inv, _, po = self._base()
            variant = variants[i % len(variants)]
            if variant == "seller_vat_id":
                d = defects.missing_seller_vat_id(inv)
                yield _Draft(
                    d.invoice,
                    Category.MISSING_FIELDS,
                    d.name,
                    d.rule_ids,
                    [REASON_VALIDATION],
                    po,
                    xrechnung_rule_ids=d.xrechnung_rule_ids,
                )
            elif variant == "due_date":
                d = defects.missing_due_date(inv)
                yield _Draft(
                    d.invoice, Category.MISSING_FIELDS, d.name, (), [REASON_NO_DUE_DATE], po
                )
            elif variant == "po_reference":
                d = defects.missing_po_reference(inv)
                yield _Draft(d.invoice, Category.MISSING_FIELDS, d.name, (), [REASON_NO_PO], po)
            elif variant == "buyer_reference":
                d = defects.missing_buyer_reference(inv)
                # Only an XRechnung (structured) document carries the CIUS rules.
                yield _Draft(
                    d.invoice,
                    Category.MISSING_FIELDS,
                    d.name,
                    d.rule_ids,
                    [REASON_VALIDATION],
                    po,
                    format=self.rng.choice(["cii", "ubl"]),
                )
            else:
                yield _Draft(
                    inv,
                    Category.MISSING_FIELDS,
                    "zugferd_minimum_profile",
                    (),
                    [REASON_NOT_E_INVOICE],
                    po,
                    format="zugferd",
                    minimum_profile=True,
                )

    def _po_mismatch(self) -> Iterator[_Draft]:
        for i in range(PER_TENANT[Category.PO_MISMATCH]):
            inv, _, po = self._base()
            line = po.lines[0]
            if i % 2 == 0:
                # PO price 5-15 % below the invoiced price.
                factor = Decimal(100 - self.rng.randint(5, 15)) / 100
                line.net_price = f"{_money(Decimal(line.net_price) * factor):f}"
                yield _Draft(inv, Category.PO_MISMATCH, "price_above_po", (), [REASON_PO_PRICE], po)
            else:
                ordered = Decimal(line.quantity)
                line.quantity = f"{max(ordered / 2, Decimal('0.25')):f}"
                yield _Draft(
                    inv, Category.PO_MISMATCH, "quantity_above_po", (), [REASON_PO_QTY], po
                )


def _serialize(
    draft_format: str, inv: Invoice, profile: TenantProfile, minimum_profile: bool
) -> tuple[bytes, str | None]:
    if minimum_profile:
        return _minimum_zugferd(inv), "urn:factur-x.eu:1p0:minimum"
    if draft_format == "cii":
        return cii.to_cii(inv, specification_id=profile.specification_id), profile.specification_id
    if draft_format == "ubl":
        return ubl.to_ubl(inv, specification_id=profile.specification_id), profile.specification_id
    if draft_format == "zugferd":
        return zugferd.embed(render_pdf(inv), inv), SPEC_EN16931
    return render_pdf(inv), None


def _minimum_zugferd(inv: Invoice) -> bytes:
    """Factur-X MINIMUM hybrid: header data only (not an e-invoice per BMF, ⚠️ see standards)."""
    import facturx

    out: bytes = facturx.generate_from_binary(
        render_pdf(inv),
        cii.to_cii_minimum(inv),
        flavor="factur-x",
        level="minimum",
        check_xsd=True,
    )
    return out


_EXT = {"cii": "xml", "ubl": "xml", "zugferd": "pdf", "pdf": "pdf"}


def _content_hash(fmt: str, data: bytes) -> str:
    if fmt == "zugferd":
        data = zugferd.attachment(data)[1]
    return hashlib.sha256(data).hexdigest()


def _truth(inv: Invoice) -> dict[str, object]:
    """Full canonical invoice as JSON; Invoice.model_validate(truth) restores it."""
    return inv.model_dump(mode="json", exclude={"provenance"})


def generate(out: Path, seed: int = DEFAULT_SEED) -> list[Label]:
    labels: list[Label] = []
    pos: list[PurchaseOrder] = []
    for profile in TENANTS:
        gen = _TenantGenerator(profile, seed)
        tenant_labels: list[tuple[Label, Invoice, bytes]] = []
        for n, draft in enumerate(gen.drafts(), start=1):
            fmt = draft.format or _pick_format(profile, gen.rng)
            data, spec = _serialize(fmt, draft.invoice, profile, draft.minimum_profile)
            doc_id = f"{profile.tenant}-{n:03d}"
            if draft.po and draft.variant != "missing_po_reference":
                pos.append(draft.po)
            rule_ids = draft.rule_ids
            if spec is not None and spec != SPEC_EN16931 and not draft.minimum_profile:
                rule_ids = rule_ids + draft.xrechnung_rule_ids
            if draft.minimum_profile:
                validation, route = "not_an_e_invoice", Route.REVIEW
            elif rule_ids:
                validation, route = "reject", Route.REVIEW
            else:
                validation = "accept"
                route = Route.REVIEW if draft.reasons else Route.AUTO_APPROVE
            label = Label(
                doc_id=doc_id,
                tenant=profile.tenant,
                file=f"files/{profile.tenant}/{doc_id}.{_EXT[fmt]}",
                format=fmt,
                category=draft.category.value,
                variant=draft.variant,
                sha256=_content_hash(fmt, data),
                specification_id=spec,
                expected_validation=validation,
                expected_rule_ids=sorted(rule_ids),
                expected_route=route.value,
                review_reasons=draft.reasons,
                purchase_order=draft.invoice.purchase_order_ref,
                truth=None if draft.minimum_profile else _truth(draft.invoice),
            )
            _write(out / label.file, data)
            tenant_labels.append((label, draft.invoice, data))
        labels.extend(lbl for lbl, _, _ in tenant_labels)
        labels.extend(_duplicates(gen, profile, tenant_labels, out))
    _write(
        out / "manifest.jsonl",
        "".join(
            json.dumps(asdict(lbl), ensure_ascii=False, sort_keys=True) + "\n" for lbl in labels
        ).encode(),
    )
    _write(
        out / "purchase_orders.json",
        json.dumps(
            {
                "generator_version": GENERATOR_VERSION,
                "seed": seed,
                "purchase_orders": [asdict(po) for po in pos],
            },
            ensure_ascii=False,
            indent=1,
            sort_keys=True,
        ).encode()
        + b"\n",
    )
    return labels


def _duplicates(
    gen: _TenantGenerator,
    profile: TenantProfile,
    originals: list[tuple[Label, Invoice, bytes]],
    out: Path,
) -> list[Label]:
    clean = [o for o in originals if o[0].category == Category.CLEAN.value]
    picks = gen.rng.sample(clean, sum(DUPLICATES_PER_TENANT.values()))
    labels = []
    n = len(originals)
    i = 0
    for kind, count in DUPLICATES_PER_TENANT.items():
        for _ in range(count):
            original, inv, data = picks[i]
            i += 1
            n += 1
            doc_id = f"{profile.tenant}-{n:03d}"
            fmt, spec = original.format, original.specification_id
            route, reasons = Route.REVIEW, [REASON_DUPLICATE]
            if kind == "exact_resend":
                route, reasons = Route.SKIP_DUPLICATE_DELIVERY, []
            elif kind == "other_format":
                fmt = "pdf" if original.format != "pdf" else "cii"
                data, spec = _serialize(fmt, inv, profile, False)
            else:  # fuzzy: new number, date shifted, same seller and amount
                inv = inv.model_copy(
                    update={
                        "number": inv.number + "-R",
                        "issue_date": inv.issue_date + dt.timedelta(days=gen.rng.randint(1, 3)),
                    }
                )
                data, spec = _serialize(fmt, inv, profile, False)
            label = Label(
                doc_id=doc_id,
                tenant=profile.tenant,
                file=f"files/{profile.tenant}/{doc_id}.{_EXT[fmt]}",
                format=fmt,
                category=Category.DUPLICATE.value,
                variant=kind,
                sha256=_content_hash(fmt, data),
                specification_id=spec,
                expected_validation="accept",
                expected_rule_ids=[],
                expected_route=route.value,
                review_reasons=reasons,
                duplicate_of=original.doc_id,
                purchase_order=inv.purchase_order_ref,
                truth=_truth(inv),
            )
            _write(out / label.file, data)
            labels.append(label)
    return labels


def _write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--out", type=Path, default=Path("eval/dataset"))
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args(argv)
    labels = generate(args.out, args.seed)
    by_cat: dict[str, int] = {}
    for lbl in labels:
        by_cat[lbl.category] = by_cat.get(lbl.category, 0) + 1
    print(f"wrote {len(labels)} documents to {args.out}: {by_cat}")  # noqa: T201


if __name__ == "__main__":
    main()
