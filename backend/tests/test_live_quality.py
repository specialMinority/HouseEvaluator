from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass
class _L:
    rent_yen: int | None = None
    admin_fee_yen: int | None = None
    monthly_total_yen: int | None = None
    area_sqm: float | None = None
    layout: str | None = None
    station_names: list[str] = field(default_factory=list)
    detail_url: str | None = None
    info_updated_at: str | None = None


def test_dedupe_listings_prefers_detail_url():
    from backend.src.live_quality import dedupe_listings

    a1 = _L(rent_yen=90000, admin_fee_yen=5000, monthly_total_yen=95000, area_sqm=25.0, layout="1DK", station_names=["x"], detail_url="https://x/detail/bk-1/")
    a2 = _L(rent_yen=91000, admin_fee_yen=5000, monthly_total_yen=96000, area_sqm=25.0, layout="1DK", station_names=["x"], detail_url="https://x/detail/bk-1/?foo=1")
    b = _L(rent_yen=92000, admin_fee_yen=5000, monthly_total_yen=97000, area_sqm=26.0, layout="1DK", station_names=["x"], detail_url="https://x/detail/bk-2/")

    out, stats = dedupe_listings([a1, a2, b], provider="chintai")
    assert len(out) == 2
    assert stats["removed"] == 1
    assert out[0].detail_url.startswith("https://x/detail/bk-1")


def test_dedupe_listings_falls_back_to_fingerprint():
    from backend.src.live_quality import dedupe_listings

    a1 = _L(rent_yen=90000, admin_fee_yen=5000, monthly_total_yen=95000, area_sqm=25.02, layout="1DK", station_names=["x"])
    a2 = _L(rent_yen=90000, admin_fee_yen=5000, monthly_total_yen=95000, area_sqm=25.03, layout="1dk", station_names=["x"])
    out, stats = dedupe_listings([a1, a2], provider="homes")
    assert len(out) == 1
    assert stats["removed"] == 1


def test_filter_stale_listings_removes_old_when_safe():
    from backend.src.live_quality import filter_stale_listings

    lst_new = _L(info_updated_at="2026-02-01")
    lst_old = _L(info_updated_at="2026-01-01")
    lst_missing = _L(info_updated_at=None)

    out, stats = filter_stale_listings([lst_new, lst_old, lst_missing], today=date(2026, 2, 10), max_age_days=14, min_keep=2)
    assert len(out) == 2
    assert stats["with_date"] == 2
    assert stats["missing_date"] == 1
    assert stats["skipped_due_to_min_keep"] is False


def test_filter_stale_listings_skips_when_it_would_drop_below_min_keep():
    from backend.src.live_quality import filter_stale_listings

    lst_new = _L(info_updated_at="2026-02-01")
    lst_old = _L(info_updated_at="2026-01-01")
    out, stats = filter_stale_listings([lst_new, lst_old], today=date(2026, 2, 10), max_age_days=14, min_keep=2)
    assert len(out) == 2
    assert stats["skipped_due_to_min_keep"] is True


def test_filter_outlier_listings_iqr_removes_extremes():
    from backend.src.live_quality import filter_outlier_listings_iqr

    normals = [95, 97, 99, 100, 101, 103, 105, 107]
    listings = [_L(monthly_total_yen=v) for v in ([10] + normals + [1000])]
    out, stats = filter_outlier_listings_iqr(listings, value_attr="monthly_total_yen", min_n=8, min_keep=2)
    assert stats["skipped"] is False
    assert stats["removed"] == 2
    assert len(out) == len(normals)

