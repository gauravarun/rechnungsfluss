import json
import random
from collections import Counter
from decimal import Decimal
from pathlib import Path

import pytest
from stdnum import iban as std_iban
from stdnum.at import uid as at_uid
from stdnum.de import vat as de_vat

from rechnungsfluss.domain.invoice import Invoice
from rechnungsfluss.synthetic import defects
from rechnungsfluss.synthetic.generate import Category, generate
from rechnungsfluss.synthetic.identifiers import fake_iban, fake_vat_id
from tests.conftest import make_invoice

REPO = Path(__file__).resolve().parents[2]
COMMITTED = REPO / "eval" / "dataset"


@pytest.fixture(scope="module")
def dataset(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, list[dict[str, object]]]:
    out = tmp_path_factory.mktemp("dataset")
    generate(out)
    labels = [json.loads(line) for line in (out / "manifest.jsonl").read_text("utf-8").splitlines()]
    return out, labels


@pytest.mark.parametrize("seed", range(200))
def test_fake_vat_ids_fail_the_official_checksum(seed: int) -> None:
    rng = random.Random(seed)
    assert not de_vat.is_valid(fake_vat_id("DE", rng))
    assert not at_uid.is_valid(fake_vat_id("AT", rng))


@pytest.mark.parametrize("seed", range(50))
def test_fake_ibans_have_valid_checksums_and_zero_bank_code(seed: int) -> None:
    rng = random.Random(seed)
    de, at = fake_iban("DE", rng), fake_iban("AT", rng)
    assert std_iban.is_valid(de)
    assert de[4:12] == "00000000"
    assert std_iban.is_valid(at)
    assert at[4:9] == "00000"


def test_vat_defect_refuses_deltas_inside_the_official_tolerance() -> None:
    with pytest.raises(ValueError, match="tolerance"):
        defects.wrong_vat_amount(make_invoice(), Decimal("1.00"))


def test_size_and_category_mix(dataset: tuple[Path, list[dict[str, object]]]) -> None:
    _, labels = dataset
    assert len(labels) >= 100
    per_tenant = Counter((lbl["tenant"], lbl["category"]) for lbl in labels)
    for tenant in ("nordwerk-de", "nordwerk-at"):
        for category in Category:
            assert per_tenant[(tenant, category.value)] > 0, (tenant, category)
    formats = Counter(lbl["format"] for lbl in labels)
    assert set(formats) == {"cii", "ubl", "zugferd", "pdf"}


def test_every_file_exists_and_truth_is_a_valid_invoice(
    dataset: tuple[Path, list[dict[str, object]]],
) -> None:
    out, labels = dataset
    for lbl in labels:
        assert (out / str(lbl["file"])).is_file()
        if lbl["truth"] is not None:
            Invoice.model_validate(lbl["truth"])
        else:
            assert lbl["variant"] == "zugferd_minimum_profile"


def test_duplicates_point_to_clean_originals_of_the_same_tenant(
    dataset: tuple[Path, list[dict[str, object]]],
) -> None:
    _, labels = dataset
    by_id = {lbl["doc_id"]: lbl for lbl in labels}
    dups = [lbl for lbl in labels if lbl["category"] == "duplicate"]
    assert {d["variant"] for d in dups} == {"exact_resend", "other_format", "fuzzy"}
    for dup in dups:
        original = by_id[dup["duplicate_of"]]
        assert original["tenant"] == dup["tenant"]
        assert original["category"] == "clean"
        if dup["variant"] == "exact_resend":
            assert dup["sha256"] == original["sha256"]
            assert dup["expected_route"] == "skip_duplicate_delivery"
        else:
            assert dup["expected_route"] == "review"


def test_routes_are_consistent_with_reasons(dataset: tuple[Path, list[dict[str, object]]]) -> None:
    _, labels = dataset
    for lbl in labels:
        if lbl["expected_route"] == "auto_approve":
            assert lbl["review_reasons"] == []
            assert lbl["expected_validation"] == "accept"
        if lbl["expected_validation"] == "reject":
            assert lbl["expected_rule_ids"]
            assert lbl["expected_route"] == "review"


def _without_scan_hashes(manifest: Path) -> list[dict[str, object]]:
    """Scan rasterization (FreeType, blur, JPEG) is not guaranteed byte-identical across
    platforms, so scan hashes are excluded here and checked against the committed files."""
    labels = [json.loads(line) for line in manifest.read_text("utf-8").splitlines()]
    return [{**lbl, "sha256": None} if lbl["layout"] == "scan" else lbl for lbl in labels]


def test_committed_manifest_matches_the_generator(
    dataset: tuple[Path, list[dict[str, object]]],
) -> None:
    """Drift guard: regenerate with `python -m rechnungsfluss.synthetic.generate`."""
    out, _ = dataset
    assert (out / "purchase_orders.json").read_bytes() == (
        COMMITTED / "purchase_orders.json"
    ).read_bytes()
    assert _without_scan_hashes(out / "manifest.jsonl") == _without_scan_hashes(
        COMMITTED / "manifest.jsonl"
    )


def test_committed_files_match_their_labels() -> None:
    """The committed documents are the canonical eval inputs (cassettes are keyed by them)."""
    import hashlib

    from rechnungsfluss.formats import zugferd

    labels = [
        json.loads(line) for line in (COMMITTED / "manifest.jsonl").read_text("utf-8").splitlines()
    ]
    for lbl in labels:
        data = (COMMITTED / str(lbl["file"])).read_bytes()
        if lbl["format"] == "zugferd":
            data = zugferd.attachment(data)[1]
        assert hashlib.sha256(data).hexdigest() == lbl["sha256"], lbl["doc_id"]
