"""Small stdlib HTML readers for the public SUUMO markup checked 2026-09-13."""

from html.parser import HTMLParser
import re
import unicodedata
from urllib.parse import urljoin

from .building_identity import building_name as _building_name
from .public_fetch import PublicFetchError, checked_url


class Node:
    def __init__(self, tag, attrs=()):
        self.tag, self.attrs, self.children = tag, dict(attrs), []

    def all(self, tag=None, cls=None):
        stack = [self]
        while stack:
            node = stack.pop()
            if not isinstance(node, Node):
                continue
            if (tag is None or node.tag == tag) and (cls is None or cls in node.attrs.get("class", "").split()):
                yield node
            stack.extend(reversed(node.children))

    def first(self, tag=None, cls=None):
        return next(self.all(tag, cls), None)

    def text(self):
        stack, parts = [self], []
        while stack:
            item = stack.pop()
            if isinstance(item, str):
                parts.append(item)
            else:
                stack.extend(reversed(item.children))
        return " ".join(" ".join(parts).split())


class Tree(HTMLParser):
    VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())

    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.root = Node("root")
        self.stack, self.ignored, self.count = [self.root], None, 0
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        if self.ignored:
            return
        if tag in ("script", "style", "template", "noscript"):
            self.ignored = tag
            return
        self.count += 1
        if self.count > 100000 or len(self.stack) > 256:
            raise PublicFetchError("html_too_complex")
        node = Node(tag, attrs)
        self.stack[-1].children.append(node)
        if tag not in self.VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if self.ignored:
            if tag == self.ignored:
                self.ignored = None
            return
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, text):
        if not self.ignored and text.strip():
            self.stack[-1].children.append(text)


def clean(value):
    return unicodedata.normalize("NFKC", value).strip() if isinstance(value, str) else ""


def node_text(node):
    return node.text() if node else ""


def station_name(value):
    value = clean(value)
    return value[:-1].strip() if value.endswith("駅") else value


def accesses(value):
    # Match each complete route, never a station substring (小岩 != 新小岩).
    result = []
    for match in re.finditer(r"([^\s/]+)\s*/\s*([^/\s]+)駅\s*(?:歩|徒歩)\s*(\d+)分", clean(value)):
        route = {"line": match[1], "station_name": station_name(match[2]), "walk_min": int(match[3])}
        if route["walk_min"] <= 1440 and route not in result:
            result.append(route)
    return result


def yen(value):
    value = clean(value).replace(",", "").replace(" ", "")
    if value in ("なし", "無し", "不要", "無料"):
        return 0
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(万)?円", value)
    if not match:
        return None
    number = float(match[1]) * (10000 if match[2] else 1)
    return int(round(number)) if 0 <= number <= 1000000000 else None


def layout(value):
    value = clean(value).replace(" ", "").upper()
    if value == "ワンルーム":
        return "1R"
    return value if re.fullmatch(r"[1-9](?:R|K|DK|LDK|SK|SDK|SLDK)", value) else None


def area(value):
    match = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:m\s*2|㎡|平米)", clean(value))
    if not match:
        return None
    number = float(match[1])
    return number if 0 < number <= 10000 else None


def floor(value):
    text = re.sub(r"\s+", "", clean(value))
    if text.count("/") > 1:
        return None
    match = re.fullmatch(r"(地下|B)?(\d+)階", text.split("/", 1)[0])
    if not match:
        return None
    number = int(match[2]) * (-1 if match[1] else 1)
    return number if number != 0 and -20 <= number <= 200 else None


def building_type(value):
    """Use the site's explicit building label, never material or height."""
    return {"賃貸マンション": "mansion", "マンション": "mansion",
            "賃貸アパート": "apartment", "アパート": "apartment"}.get(clean(value))


def building_floors(value):
    """Above-ground total, distinct from the occupied floor and basement count."""
    text = re.sub(r"\s+", "", clean(value))
    text = re.sub(r"^(?:築[0-9]{1,3}年|新築)", "", text)
    if "/" in text:
        if text.count("/") != 1:
            return None
        unit, text = text.split("/", 1)
        if floor(unit) is None:
            return None
    # Actual list markup includes e.g. 地下1地上13階建. Basement floors
    # are validated separately and never added to the above-ground total.
    match = re.fullmatch(r"(?:(?:地下([0-9]{1,2})(?:階)?地上)|地上)?([0-9]{1,3})階建", text)
    if match:
        basement, above = match[1], match[2]
    else:
        match = re.fullmatch(r"(?:地上)?([0-9]{1,3})階建\(地下([0-9]{1,2})階\)", text)
        if not match:
            return None
        above, basement = match[1], match[2]
    if basement is not None and not 1 <= int(basement) <= 99:
        return None
    return int(above) if 1 <= int(above) <= 100 else None


