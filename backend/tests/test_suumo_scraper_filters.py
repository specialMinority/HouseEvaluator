"""
Tests for suumo_scraper layout parsing — specifically the \b boundary bug fix.

SUUMO raw blocks tend to have layout text directly adjacent to Japanese chars:
  "万円1DK25.21m2" — no word boundary between 円 and 1DK
  "1DK25.21m2"    — no word boundary between K and 2
The previous _LAYOUT_PATTERN used \b which fails for these cases.
"""
import pytest
from backend.src.suumo_scraper import _extract_listings_from_block


def test_layout_adjacent_to_kana_and_numbers():
    """Japanese chars directly adjacent to layout — original \b bug case."""
    block = "3階 8.7万円5,000円1DK25.21m2 南西 築5年 歩12分"
    listings = _extract_listings_from_block(block)
    assert len(listings) >= 1, "Should parse at least one listing"
    assert listings[0].layout == "1DK", f"Expected '1DK', got {listings[0].layout!r}"


def test_layout_1ldk_adjacent():
    """1LDK parsing without spaces."""
    block = "5階 13.5万円8,000円1LDK40.00m2 南 築3年 歩7分"
    listings = _extract_listings_from_block(block)
    assert len(listings) >= 1
    assert listings[0].layout == "1LDK", f"Expected '1LDK', got {listings[0].layout!r}"


def test_layout_1k_adjacent():
    """1K parsing: 'K' followed by digits should not prevent match."""
    block = "2階 6.5万円0円1K20.00m2 北 築10年 歩5分"
    listings = _extract_listings_from_block(block)
    assert len(listings) >= 1
    assert listings[0].layout == "1K", f"Expected '1K', got {listings[0].layout!r}"


def test_layout_2ldk():
    """2LDK parsing."""
    block = "8階 18.0万円15,000円2LDK55.50m2 東 築2年 歩3分"
    listings = _extract_listings_from_block(block)
    assert len(listings) >= 1
    assert listings[0].layout == "2LDK"


def test_no_false_positive_from_rc():
    """'RC' should NOT be parsed as a layout."""
    block = "3階 10.0万円5,000円1LDK30.00m2 RC造 南 築5年 歩8分"
    listings = _extract_listings_from_block(block)
    assert len(listings) >= 1
    # RC should not be captured as layout
    assert listings[0].layout == "1LDK"


def test_no_false_positive_from_area():
    """'25.21' digits in area should not be part of layout."""
    block = "2階 9.5万円0円1DK25.21m2 築7年 歩10分"
    listings = _extract_listings_from_block(block)
    assert len(listings) >= 1
    assert listings[0].layout == "1DK"
