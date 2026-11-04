"""서버 데이터에서 아이템 이름(한/영)과 '어디서 얻나'에 답할 사실을 모은다.

- 이름: 바닐라(data/vanilla, Mojang 에셋에서 받은 ko_kr + 서버 jar의 en_us), kubejs/assets의 ko_kr, 모드 jar의 en_us,
  그리고 아이템 ID에서 만든 영어 별칭('society:ancient_fruit' -> 'ancient fruit').
- 획득처: society_trading 상점(누가 얼마에 파는지), 농업연감 가축의 생산품(드롭, 착유, 산란 등).
- 제철: Serene Seasons 태그(모드 jar 기본값 - KubeJS handleSeasonTags.js 제거 목록 + 추가 목록).
"""
import json
import re
import zipfile

from . import config

SEASONS_KO = {"spring": "봄", "summer": "여름", "autumn": "가을", "winter": "겨울"}
NAME_KEY_RE = re.compile(r"(item|block|entity|effect)\.([a-z0-9_.-]+?)\.([a-z0-9_/.-]+)$")


def _jar_files(pattern):
    """모드 jar와 서버 jar 안에서 정규식에 맞는 파일 내용을 (jar 이름, 경로, bytes)로."""
    jars = sorted((config.SERVER_DIR / "mods").glob("*.jar"))
    jars += sorted((config.SERVER_DIR / "libraries" / "net" / "minecraft" / "server").rglob("*-extra.jar"))
    for jar in jars:
        try:
            with zipfile.ZipFile(jar) as z:
                for entry in z.namelist():
                    if pattern.match(entry):
                        yield jar.name, entry, z.read(entry)
        except (zipfile.BadZipFile, OSError):
            pass


def _load_json(data):
    try:
        return json.loads(data if isinstance(data, str) else data.decode("utf-8", "replace"))
    except ValueError:
        return None


_names = None
_langs = None


def langs():
    """모든 lang 파일을 합친 (ko, en): lang 키 -> 문자열. 바닐라 + 모드 jar의 en_us·ko_kr + kubejs/assets의 ko_kr·en_us."""
    global _langs
    if _langs is not None:
        return _langs
    ko, en = {}, {}
    vanilla = config.DATA_DIR / "vanilla"
    for path, target in ((vanilla / "ko_kr.json", ko), (vanilla / "en_us.json", en)):
        if path.exists():
            target.update(_load_json(path.read_text(encoding="utf-8")) or {})
    for _, _, data in _jar_files(re.compile(r"assets/[^/]+/lang/en_us\.json$")):
        en.update(_load_json(data) or {})
    # 모드 jar의 ko_kr도 읽는다(플레이어 화면에 보이는 이름). 빠뜨리면 '고도계'가 'Altimeter'로만 남았다(11/04).
    # kubejs/assets의 ko_kr(팩 번역)이 뒤에서 덮어쓴다.
    for _, _, data in _jar_files(re.compile(r"assets/[^/]+/lang/ko_kr\.json$")):
        ko.update(_load_json(data) or {})
    assets = config.SERVER_DIR / "kubejs" / "assets"
    for path in assets.glob("*/lang/en_us.json"):
        en.update(_load_json(path.read_text(encoding="utf-8")) or {})
    for path in assets.glob("*/lang/ko_kr.json"):
        ko.update(_load_json(path.read_text(encoding="utf-8")) or {})
    _langs = (ko, en)
    return _langs


def translations():
    """영어 문자열(소문자) -> 한국어 문자열. 같은 lang 키에 둘 다 있는 것 (NPC 이름 'Caroline' -> '캐롤라인' 등).
    위키 문서 제목 같은 영어 이름을 한국어 이름과 잇는 데 쓴다."""
    ko, en = langs()
    out = {}
    for key, en_text in en.items():
        ko_text = ko.get(key)
        if isinstance(en_text, str) and isinstance(ko_text, str) and en_text.strip() and ko_text.strip():
            out.setdefault(en_text.strip().lower(), ko_text.strip())
    for item_id, entry in names().items():
        if entry.get("ko"):
            # en_us에 영어 이름이 없는 KubeJS 아이템은 ID 별칭('fish pond')으로 잇는다.
            for en_name in (entry.get("en"), id_alias(item_id)):
                if en_name:
                    out.setdefault(en_name.strip().lower(), entry["ko"])
    return out