def elevator(amenities, field_value=None):
    """Absence from a feature list means unknown; contradictory text does too."""
    observations = set()
    for token in re.split(r"[、,;；\n]+", clean(amenities)):
        token = re.sub(r"\s+", "", token)
        if re.fullmatch(r"エレベーター?(?:あり|有り|有|付き|付)?", token) or re.fullmatch(r"エレベーター?[1-9][0-9]?基(?:以上)?", token):
            observations.add(True)
        elif re.fullmatch(r"エレベーター?(?:なし|無し|無)", token):
            observations.add(False)
    values = field_value if isinstance(field_value, list) else [field_value]
    for value in values:
        explicit = re.sub(r"\s+", "", clean(value))
        if explicit in ("あり", "有り", "有", "付き", "設置済") or re.fullmatch(r"[1-9][0-9]?基", explicit):
            observations.add(True)
        elif explicit in ("なし", "無し", "無", "未設置"):
            observations.add(False)
    return next(iter(observations)) if len(observations) == 1 else None


def structure(value):
    value = clean(value).replace(" ", "").upper()
    return {"鉄筋コン": "rc", "鉄筋コンクリート": "rc", "RC": "rc", "SRC": "src",
            "鉄筋コン造": "rc", "鉄筋コンクリート造": "rc", "鉄骨鉄筋コン": "src",
            "鉄骨鉄筋": "src", "鉄骨鉄筋コンクリート": "src", "鉄骨鉄筋コンクリート造": "src", "鉄骨": "steel", "鉄骨造": "steel",
            "軽量鉄骨": "light_steel", "軽量鉄骨造": "light_steel", "木造": "wood"}.get(value)


def orientation(value):
    return {"北": "N", "北東": "NE", "東": "E", "南東": "SE", "南": "S", "南西": "SW", "西": "W", "北西": "NW"}.get(clean(value))


def address_region(address, regions):
    value = clean(address).replace(" ", "")
    for city in regions:
        for municipality in city["municipalities"]:
            prefix = city["prefecture"] + municipality["name"]
            if value.startswith(prefix):
                return city["id"], municipality["name"]
    return None, None


def select_access(item, target):
    target = station_name(target)
    matching = [route for route in item["accesses"] if route["station_name"] == target]
    routes = matching or item["accesses"]
    route = min(routes, key=lambda r: r["walk_min"]) if routes else {}
    item["station_name"] = route.get("station_name")
    item["walk_min"] = route.get("walk_min")


def missing(item):
    item["missing_fields"] = [key for key in ("city", "municipality", "station_name", "layout", "area_sqm", "rent_yen",
        "mgmt_fee_yen", "structure", "built_year", "walk_min", "floor", "orientation", "bathroom_separate", "furnished", "contract_type",
        "property_type", "building_type", "building_floors", "elevator")
        if item.get(key) is None]
    return item


