from backend.src.station_utils import normalize_station_name, station_similar


def test_normalize_station_drops_suffix_and_spaces():
    assert normalize_station_name("  なんば 駅 ") == "なんば"


def test_station_similar_kana_kanji_alias():
    assert station_similar("なんば", "難波") is True
    assert station_similar("難波", "なんば") is True


def test_station_similar_substring_variant():
    assert station_similar("難波", "大阪難波") is True
    assert station_similar("大阪難波", "難波") is True