NAME_KIND_ORDER = {"item": 0, "block": 1, "entity": 2, "effect": 3}
FORMAT_ARG_RE = re.compile(r"%(\d+\$)?[sd]")


def names():
    """아이템/블록/몹/효과 ID -> {"ko", "en", "kind"}. 'item.society.geode' 같은 lang 키에서 만든다."""
    global _names
    if _names is not None:
        return _names
    ko, en = langs()
    out = {}
    # 같은 ID의 item·block·entity 키가 함께 있으면 item 이름을 먼저 쓴다. 집합 순서대로 돌면 실행할 때마다
    # '달걀'이 '던져진 달걀'(entity 키)이 되기도 했다(11/04).
    keys = sorted(set(ko) | set(en), key=lambda k: (NAME_KIND_ORDER.get(k.split(".")[0], 9), k))
    for key in keys:
        m = NAME_KEY_RE.match(key)
        if not m or "." in m.group(3):
            continue
        item_id = f"{m.group(2)}:{m.group(3)}"
        entry = out.setdefault(item_id, {"kind": m.group(1)})
        for lang, field in ((ko, "ko"), (en, "en")):
            # '%s%s구리 상자'처럼 앞에 붙는 말 자리(%s)는 뺀다.
            text = FORMAT_ARG_RE.sub("", lang[key]).strip() if isinstance(lang.get(key), str) else ""
            if text and field not in entry:
                entry[field] = text
            elif text and m.group(1) == "entity" and entry["kind"] != "entity" and text != entry.get(field):
                # 아이템과 같은 ID의 몹('minecraft:rabbit' = 익히지 않은 토끼고기 / 토끼)은 몹 이름을 따로 둔다.
                entry.setdefault("mob", {})[field] = text
    _compose_ko(out)
    _names = out
    return out


# 한국어 번역이 없는 모드 아이템 중 '재료_바닐라아이템' 꼴의 ID('aquaculture:iron_fishing_rod')는 한국어 이름을 지어 붙인다
# ('철 낚싯대'). 플레이어가 한국어로 물어도 찾을 수 있고, '낚싯대' 계열로 묶인다.
MATERIAL_KO = {"iron": "철", "gold": "금", "golden": "금", "diamond": "다이아몬드", "netherite": "네더라이트",
               "copper": "구리", "stone": "돌", "wooden": "나무", "wood": "나무", "leather": "가죽",
               "neptunium": "넵튜늄", "chainmail": "사슬"}


def _compose_ko(out):
    vanilla_ko = {i.split(":")[1]: e["ko"] for i, e in out.items() if i.startswith("minecraft:") and e.get("ko")}
    # 여러 아이템이 같은 한국어 이름을 쓰는데 ID가 재료로 시작하면 재료를 앞에 붙여 구분한다
    # ('society:iron_upgrade_smithing_template' 등이 모두 '대장장이 형판' -> '철 대장장이 형판').
    shared = {}
    for item_id, entry in out.items():
        if entry.get("ko"):
            shared.setdefault(entry["ko"], []).append(item_id)
    for ko, ids in shared.items():
        if len(ids) > 1:
            for item_id in ids:
                material = item_id.split(":")[-1].split("_")[0]
                if material in MATERIAL_KO and not ko.startswith(MATERIAL_KO[material]):
                    out[item_id]["ko"] = f"{MATERIAL_KO[material]} {ko}"
    for item_id, entry in out.items():
        if entry.get("ko") or item_id.startswith("minecraft:"):
            continue
        material, _, base = item_id.split(":")[-1].partition("_")
        if material in MATERIAL_KO and base in vanilla_ko:
            entry["ko"] = f"{MATERIAL_KO[material]} {vanilla_ko[base]}"


def display_name(item_id):
    entry = names().get(item_id, {})
    return entry.get("ko") or entry.get("en") or item_id.split(":")[-1].replace("_", " ")


def id_alias(item_id):
    """'society:ancient_fruit' -> 'ancient fruit' (en_us에 없는 KubeJS 아이템도 영어로 찾게)."""
    return item_id.split(":")[-1].replace("_", " ")


def _js_array(src, name):
    m = re.search(rf"const {name} = \[(.*?)\];", src, re.S)
    return re.findall(r'"([^"]+)"', m.group(1)) if m else []