def parse_search(text, *, fetched_at, regions, target_station):
    tree = Tree(text).root
    cards = list(tree.all(cls="cassetteitem"))
    listings, seen, conflicts = [], {}, set()
    for card in cards[:50]:
        accepted_in_card = 0
        name = node_text(card.first(cls="cassetteitem_content-title"))[:200]
        address = node_text(card.first(cls="cassetteitem_detail-col1"))[:300]
        city, municipality = address_region(address, regions)
        routes = accesses(node_text(card.first(cls="cassetteitem_detail-col2")))
        building_label = clean(node_text(card.first(cls="cassetteitem_content-label")))
        age_label = clean(node_text(card.first(cls="cassetteitem_detail-col3")))
        age_match = re.search(r"(?:^|\s)築\s*(\d{1,3})年(?:\s|$)", age_label)
        advertised_age = int(age_match[1]) if age_match and 0 <= int(age_match[1]) <= 300 else None
        for row in card.all("tr", "js-cassette_link"):
            link = row.first("a", "js-cassette_link_href")
            if link is None:
                continue
            url = urljoin("https://suumo.jp", link.attrs.get("href", ""))
            try:
                parts = checked_url(url)
                if not re.fullmatch(r"/chintai/(?:bc|jnc)_[0-9]{8,16}/", parts.path):
                    continue
            except PublicFetchError:
                continue
            if url in conflicts:
                continue
            cells = [node for node in row.children if isinstance(node, Node) and node.tag == "td"]
            source_input = next((node for node in row.all("input") if node.attrs.get("name") == "bc"), None)
            source_listing_id = source_input.attrs.get("value") if source_input else None
            if not isinstance(source_listing_id, str) or not re.fullmatch(r"[0-9]{8,16}", source_listing_id):
                source_listing_id = None
            item = {
                "source_id": "suumo", "source_url": url, "title": name, "building_name": _building_name(name),
                "source_listing_id": source_listing_id,
                "address": address or None, "city": city, "municipality": municipality,
                "rent_yen": yen(node_text(row.first(cls="cassetteitem_price--rent"))),
                "mgmt_fee_yen": yen(node_text(row.first(cls="cassetteitem_price--administration"))),
                "layout": layout(node_text(row.first(cls="cassetteitem_madori"))),
                "area_sqm": area(node_text(row.first(cls="cassetteitem_menseki"))),
                "floor": floor(node_text(cells[2])) if len(cells) >= 3 else None,
                "structure": None, "built_year": None, "orientation": None, "contract_type": None,
                "building_age_years": advertised_age,
                "bathroom_separate": None, "furnished": None,
                "property_type": "apartment" if building_label in ("賃貸マンション", "賃貸アパート") else "house" if building_label == "賃貸一戸建て" else None,
                "building_type": building_type(building_label), "building_floors": building_floors(age_label), "elevator": None,
                "accesses": [dict(route) for route in routes], "fetched_at": fetched_at,
                "listing_updated_date": None, "details_fetched_at": None,
            }
            select_access(item, target_station)
            if url in seen:
                if seen[url] != item:
                    conflicts.add(url)
                    listings = [previous for previous in listings if previous["source_url"] != url]
                continue
            # Store before missing() mutates the output dictionary.
            seen[url] = dict(item)
            # A malformed price/area row must not become a plausible candidate.
            if (item["rent_yen"] is not None and item["rent_yen"] > 0 and item["area_sqm"] is not None
                    and item["layout"] is not None and accepted_in_card < 8):
                listings.append(missing(item))
                accepted_in_card += 1
            # Keep scanning for conflicts, but reserve output space for every
            # building card. One large new development cannot consume the
            # entire page budget before later buildings are even inspected.
            # At most 50 cards * 8 accepted advertisements = 400 output rows.
    empty = bool(re.search(r"(?:該当する物件はありません|該当する物件がありません|検索結果\s*0\s*件|0件の物件)", tree.text()))
    return listings, len(cards), empty


