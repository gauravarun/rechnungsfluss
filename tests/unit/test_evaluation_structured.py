import pytest

from rechnungsfluss.evaluation.structured import FieldStats, Report, _group, to_markdown


@pytest.mark.parametrize(
    ("path", "grouped"),
    [
        ("totals.amount_due", "totals.amount_due"),
        ("lines[3].quantity", "lines[].quantity"),
        ("vat_breakdown[0].rate", "vat_breakdown[].rate"),
        ("lines[12].description", "lines[].description"),
    ],
)
def test_group_collapses_indices(path: str, grouped: str) -> None:
    assert _group(path) == grouped


def test_markdown_states_scope_and_limits() -> None:
    report = Report(
        generated_at="2026-10-19T10:00:00+00:00",
        git_commit="abc1234",
        dataset_manifest_sha256="0" * 64,
        validator="KoSIT test",
        documents=2,
        by_format={"cii": 2},
        parse_ok=2,
        fields={"number": FieldStats(compared=2, correct=1)},
        validation_compared=2,
        validation_agree=2,
    )
    md = to_markdown(report)
    assert "50.00 % (1 / 2)" in md
    assert "**not** evaluated here" in md
    assert "not** evidence of accuracy on real supplier XML" in md


def test_empty_field_stats_have_no_accuracy() -> None:
    assert FieldStats().accuracy is None