def crop_seasons():
    """아이템 ID -> 제철 목록(한국어). 실제 게임과 같도록 KubeJS가 바꾼 태그까지 반영한다."""
    tags = {s: set() for s in SEASONS_KO}
    pattern = re.compile(r"data/sereneseasons/tags/items/(spring|summer|autumn|winter|year_round)_crops\.json$")
    raw = {}
    for _, entry, data in _jar_files(pattern):
        season = pattern.match(entry).group(1)
        raw.setdefault(season, []).extend((_load_json(data) or {}).get("values", []))
    year_round = {v for v in raw.get("year_round", []) if isinstance(v, str) and not v.startswith("#")}
    for season in SEASONS_KO:
        for v in raw.get(season, []):
            if v == "#sereneseasons:year_round_crops":
                tags[season] |= year_round
            elif isinstance(v, str) and not v.startswith("#"):
                tags[season].add(v)

    script = config.SERVER_DIR / "kubejs" / "server_scripts" / "tags" / "handleSeasonTags.js"
    if script.exists():
        src = script.read_text(encoding="utf-8")
        for item in _js_array(src, "stripTags"):
            for season in tags.values():
                season.discard(item)
        for season in SEASONS_KO:
            tags[season] |= set(_js_array(src, f"{season}Crops"))
        for season in tags.values():
            season |= set(_js_array(src, "allSeasonCrops"))

    out = {}
    for season, items in tags.items():
        for item in items:
            out.setdefault(item, []).append(SEASONS_KO[season])
    return out


def slime_sources():
    """아이템 ID -> ['부패 슬라임의 플로트를 플로트 리핏으로 분해 (3개). 슬라임: ...', ...]
    이 모드팩은 지상에 적대 몹이 나오지 않아서 뼈·썩은 살점·화약 같은 몹 드롭을 슬라임 플로트로 얻는다."""
    base = config.SERVER_DIR / "kubejs" / "data" / "splendid_slimes"
    lang_path = config.SERVER_DIR / "kubejs" / "assets" / "splendid_slimes" / "lang" / "ko_kr.json"
    lang = (_load_json(lang_path.read_text(encoding="utf-8")) or {}) if lang_path.exists() else {}
    ripper = display_name("splendid_slimes:plort_rippit")
    out = {}
    for recipe in sorted((base / "recipes" / "plort_ripping").glob("*.json")):
        data = _load_json(recipe.read_text(encoding="utf-8")) or {}
        slime = ((data.get("ingredient") or {}).get("nbt") or {}).get("plort", {}).get("id", "").split(":")[-1]
        if not slime:
            continue
        slime_name = lang.get(f"slime.splendid_slimes.{slime}", slime)
        info = lang.get(f"slime.splendid_slimes.{slime}.info", "")
        diet = lang.get(f"diet.splendid_slimes.{slime}", "")
        detail = " ".join(x for x in (info, f"먹이: {diet}." if diet else "") if x)
        for result in data.get("results", []):
            item = result.get("item")
            if not item:
                continue
            line = f"'{slime_name}' 슬라임의 플로트를 {ripper}으로 분해하면 나옴 ({result.get('count', 1)}개)"
            out.setdefault(item, []).append(line + (f". 이 슬라임은 {detail}" if detail else ""))
    return out


def _season_text(seasons_required):
    """['early_spring', 'mid_spring', ...] -> '봄, 여름'"""
    found = []
    for s in seasons_required or []:
        ko = SEASONS_KO.get(s.split("_")[-1])
        if ko and ko not in found:
            found.append(ko)
    return ", ".join(found) if len(found) < 4 else ""


