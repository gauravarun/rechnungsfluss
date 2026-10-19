import datetime as dt
from decimal import Decimal

import pytest

from rechnungsfluss.domain.duplicates import (
    DuplicateHit,
    find_duplicates,
    fingerprint,
    number_key,
)
from rechnungsfluss.domain.invoice import Invoice, InvoiceLine, Party
from rechnungsfluss.domain.matching import MatchIssue, PurchaseOrder, PurchaseOrderLine, match
from rechnungsfluss.domain.reasons import Reason
from rechnungsfluss.domain.routing import Route, decide
from rechnungsfluss.domain.tenancy import TenantId
from rechnungsfluss.domain.tenant_config import (
    DuplicateTolerances,
    MatchTolerances,
    UnknownTenantError,
    config_for,
)
from tests.conftest import make_invoice

TOL = MatchTolerances()


def _po(**overrides: object) -> PurchaseOrder:
    data: dict[str, object] = {
        "tenant": "nordwerk-de",
        "number": "PO-DE-1001",
        "supplier_vat_id": "DE999999999",
        "supplier_name": "Muster Schrauben KG (synthetic)",
        "currency": "EUR",
        "lines": (
            PurchaseOrderLine(
                line_no=1,
                description="Sechskantschraube M8",
                quantity=Decimal("10"),
                unit_code="H87",
                net_price=Decimal("12.50"),
            ),
        ),
    }
    data.update(overrides)
    return PurchaseOrder.model_validate(data)


def _with_line(inv: Invoice, **changes: object) -> Invoice:
    line = inv.lines[0].model_copy(update=changes)
    return inv.model_copy(update={"lines": (line,)})


def _reasons(issues: list[MatchIssue]) -> set[Reason]:
    return {i.reason for i in issues}


# --- matching -------------------------------------------------------------------


def test_clean_invoice_matches(invoice: Invoice) -> None:
    assert match(invoice, _po(), TOL) == []


def test_bt132_pairing_ignores_description(invoice: Invoice) -> None:
    inv = _with_line(invoice, order_line_ref="1", description="ganz anderer Text")
    assert match(inv, _po(), TOL) == []


def test_description_fallback_is_whitespace_and_case_insensitive(invoice: Invoice) -> None:
    inv = _with_line(invoice, description="  sechskantSCHRAUBE   m8 ")
    assert match(inv, _po(), TOL) == []


@pytest.mark.parametrize(
    ("changes", "reason"),
    [
        ({"order_line_ref": "7"}, Reason.PO_LINE_NOT_FOUND),
        ({"order_line_ref": "x"}, Reason.PO_LINE_NOT_FOUND),
        ({"description": "Mutter M8"}, Reason.PO_LINE_NOT_FOUND),
        ({"order_line_ref": "1", "unit_code": "KGM"}, Reason.PO_UNIT_MISMATCH),
        ({"net_price": Decimal("12.76")}, Reason.PO_PRICE_MISMATCH),  # > +2 %
        ({"quantity": Decimal("10.01")}, Reason.PO_QUANTITY_MISMATCH),
    ],
)
def test_line_issues(invoice: Invoice, changes: dict[str, object], reason: Reason) -> None:
    assert _reasons(match(_with_line(invoice, **changes), _po(), TOL)) == {reason}


def test_price_tolerance_boundary(invoice: Invoice) -> None:
    assert match(_with_line(invoice, net_price=Decimal("12.75")), _po(), TOL) == []  # exactly +2 %


def test_absolute_tolerance_applies_to_cheap_items(invoice: Invoice) -> None:
    po = _po(
        lines=(
            PurchaseOrderLine(
                line_no=1,
                description="Sechskantschraube M8",
                quantity=Decimal("10"),
                unit_code="H87",
                net_price=Decimal("0.10"),
            ),
        )
    )
    cheap = _with_line(invoice, net_price=Decimal("0.12"))  # +20 % but only +0.02
    assert match(cheap, po, TOL) == []


def test_lower_price_and_quantity_are_accepted(invoice: Invoice) -> None:
    inv = _with_line(invoice, net_price=Decimal("9.99"), quantity=Decimal("3"))
    assert match(inv, _po(), TOL) == []


def test_header_issues(invoice: Invoice) -> None:
    assert _reasons(match(invoice.model_copy(update={"purchase_order_ref": None}), None, TOL)) == {
        Reason.NO_PO_REFERENCE
    }
    assert _reasons(match(invoice, None, TOL)) == {Reason.PO_NOT_FOUND}
    assert _reasons(match(invoice, _po(currency="CHF"), TOL)) == {Reason.PO_CURRENCY_MISMATCH}
    assert _reasons(match(invoice, _po(supplier_vat_id="DE111111111"), TOL)) == {
        Reason.PO_SUPPLIER_MISMATCH
    }