def enrich_detail(item, text, *, fetched_at, regions, target_station):
    tree = Tree(text).root
    content = next((node for node in tree.all() if node.attrs.get("id") == "contents"), None)
    heading = tree.first(cls="section_h1")
    if content is None or heading is None:
        return None
    fields = {}
    tables = list(heading.all("table", "property_view_table")) + list(content.all("table", "table_gaiyou"))
    for table in tables:
        for row in table.all("tr"):
            cells = [node for node in row.children if isinstance(node, Node) and node.tag in ("th", "td")]
            for index in range(len(cells) - 1):
                if cells[index].tag == "th" and cells[index + 1].tag == "td":
                    key = clean(cells[index].text()).replace(" ", "")
                    value = cells[index + 1].text()
                    if key in fields and clean(fields[key]) != clean(value):
                        return None
                    fields[key] = value
    if not {"所在地", "間取り", "専有面積"}.issubset(fields):
        return None
    advertised_id = clean(fields.get("SUUMO物件コード", ""))
    if item.get("source_listing_id") and advertised_id != item["source_listing_id"]:
        return None
    updated = dict(item)
    updated["address"] = fields["所在地"][:300]
    updated["city"], updated["municipality"] = address_region(updated["address"], regions)
    updated["layout"], updated["area_sqm"] = layout(fields["間取り"]), area(fields["専有面積"])
    # Do not mix a substituted room/detail page into an earlier search row.
    if (updated["layout"] != item["layout"] or updated["area_sqm"] != item["area_sqm"]
            or updated["city"] != item["city"] or updated["municipality"] != item["municipality"]
            or clean(updated["address"]).replace(" ", "") != clean(item["address"]).replace(" ", "")):
        return None
    title = node_text(heading.first("h1"))
    search_name = clean(item.get("building_name"))
    if title and search_name and "駅" not in search_name and clean(title) != search_name:
        return None
    if title:
        updated["title"] = title[:200]
    price_box = heading.first(cls="property_view_note") or heading.first(cls="property_view_main")
    price_node = price_box.first(cls="property_view_note-emphasis") if price_box else None
    if price_node is None and price_box:
        price_node = price_box.first(cls="property_view_main-emphasis")
    detail_rent = yen(node_text(price_node))
    fee_match = re.search(r"管理費(?:・共益費)?\s*[:：]\s*([^\s]+)", clean(node_text(price_box)))
    if detail_rent is None or detail_rent <= 0 or not fee_match:
        return None
    detail_fee = yen(fee_match[1])
    if detail_rent != item["rent_yen"] or detail_fee != item["mgmt_fee_yen"]:
        return None
    updated["structure"] = structure(fields.get("構造", fields.get("建物構造", "")))
    match = re.fullmatch(r"(\d{4})年(\d{1,2})月", clean(fields.get("築年月", "")))
    updated["built_year"] = int(match[1]) if match and 1800 <= int(match[1]) <= int(fetched_at[:4]) and 1 <= int(match[2]) <= 12 else None
    updated["orientation"] = orientation(fields.get("向き", ""))
    explicit_types = {building_type(fields[key]) for key in ("建物種別", "建物種類") if key in fields}
    known_types = explicit_types - {None}
    if len(known_types) > 1:
        return None
    detail_type = next(iter(known_types)) if known_types else None
    detail_total = building_floors(fields.get("階建", ""))
    for key, value in (("building_type", detail_type), ("building_floors", detail_total)):
        updated.setdefault(key, None)
        if value is not None:
            if item.get(key) is not None and item[key] != value:
                return None
            updated[key] = value
    detail_floor = floor(fields.get("階建", fields.get("階", "")))
    separate_floor = floor(fields.get("階", ""))
    if separate_floor is not None:
        if detail_floor is not None and detail_floor != separate_floor:
            return None
        detail_floor = separate_floor
    if detail_floor is not None:
        if item["floor"] is not None and detail_floor != item["floor"]:
            return None
        updated["floor"] = detail_floor
    if (updated.get("floor") is not None and updated.get("building_floors") is not None
            and updated["floor"] > updated["building_floors"]):
        return None
    routes = accesses(fields.get("駅徒歩", fields.get("交通", "")))
    if routes:
        updated["accesses"] = routes
        select_access(updated, target_station)
    contract = clean(fields.get("契約期間", ""))
    updated["contract_type"] = "standard" if contract.startswith("普通借家") else "fixed_term" if contract.startswith("定期借家") else None
    # Presence is meaningful; absence from a marketing amenity list is unknown.
    amenity_box = next((node for node in content.all() if node.attrs.get("id") == "bkdt-option"), None)
    amenities = node_text(amenity_box)
    # Preserve list-item boundaries: a separate エレベーター <li> is an
    # explicit feature even when the other <li> elements use no commas.
    feature_items = list(amenity_box.all("li")) if amenity_box else []
    feature_text = "、".join(node.text() for node in feature_items) if feature_items else amenities
    detail_elevator = elevator(feature_text, [fields[key] for key in ("エレベーター", "エレベータ") if key in fields])
    if item.get("elevator") is not None and detail_elevator is not None and item["elevator"] is not detail_elevator:
        return None
    updated["elevator"] = detail_elevator
    updated["bathroom_separate"] = True if "バストイレ別" in amenities else None
    updated["furnished"] = True if "家具付" in amenities else None
    date = clean(fields.get("情報更新日", ""))
    updated["listing_updated_date"] = date if re.fullmatch(r"\d{4}/\d{2}/\d{2}", date) else None
    # Date-only portal text is never promoted to a verified UTC status time.
    updated["details_fetched_at"] = fetched_at
    return missing(updated)