def shop_sources():
    """아이템 ID -> ['마트에서 코인 4에 살 수 있음, 봄, 여름, 가을에만', ...]"""
    lang = {}
    path = config.SERVER_DIR / "kubejs" / "assets" / "society_trading" / "lang" / "ko_kr.json"
    if path.exists():
        lang = _load_json(path.read_text(encoding="utf-8")) or {}
    out = {}
    for shop in sorted((config.SERVER_DIR / "kubejs" / "data" / "society_trading" / "shops").glob("*.json")):
        data = _load_json(shop.read_text(encoding="utf-8")) or {}
        # 가구 카탈로그는 수백 개라 검색만 어지럽힌다.
        if shop.stem.endswith("catalog"):
            continue
        shop_name = lang.get(data.get("name", ""), shop.stem)
        for trade in data.get("trades", []):
            offer, request = trade.get("offer") or {}, trade.get("request") or {}
            item = offer.get("item")
            if not item:
                continue
            if "numismatics_cost" in trade:
                price = f"코인 {trade['numismatics_cost']:,}"
            elif request.get("item"):
                price = f"{display_name(request['item'])} {request.get('count', 1)}개"
            else:
                continue
            count = offer.get("count", 1)
            # '판매'라고 쓰면 모델이 플레이어가 파는 것으로 읽는다(11/04 대나무). 플레이어가 사는 값임을 분명히 쓴다.
            line = f"{shop_name}에서 {price}에 살 수 있음" + (f" ({count}개 묶음)" if count > 1 else "")
            season = _season_text(trade.get("seasons_required"))
            if season:
                line += f", {season}에만"
            if trade.get("stage_required"):
                line += ", 진행 조건 있음"
            out.setdefault(item, [])
            if line not in out[item]:
                out[item].append(line)
    return out


# 레시피 종류 -> 쓰는 기계·작업대 블록 ID (이름은 lang에서 가져온다). 여기 없으면 종류 ID가 블록 ID인지 보고, 아니면 ID를 그대로 쓴다.
RECIPE_STATIONS = {
    "minecraft:crafting_shaped": "minecraft:crafting_table",
    "minecraft:crafting_shapeless": "minecraft:crafting_table",
    "minecraft:smelting": "minecraft:furnace",
    "minecraft:blasting": "minecraft:blast_furnace",
    "minecraft:smoking": "minecraft:smoker",
    "minecraft:campfire_cooking": "minecraft:campfire",
    "minecraft:stonecutting": "minecraft:stonecutter",
    "minecraft:smithing_transform": "minecraft:smithing_table",
    "create:mixing": "create:mechanical_mixer",
    "create:milling": "create:millstone",
    "create:crushing": "create:crushing_wheel",
    "create:pressing": "create:mechanical_press",
    "create:deploying": "create:deployer",
    "create:mechanical_crafting": "create:mechanical_crafter",
    "create:cutting": "create:mechanical_saw",
    "create:filling": "create:spout",
    "create:splashing": "create:encased_fan",
    "create:haunting": "create:encased_fan",
    "farmersdelight:cutting": "farmersdelight:cutting_board",
    "farmersdelight:cooking": "farmersdelight:cooking_pot",
    "botania:mana_infusion": "botania:mana_pool",
    "botania:petal_apothecary": "botania:apothecary_default",
    "botania:runic_altar": "botania:runic_altar",
    "refurbished_furniture:workbench_constructing": "refurbished_furniture:workbench",
    "tanukidecor:diy": "tanukidecor:diy_workbench",
    "meadow:woodcutting": "meadow:woodcutter",
    "vinery:wine_fermentation": "vinery:fermentation_barrel",
    "vintagedelight:fermenting": "vintagedelight:fermenting_jar",
    "splendid_slimes:plort_ripping": "splendid_slimes:plort_rippit",
    "splendid_slimes:plort_pressing": "splendid_slimes:plort_press",
    "farm_and_charm:pot_cooking": "farm_and_charm:cooking_pot",
}
# 같은 아이템의 레시피가 여럿이면 플레이어가 보통 쓰는 것부터 보여준다.
RECIPE_ORDER = ["minecraft:crafting_shaped", "minecraft:crafting_shapeless", "minecraft:smithing_transform",
                "minecraft:smelting", "minecraft:blasting"]
# 레시피가 너무 많아 '재료로 쓰임'을 어지럽히는 종류 (석재 절단기 변형, 가구 작업대)
NOISY_USE_TYPES = {"minecraft:stonecutting", "refurbished_furniture:workbench_constructing", "tanukidecor:diy"}


def _unwrap(recipe):
    """forge:conditional처럼 감싼 레시피는 첫 번째 것을 쓴다."""
    while isinstance(recipe, dict) and isinstance(recipe.get("recipes"), list) and recipe["recipes"]:
        inner = recipe["recipes"][0]
        recipe = inner.get("recipe", inner) if isinstance(inner, dict) else inner
    return recipe if isinstance(recipe, dict) else {}


