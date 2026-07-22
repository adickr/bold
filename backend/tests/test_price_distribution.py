"""Price distribution histogram helpers."""

from app.api.queries import build_price_distribution


def test_price_distribution_buckets():
    prices = [399_000, 420_000, 450_000, 451_000, 539_900, 700_000, 749_999]
    buckets = build_price_distribution(prices, bucket_zar=50_000)
    by_start = {b["min_price"]: b["count"] for b in buckets}
    assert by_start[350_000] == 1  # 399k
    assert by_start[400_000] == 1  # 420k
    assert by_start[450_000] == 2  # 450k + 451k
    assert by_start[500_000] == 1  # 539900
    assert by_start[700_000] == 2  # 700k + 749999
    assert sum(b["count"] for b in buckets) == len(prices)
    assert all("label" in b for b in buckets)


def test_price_distribution_empty():
    assert build_price_distribution([]) == []
    assert build_price_distribution([0, -1]) == []
