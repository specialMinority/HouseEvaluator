"""Conservative building-name handling shared by ingestion and comparison."""
import re
import unicodedata


def building_name(title):
    # A station followed only by walk/height/age facts is an advertisement
    # description. Keep its display title without inventing a building name.
    if not isinstance(title, str):
        return None
    description = re.fullmatch(r".+駅\s+(.+)", unicodedata.normalize('NFKC', title).strip())
    if description:
        facts = re.sub(r"\s+", "", description[1])
        if facts and re.fullmatch(r"(?:徒歩[0-9]+分)?(?:[0-9]+階建(?:\(地下[0-9]+階\))?)?(?:築[0-9]+年|新築)?", facts):
            return None
    return title or None