def _stacks(value):
    """결과 표기들 -> [(아이템 ID, 개수)]. 'id', {"item": id, "count": n}, 그 목록을 모두 받는다."""
    if isinstance(value, str):
        return [(value, 1)]
    if isinstance(value, list):
        return [s for v in value for s in _stacks(v)]
    if isinstance(value, dict):
        item = value.get("item") or value.get("id")
        if isinstance(item, str):
            return [(item, value.get("count", 1) or 1)]
        for key in ("result", "output", "stack"):
            if key in value:
                return _stacks(value[key])
    return []


def _ingredient(value):
    """재료 하나 -> (보여줄 이름, 아이템 ID들). 대체 재료 목록이면 첫 번째를 쓰고 '등'을 붙인다."""
    if isinstance(value, list):
        parts = [_ingredient(v) for v in value]
        parts = [p for p in parts if p[0]]
        if not parts:
            return "", []
        return parts[0][0] + (" 등" if len(parts) > 1 else ""), [i for p in parts for i in p[1]]
    if isinstance(value, str):
        value = {"tag": value[1:]} if value.startswith("#") else {"item": value}
    if not isinstance(value, dict):
        return "", []
    if isinstance(value.get("item"), str):
        return display_name(value["item"]), [value["item"]]
    if isinstance(value.get("tag"), str):
        return _tag_name(value["tag"]), []
    if isinstance(value.get("fluid"), str):
        return f"{display_name(value['fluid'])}(액체)", []
    for key in ("ingredient", "base", "value"):
        if key in value:
            return _ingredient(value[key])
    return "", []


# 이름으로 대표 아이템을 짐작할 수 없는 흔한 태그
TAG_EXAMPLES = {"rods/wooden": "minecraft:stick", "stone_tool_materials": "minecraft:cobblestone",
                "stone_crafting_materials": "minecraft:cobblestone", "logs": "minecraft:oak_log",
                "planks": "minecraft:oak_planks", "wool": "minecraft:white_wool", "cobblestone": "minecraft:cobblestone"}
_by_path = None


def _tag_name(tag):
    """'forge:ingots/iron' -> '철 주괴 등', 'forge:crops/tomato' -> '토마토 등'. 대표 아이템을 못 찾으면 태그 이름을 그대로."""
    global _by_path
    if _by_path is None:
        _by_path = {}
        # 바닐라를 먼저 둬서 같은 경로면 바닐라 아이템을 대표로 쓴다.
        for item_id in sorted(names(), key=lambda i: not i.startswith("minecraft:")):
            _by_path.setdefault(item_id.split(":")[-1], item_id)
    path = tag.split(":")[-1]
    parts = path.split("/")
    single = lambda w: w[:-1] if w.endswith("s") else w
    candidates = [parts[-1], single(parts[-1])]
    if len(parts) == 2:
        candidates += [f"{parts[1]}_{single(parts[0])}", f"{parts[1]}_{parts[0]}"]
    found = TAG_EXAMPLES.get(path) or next((_by_path[c] for c in candidates if c in _by_path), None)
    return display_name(found) + " 등" if found else f"{path} 계열(태그)"


def _recipe_inputs(recipe):
    """레시피 JSON -> [(이름, 개수, 아이템 ID들)]. 모양 레시피는 패턴 속 글자 수만큼 센다."""
    inputs = []
    if isinstance(recipe.get("key"), dict) and isinstance(recipe.get("pattern"), list):
        counts = {}
        for row in recipe["pattern"]:
            for ch in row:
                if ch != " ":
                    counts[ch] = counts.get(ch, 0) + 1
        for ch, n in counts.items():
            name, ids = _ingredient(recipe["key"].get(ch))
            if name:
                inputs.append((name, n, ids))
    for key in ("template", "base", "addition", "ingredient", "ingredients", "input", "inputs"):
        value = recipe.get(key)
        if value is None:
            continue
        values = value if key in ("ingredients", "inputs") and isinstance(value, list) else [value]
        for v in values:
            name, ids = _ingredient(v)
            count = v.get("count", 1) if isinstance(v, dict) and isinstance(v.get("count"), int) else 1
            if name:
                inputs.append((name, count, ids))
    # 같은 재료는 합친다 (모양 없는 레시피는 같은 재료가 여러 번 나온다).
    merged = {}
    for name, n, ids in inputs:
        if name in merged:
            merged[name] = (name, merged[name][1] + n, merged[name][2])
        else:
            merged[name] = (name, n, ids)
    return list(merged.values())