def test_supplier_falls_back_to_name_when_vat_id_missing(invoice: Invoice) -> None:
    seller = Party.model_validate({**invoice.seller.model_dump(), "vat_id": None})
    inv = invoice.model_copy(update={"seller": seller})
    assert match(inv, _po(), TOL) == []
    assert _reasons(match(inv, _po(supplier_name="Jemand Anderes"), TOL)) == {
        Reason.PO_SUPPLIER_MISMATCH
    }


# --- duplicates -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("a", "b"), [("RE-2026-0042", "re 2026 42"), ("INV/007", "inv7"), ("A-001-B", "a1b")]
)
def test_number_key_normalization(a: str, b: str) -> None:
    assert number_key(a) == number_key(b)


def test_number_key_keeps_inner_zeros() -> None:
    assert number_key("RE-2026-1001") != number_key("RE-2026-11")


def test_number_key_keeps_run_boundaries() -> None:
    assert number_key("RE-20-26") != number_key("RE-2026")


def test_exact_duplicate_by_seller_and_number(invoice: Invoice) -> None:
    original = fingerprint("inv-1", invoice)
    resent = fingerprint("inv-2", invoice.model_copy(update={"number": "re 2026 1"}))
    assert find_duplicates(resent, [original], DuplicateTolerances()) == [
        DuplicateHit(original_invoice_id="inv-1", kind="exact")
    ]


def test_fuzzy_duplicate_window(invoice: Invoice) -> None:
    original = fingerprint("inv-1", invoice)
    tol = DuplicateTolerances()
    near = invoice.model_copy(
        update={"number": "OTHER-1", "issue_date": invoice.issue_date + dt.timedelta(days=3)}
    )
    far = invoice.model_copy(
        update={"number": "OTHER-2", "issue_date": invoice.issue_date + dt.timedelta(days=4)}
    )
    assert [h.kind for h in find_duplicates(fingerprint("x", near), [original], tol)] == ["fuzzy"]
    assert find_duplicates(fingerprint("y", far), [original], tol) == []


def test_different_seller_is_never_a_duplicate(invoice: Invoice) -> None:
    other_seller = Party.model_validate({**invoice.seller.model_dump(), "vat_id": "DE123456789"})
    other = invoice.model_copy(update={"seller": other_seller})
    assert (
        find_duplicates(fingerprint("b", other), [fingerprint("a", invoice)], DuplicateTolerances())
        == []
    )


def test_document_is_not_its_own_duplicate(invoice: Invoice) -> None:
    fp = fingerprint("a", invoice)
    assert find_duplicates(fp, [fp], DuplicateTolerances()) == []


# --- routing --------------------------------------------------------------------


def test_auto_approve_only_without_any_reason(invoice: Invoice) -> None:
    decision = decide(
        invoice=invoice, is_e_invoice=True, validation_accepted=True, match_issues=[], duplicates=[]
    )
    assert decision.route is Route.AUTO_APPROVE
    assert decision.reasons == ()


def test_reasons_are_combined_and_sorted(invoice: Invoice) -> None:
    decision = decide(
        invoice=invoice.model_copy(update={"due_date": None}),
        is_e_invoice=True,
        validation_accepted=False,
        match_issues=[MatchIssue(reason=Reason.PO_PRICE_MISMATCH, detail="x")],
        duplicates=[DuplicateHit(original_invoice_id="a", kind="fuzzy")],
    )
    assert decision.route is Route.REVIEW
    assert decision.reasons == (
        Reason.DUPLICATE_SUSPECTED,
        Reason.MISSING_DUE_DATE,
        Reason.PO_PRICE_MISMATCH,
        Reason.VALIDATION_FAILED,
    )


def test_not_an_e_invoice_goes_to_review() -> None:
    decision = decide(
        invoice=None, is_e_invoice=False, validation_accepted=None, match_issues=[], duplicates=[]
    )
    assert decision.reasons == (Reason.NOT_AN_E_INVOICE,)


# --- tenant config --------------------------------------------------------------


def test_every_tenant_has_config() -> None:
    for tenant in ("nordwerk-de", "nordwerk-at"):
        assert config_for(TenantId(tenant)).matching.price_pct == Decimal("2")
    with pytest.raises(UnknownTenantError):
        config_for(TenantId("unknown-tenant"))


def test_invoice_line_model_accepts_bt132() -> None:
    line = InvoiceLine(
        line_id="1",
        quantity=Decimal("1"),
        unit_code="H87",
        net_price=Decimal("1.00"),
        net_amount=Decimal("1.00"),
        vat_category="S",
        order_line_ref="3",
    )
    assert line.order_line_ref == "3"


def test_fixture_invoice_round_trips(invoice: Invoice) -> None:
    assert make_invoice() == invoice
