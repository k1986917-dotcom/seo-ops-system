from seo_ops.ingest.gsc import parse_gsc_workbook
from tests.helpers import workbook_bytes


def test_standard_gsc_workbook_keeps_fractional_ctr():
    result = parse_gsc_workbook(workbook_bytes())
    page = next(metric for metric in result.metrics if metric.dimension == "page")

    assert result.metadata["mode"] == "standard"
    assert page.ctr == 0.04
    assert page.period == "current"
    assert set(result.metadata["dimensions"]) == {"page", "query"}


def test_comparison_gsc_workbook_creates_two_periods():
    result = parse_gsc_workbook(workbook_bytes(comparison=True))
    example = [
        metric
        for metric in result.metrics
        if metric.dimension == "page" and metric.value.endswith("/blog/example")
    ]

    assert result.metadata["mode"] == "comparison"
    assert {metric.period for metric in example} == {"current", "previous"}
    assert next(metric for metric in example if metric.period == "previous").clicks == 10