def _station(rtype):
    block = RECIPE_STATIONS.get(rtype, rtype)
    return display_name(block) if block in names() else rtype


RECIPE_DUMP_VERSION = 2




def sell_prices():
    """배송 상자 판매가(config.PRICES_FILE, kubejs/askhowto_prices.js가 서버 시작 때 씀): 아이템 ID -> 카드 한 줄.
    '<ID>/<변종>' 키(슬라임 플로트·나비 변종)는 뺀다. 파일이 없으면 빈 dict."""
    if not config.PRICES_FILE.exists():
        return {}
    raw = _load_json(config.PRICES_FILE.read_text(encoding="utf-8")) or {}
    raw.pop("_askhowto", None)
    out = {}
    for item_id, trade in raw.items():
        if "/" in item_id or not isinstance(trade, dict) or not trade.get("value"):
            continue
        # 품질 배수: globalShippingBinHandlers.js calculateQualityValue (별 1·2·3개 = 1.25·1.5·2배)
        out[item_id] = (f"배송 상자에 넣어 팔면 코인 {int(trade['value']):,} "
                        f"(품질 별 1·2·3개면 1.25·1.5·2배, 스킬 판매 배수는 따로)")
    return out


def recipes():
    """서버가 덤프한 최종 레시피(config.RECIPES_FILE, kubejs/askhowto_recipes.js가 서버 시작 때 씀)에서
    아이템 ID -> {"make": ['조합(제작대): 막대기 2, 실 3'], "uses": [이 아이템이 재료인 결과물 ID들]}.
    파일이 없으면 None (레시피를 모르는 것이지 '만들 수 없다'가 아니다)."""
    if not config.RECIPES_FILE.exists():
        return None
    raw = _load_json(config.RECIPES_FILE.read_text(encoding="utf-8")) or {}
    # 11/03 첫 덤프는 스크립트가 추가한 레시피(대장장이 업그레이드 등)가 빠져 있었다. 그런 덤프로 '만들 수 없음'이라 답하면 안 된다.
    meta = _load_json(raw.pop("_askhowto", "")) or {}
    if meta.get("version", 0) < RECIPE_DUMP_VERSION:
        return None
    made, uses = {}, {}
    for rid, text in raw.items():
        recipe = _unwrap(_load_json(text) if isinstance(text, str) else text)
        rtype = recipe.get("type", "")
        # 스크립트가 추가한 레시피는 덤프 시점에 종류가 'unknown'으로 남을 수 있다. 형판+기본+재료 꼴이면 대장장이 작업대다.
        if rtype in ("", "unknown") and all(k in recipe for k in ("template", "base", "addition")):
            rtype = "minecraft:smithing_transform"
        outputs = _stacks(recipe.get("result") or recipe.get("results") or recipe.get("output") or recipe.get("outputs"))
        inputs = _recipe_inputs(recipe)
        if not outputs or not inputs:
            continue
        item, count = outputs[0]
        parts = ", ".join(f"{name} {n}" if n > 1 else name for name, n, _ in inputs)
        line = f"{_station(rtype)}에서: {parts}" + (f" → {count}개" if count > 1 else "")
        made.setdefault(item, []).append((RECIPE_ORDER.index(rtype) if rtype in RECIPE_ORDER else 99, line))
        if rtype not in NOISY_USE_TYPES:
            for _, _, ids in inputs:
                for i in ids:
                    if i != item:
                        uses.setdefault(i, []).append(item)
    out = {}
    for item, lines in made.items():
        out[item] = {"make": list(dict.fromkeys(l for _, l in sorted(lines))), "uses": []}
    for item, products in uses.items():
        out.setdefault(item, {"make": [], "uses": []})["uses"] = list(dict.fromkeys(products))
    return out


def _nbt(data):
    """압축 푼 NBT -> dict (level.dat의 게임룰을 읽는 데만 쓰는 최소 구현)."""
    import struct
    pos = 0

    def take(n):
        nonlocal pos
        pos += n
        return data[pos - n:pos]

    def payload(tag):
        if tag in (1, 2, 3, 4, 5, 6):
            fmt = {1: ">b", 2: ">h", 3: ">i", 4: ">q", 5: ">f", 6: ">d"}[tag]
            return struct.unpack(fmt, take(struct.calcsize(fmt)))[0]
        if tag in (7, 11, 12):
            n = struct.unpack(">i", take(4))[0]
            take(n * {7: 1, 11: 4, 12: 8}[tag])
            return None
        if tag == 8:
            return take(struct.unpack(">H", take(2))[0]).decode("utf-8", "replace")
        if tag == 9:
            inner, n = take(1)[0], struct.unpack(">i", take(4))[0]
            return [payload(inner) for _ in range(n)]
        if tag == 10:
            out = {}
            while (t := take(1)[0]) != 0:
                key = take(struct.unpack(">H", take(2))[0]).decode("utf-8", "replace")
                out[key] = payload(t)
            return out
        raise ValueError(f"NBT 태그 {tag}")

    tag = take(1)[0]
    take(struct.unpack(">H", take(2))[0])
    return payload(tag)


def gamerules():
    """서버 월드(level.dat)의 게임룰 -> {"keepInventory": "true", ...}. 못 읽으면 {}."""
    import gzip
    props = config.SERVER_DIR / "server.properties"
    level = "world"
    if props.exists():
        m = re.search(r"^level-name=(.+)$", props.read_text(encoding="utf-8"), re.M)
        level = m.group(1).strip() if m else level
    try:
        root = _nbt(gzip.decompress((config.SERVER_DIR / level / "level.dat").read_bytes()))
        return root["Data"]["GameRules"]
    except (OSError, KeyError, ValueError, IndexError):
        return {}


LOOT_RE = re.compile(r"data/([^/]+)/loot_tables/(blocks|entities|chests|gameplay)/(.+)\.json$")
MAX_LOOT_LINES = 6


def _loot_items(node):
    """전리품 테이블 JSON 안의 아이템 ID들 (풀·대체·그룹을 따라 내려간다)."""
    out = []
    if isinstance(node, dict):
        if node.get("type") in ("minecraft:item", "item") and isinstance(node.get("name"), str):
            out.append(node["name"])
        for key in ("pools", "entries", "children"):
            out += [i for child in node.get(key, []) or [] for i in _loot_items(child)]
    elif isinstance(node, list):
        out += [i for child in node for i in _loot_items(child)]
    return out


def loot_sources():
    """아이템 ID -> ['몹 '좀비'가 떨어뜨림', '블록 '석탄 광석'을 캐면 나옴', '구조물 상자(village 등 3곳)', '낚시로 나옴'].
    모드 jar의 기본 전리품 테이블이라 KubeJS(server_scripts/loot)가 바꾼 것은 반영되지 않는다.
    블록이 자기 자신을 떨어뜨리는 경우는 뺀다(블록 테이블 1만 개 대부분). 이름 끝만 다른 상자(balloon_chest_blue/_red …)는 묶는다."""
    found = {}  # 아이템 -> {(종류, 이름): 줄}
    for _, entry, data in _jar_files(LOOT_RE):
        ns, kind, path = LOOT_RE.match(entry).groups()
        table = _load_json(data)
        if not table:
            continue
        source_id = f"{ns}:{path}"
        for item in set(_loot_items(table)):
            if kind == "blocks" and item == source_id:
                continue
            found.setdefault(item, set()).add((kind, path, source_id))
    out = {}
    order = {"entities": 0, "blocks": 1, "gameplay": 2, "chests": 3}
    for item, sources in found.items():
        chests = {}
        lines = []
        for kind, path, source_id in sorted(sources, key=lambda s: (order[s[0]], s[1])):
            if kind == "entities":
                line = f"몹 '{display_name(source_id)}'이(가) 떨어뜨림"
            elif kind == "blocks":
                line = f"블록 '{display_name(source_id)}'을(를) 캐면 나옴"
            elif kind == "gameplay":
                line = "낚시로 나옴" + (f"({path.split('/', 1)[1]})" if path.startswith("fishing/") else "") \
                    if path.startswith("fishing") else f"게임플레이 보상({path})"
            else:
                chests.setdefault(re.sub(r"[_/][^_/]*$", "", path) or path, []).append(path)
                continue
            if line not in lines:
                lines.append(line)
        for base, paths in chests.items():
            lines.append(f"구조물 상자 전리품({paths[0] if len(paths) == 1 else f'{base} 등 {len(paths)}곳'})")
        out[item] = lines[:MAX_LOOT_LINES]
    return out
