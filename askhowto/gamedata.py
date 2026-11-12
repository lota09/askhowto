"""서버 데이터에서 아이템 이름(한/영)과 '어디서 얻나'에 답할 사실을 모은다.

- 이름: 바닐라(data/vanilla, Mojang 에셋에서 받은 ko_kr + 서버 jar의 en_us), kubejs/assets의 ko_kr, 모드 jar의 en_us,
  그리고 아이템 ID에서 만든 영어 별칭('society:ancient_fruit' -> 'ancient fruit').
- 획득처: society_trading 상점(누가 얼마에 파는지), 농업연감 가축의 생산품(드롭, 착유, 산란 등).
- 제철: Serene Seasons 태그(모드 jar 기본값 - KubeJS handleSeasonTags.js 제거 목록 + 추가 목록).
"""
import json
import re
import subprocess
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
    # 팩 스크립트가 추가한 제작대 레시피(이름이 'kubejs:shaped에서'로 나왔다, 11/07)
    "kubejs:shaped": "minecraft:crafting_table",
    "kubejs:shapeless": "minecraft:crafting_table",
    "create:compacting": "create:mechanical_press",
    "botania:elven_trade": "botania:alfheim_portal",
    "beachparty:tiki_bar_mixing": "beachparty:tiki_bar",
    "beachparty:mini_fridge_mixing": "beachparty:mini_fridge",
    "create_mechanical_extruder:extruding": "create_mechanical_extruder:mechanical_extruder",
    "meadow:cheese": "meadow:cheese_form",
    "botania:terra_plate": "botania:terra_plate",
    "botania:brew": "botania:brewery",
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


# 블록 하나로 말할 수 없는 방식
RECIPE_METHODS = {
    "create:item_application": "아이템 적용(재료를 손에 들고 블록에 우클릭)",
    "create:sequenced_assembly": "Create 순서 조립(기계 손·압착기 등을 차례로 거침)",
    "create:compacting": "압착기+대야(압축)",
}


def _station(rtype):
    if rtype in RECIPE_METHODS:
        return RECIPE_METHODS[rtype]
    block = RECIPE_STATIONS.get(rtype, rtype)
    return display_name(block) if block in names() else rtype


RECIPE_DUMP_VERSION = 2




_startup = None


def startup_dump():
    """KubeJS startup_scripts를 서버 밖에서 Rhino(서버 mods의 KubeJS용 Rhino)로 실행해 뽑은 전역 표:
    {"machines": [장인 설비 정의와 레시피], "trades": {아이템 ID: {value, multiplier}}}.
    서버를 재시작하지 않아도 스크립트가 바뀌면 바로 반영된다. 11/07 확인: 판매가 1,997개가 서버 덤프와 모두 같았다.
    실행이 안 되면(JDK 없음 등) 지난번 결과(data/startup_dump.json), 그것도 없으면 None."""
    global _startup
    if _startup is not None:
        return _startup or None
    tool = config.ROOT / "tools_offline"
    classes = config.ROOT / "runs" / "offline"
    mods, libs = config.SERVER_DIR / "mods", config.SERVER_DIR / "libraries"
    jars = sorted(mods.glob("rhino-forge-*.jar"))[:1] + sorted(libs.rglob("gson-*.jar"))[:1] + sorted(libs.rglob("commons-lang3-*.jar"))[:1]
    try:
        cp = ":".join(map(str, jars + [classes]))
        if not (classes / "DumpStartup.class").exists() or (tool / "DumpStartup.java").stat().st_mtime > (classes / "DumpStartup.class").stat().st_mtime:
            classes.mkdir(parents=True, exist_ok=True)
            subprocess.run([str(config.JDK_BIN / "javac"), "-cp", cp, "-d", str(classes), str(tool / "DumpStartup.java")],
                           check=True, capture_output=True, timeout=300)
        run = subprocess.run([str(config.JDK_BIN / "java"), "-cp", cp, "DumpStartup",
                              str(config.SERVER_DIR / "kubejs" / "startup_scripts"), str(tool / "prelude.js"), str(tool / "dump.js")],
                             check=True, capture_output=True, timeout=300, text=True)
        data = json.loads(run.stdout)
        config.STARTUP_DUMP_FILE.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    except (OSError, subprocess.SubprocessError, ValueError) as err:
        print(f"startup_scripts 실행 실패, 지난 결과를 쓴다: {err}")
        data = _load_json(config.STARTUP_DUMP_FILE.read_text(encoding="utf-8")) if config.STARTUP_DUMP_FILE.exists() else None
    _startup = data or {}
    return data


def _stack(text):
    """'2x society:ocean_stone' -> ('society:ocean_stone', 2)"""
    m = re.match(r"\s*(\d+)x\s+(.+)", str(text))
    return (m.group(2).strip(), int(m.group(1))) if m else (str(text).strip(), 1)


def machine_recipes():
    """장인 설비 레시피 -> [(만들어지는 아이템 ID, 재료 ID, 줄, 설비 ID)]. 예:
    ('society:oak_resin', 'minecraft:oak_log', '수액 채취기에서: 참나무 원목에 붙이면 약 5일마다 → 참나무 수지 1개', 'society:tapper').
    재료가 태그('#minecraft:wool')면 재료 ID 자리에 태그를 둔다."""
    dump = startup_dump() or {}
    out = []
    for machine in dump.get("machines", []):
        recipes = machine.get("recipes")
        if not isinstance(recipes, dict):
            continue
        station = display_name(machine["id"])
        need = int(machine.get("maxInput") or 1)
        for source, recipe in recipes.items():
            if not isinstance(recipe, dict) or not recipe.get("output"):
                continue
            days = recipe.get("time") or machine.get("stageCount")
            src_name = _tag_name(source[1:]) if source.startswith("#") else display_name(source)
            outs = [_stack(o) for o in recipe["output"]]
            made = ", ".join(f"{display_name(i)} {n}개" for i, n in outs)
            when = f"약 {days:g}일" if isinstance(days, (int, float)) and days else ""
            if machine["id"] == "society:tapper":
                line = f"{station}에서: {src_name}에 붙이면 {when}마다 → {made}"
            else:
                line = f"{station}에서: {src_name}" + (f" {need}개" if need > 1 else "") + f" → {made}" + (f" ({when})" if when else "")
            for item, _ in outs:
                out.append((item, source, line, machine["id"]))
    return out


_item_tags = None


def item_tags():
    """아이템 태그 -> 멤버 아이템 ID 집합 (모드 jar와 바닐라 데이터, 태그 안의 태그도 풀어서)."""
    global _item_tags
    if _item_tags is not None:
        return _item_tags
    pattern = re.compile(r"data/([^/]+)/tags/items?/(.+)\.json$")
    raw = {}
    for _, entry, data in _jar_files(re.compile(r"data/[^/]+/tags/items?/.+\.json$")):
        m = pattern.match(entry)
        d = _load_json(data) or {}
        values = [v if isinstance(v, str) else (v or {}).get("id") for v in d.get("values", [])]
        key = f"{m.group(1)}:{m.group(2)}"
        raw[key] = set(values) if d.get("replace") else raw.get(key, set()) | set(values)

    def members(tag, seen=()):
        out = set()
        for v in raw.get(tag, ()):
            if v and v.startswith("#"):
                if v[1:] not in seen:
                    out |= members(v[1:], seen + (tag,))
            elif v:
                out.add(v)
        return out
    _item_tags = {tag: members(tag) for tag in raw}
    return _item_tags


CATEGORY_MIN, CATEGORY_MAX = 3, 80


def categories():
    """묶음 이름(한 낱말) -> 멤버 아이템 ID들. 태그의 한국어 이름(lang의 tag.item.*)이 그 낱말이거나 그 낱말로 끝나는 태그를 모은다.
    '갑옷' <- '장식 가능한 갑옷'(minecraft:trimmable_armor). '갑옷 업그레이드 어떻게 해?'가 실제 아이템(철 흉갑 등)에 닿게 한다(11/07)."""
    ko, _ = langs()
    tags = item_tags()
    out = {}
    for key, label in ko.items():
        if not key.startswith("tag.item.") or not isinstance(label, str):
            continue
        rest = key[len("tag.item."):]
        ns, _, path = rest.partition(".")
        members = tags.get(f"{ns}:{path.replace('.', '/')}", set())
        word = label.strip().split()[-1] if label.strip() else ""
        if re.fullmatch(r"[가-힣]{1,6}", word) and CATEGORY_MIN <= len(members) <= CATEGORY_MAX:
            out.setdefault(word, set()).update(members)
    return {w: ids for w, ids in out.items() if len(ids) <= CATEGORY_MAX}


def _data_files(pattern):
    """데이터팩 파일 (리소스 경로 -> JSON). 모드 jar·바닐라 위에 kubejs/data가 같은 경로를 덮어쓴다(팩이 끈 것 반영)."""
    out = {}
    regex = re.compile(pattern)
    for _, entry, data in _jar_files(regex):
        out[entry] = _load_json(data)
    base = config.SERVER_DIR / "kubejs"
    for path in (base / "data").rglob("*.json"):
        entry = str(path.relative_to(base))
        if regex.match(entry):
            out[entry] = _load_json(path.read_text(encoding="utf-8", errors="replace"))
    return {k: v for k, v in out.items() if isinstance(v, dict)}


def _res(entry):
    """'data/minecraft/worldgen/placed_feature/ore_gold.json' -> 'minecraft:ore_gold'"""
    m = re.match(r"data/([^/]+)/worldgen/[^/]+/(.+)\.json$", entry)
    return f"{m.group(1)}:{m.group(2)}" if m else entry


BIOME_SET_KO = {"#minecraft:is_overworld": "지상(오버월드)", "#society:mining_biomes": "지상과 해골 광산",
                "#society:is_skull_cavern": "해골 광산", "#minecraft:is_nether": "네더",
                "#society:spawns_magma_geodes": "네더와 해골 광산의 사막·흑암 동굴", "#forge:is_overworld": "지상(오버월드)"}


# 차원별 (바닥, 천장) — 'above_bottom'/'below_top' 높이를 실제 Y로 바꾸는 데 쓴다(네더는 논리 높이 128)
DIM_BOUNDS = {"지상": (-64, 320), "네더": (0, 128)}


def _y(anchor, dim="지상"):
    bottom, top = DIM_BOUNDS.get(dim, DIM_BOUNDS["지상"])
    if not isinstance(anchor, dict):
        return None
    if "absolute" in anchor:
        return int(anchor["absolute"])
    if "above_bottom" in anchor:
        return bottom + int(anchor["above_bottom"])
    if "below_top" in anchor:
        return top - int(anchor["below_top"])
    return None


def _biome_label(dim, biomes):
    """생물군계가 많으면 차원 이름, 몇 개뿐이면 그 생물군계 이름들('악지')."""
    if len(biomes) >= 10:
        return dim
    ko, en = langs()
    names = [ko.get(f"biome.{b.replace(':', '.')}") or en.get(f"biome.{b.replace(':', '.')}") or b.split(":")[-1] for b in sorted(biomes)]
    return f"{dim}의 " + "·".join(dict.fromkeys(names)) + " 생물군계"


def ore_generation():
    """광석 블록 ID -> ['Y -64~32, 가장 많은 높이 Y -16, 한 덩어리 최대 9개, 청크당 4번, 지상(오버월드)' …].
    바닐라는 생물군계 정의에 든 광석, 모드는 생물군계 수정(forge:add_features)으로 더한 광석. 팩이 끈 것(forge:false)은 뺀다.
    ('금좌표', 11/08 — 직접 쓴 바닐라 문서 말고는 광석 높이 자료가 없었다)"""
    placed = {_res(k): v for k, v in _data_files(r"data/[^/]+/worldgen/placed_feature/.+\.json$").items()}
    configured = {_res(k): v for k, v in _data_files(r"data/[^/]+/worldgen/configured_feature/.+\.json$").items()}
    where, dims = {}, {}  # placed feature -> 어디에서(글), 차원
    by_biome = {}
    for k, biome in _data_files(r"data/[^/]+/worldgen/biome/.+\.json$").items():
        dim = "네더" if any(w in k for w in ("nether", "crimson", "warped", "basalt", "soul_sand")) else "지상"
        for step in biome.get("features") or []:
            for f in step if isinstance(step, list) else []:
                if isinstance(f, str):
                    by_biome.setdefault(f, {}).setdefault(dim, set()).add(_res(k).replace("worldgen/biome/", ""))
    for f, per_dim in by_biome.items():
        for dim, biomes in per_dim.items():
            where.setdefault(f, set()).add(_biome_label(dim, biomes))
            dims[f] = dim
    for k, mod in _data_files(r"data/[^/]+/forge/biome_modifier/.+\.json$").items():
        if mod.get("type") != "forge:add_features" or any(c.get("type") == "forge:false" for c in mod.get("forge:conditions", []) if isinstance(c, dict)):
            continue
        biomes = mod.get("biomes")
        label = BIOME_SET_KO.get(biomes, biomes) if isinstance(biomes, str) else "여러 생물군계"
        for f in [mod.get("features")] if isinstance(mod.get("features"), str) else (mod.get("features") or []):
            if isinstance(f, str):
                where.setdefault(f, set()).add(label)
    out = {}
    for pid, feature in placed.items():
        if pid not in where:
            continue
        conf = configured.get(feature.get("feature")) if isinstance(feature.get("feature"), str) else None
        if not conf or conf.get("type") not in ("minecraft:ore", "minecraft:scattered_ore"):
            continue
        cfg = conf.get("config") or {}
        blocks = [(t.get("state") or {}).get("Name") for t in cfg.get("targets") or [] if isinstance(t, dict)]
        count, low, high, peak = None, None, None, None
        for step in feature.get("placement") or []:
            if step.get("type") == "minecraft:count" and isinstance(step.get("count"), int):
                count = step["count"]
            if step.get("type") == "minecraft:rarity_filter":
                count = f"1/{step.get('chance')}"
            if step.get("type") == "minecraft:height_range":
                h = step.get("height") or {}
                dim = dims.get(pid, "네더" if "네더" in " ".join(where[pid]) else "지상")
                low, high = _y(h.get("min_inclusive"), dim), _y(h.get("max_inclusive"), dim)
                if h.get("type") == "minecraft:trapezoid" and low is not None and high is not None:
                    peak = (low + high) // 2
                # 바닥보다 아래까지 잡은 범위(다이아몬드 -144)는 실제로는 바닥에서 시작한다
                bottom, top = DIM_BOUNDS[dim]
                low, high = (max(low, bottom) if low is not None else None), (min(high, top) if high is not None else None)
                peak = max(peak, bottom) if peak is not None else None
        if low is None:
            continue
        line = f"Y {low}~{high}" + (f", 가장 많은 높이 Y {peak}" if peak is not None else ", 이 범위에 고르게")
        line += f", 한 덩어리 최대 {cfg.get('size')}개" if cfg.get("size") else ""
        line += f", 청크당 {count}번" if count is not None else ""
        line += f" ({', '.join(sorted(where[pid]))})"
        for b in dict.fromkeys(b for b in blocks if b):
            if line not in out.setdefault(b, []):
                out[b].append(line)
    # 팩이 생성을 끈 광석(설정을 no_op으로 덮어씀: 고대 잔해 등)
    originals = {}
    for _, entry, data in _jar_files(re.compile(r"data/[^/]+/worldgen/configured_feature/.+\.json$")):
        d = _load_json(data) or {}
        if d.get("type") in ("minecraft:ore", "minecraft:scattered_ore"):
            originals.setdefault(_res(entry), [(t.get("state") or {}).get("Name") for t in (d.get("config") or {}).get("targets") or []])
    for cid, blocks in originals.items():
        if (configured.get(cid) or {}).get("type") == "minecraft:no_op":
            for b in blocks:
                if b and b not in out:
                    out[b] = ["이 서버에서는 자연 생성되지 않음(팩이 생성을 껐다)"]
    return out


def tree_biomes():
    """원목 블록 ID -> 그 나무가 자연히 자라는 생물군계 이름들. 생물군계의 지형 기능을 따라가(무작위 선택 → 배치 → 나무)
    나무 줄기 블록을 찾는다. 모드가 생물군계 수정으로 더한 나무도 포함. ('가문비나무 어느 생물군계에서 구해?', 11/11)"""
    placed = {_res(k): v for k, v in _data_files(r"data/[^/]+/worldgen/placed_feature/.+\.json$").items()}
    configured = {_res(k): v for k, v in _data_files(r"data/[^/]+/worldgen/configured_feature/.+\.json$").items()}
    ko, en = langs()

    def from_placed(ref, depth=0):
        """배치 기능(ID 또는 객체) -> 나무 줄기 블록들. 배치의 'feature'는 설정 기능을 가리킨다(ID가 같아도 다른 것)."""
        if depth > 8 or ref is None:
            return set()
        if isinstance(ref, str):
            ref = placed.get(ref)
        return from_configured(ref.get("feature"), depth + 1) if isinstance(ref, dict) else set()

    def from_configured(ref, depth=0):
        if depth > 8 or ref is None:
            return set()
        if isinstance(ref, str):
            ref = configured.get(ref)
        if not isinstance(ref, dict):
            return set()
        cfg = ref.get("config") or {}
        if ref.get("type") == "minecraft:tree":
            state = ((cfg.get("trunk_provider") or {}).get("state") or {}).get("Name")
            return {state} if state else set()
        out = set()
        for item in cfg.get("features") or []:  # random_selector: {chance, feature(배치)} / simple_random_selector: 배치들
            out |= from_placed(item.get("feature") if isinstance(item, dict) and "chance" in item else item, depth + 1)
        for key in ("default", "feature_true", "feature_false"):
            if key in cfg:
                out |= from_placed(cfg[key], depth + 1)
        if isinstance(cfg.get("feature"), (str, dict)):  # random_patch 등
            out |= from_placed(cfg["feature"], depth + 1)
        return out

    trunks = from_placed

    def biome_name(bid):
        key = f"biome.{bid.replace(':', '.')}"
        return ko.get(key) or en.get(key) or bid.split(":")[-1].replace("_", " ")

    tag_raw = {}
    for k, d in _data_files(r"data/[^/]+/tags/worldgen/biome/.+\.json$").items():
        m = re.match(r"data/([^/]+)/tags/worldgen/biome/(.+)\.json$", k)
        tag_raw[f"{m.group(1)}:{m.group(2)}"] = [v if isinstance(v, str) else (v or {}).get("id") for v in d.get("values", [])]

    def tag_biomes(tag, seen=()):
        out = []
        for v in tag_raw.get(tag, []):
            if v and v.startswith("#"):
                out += tag_biomes(v[1:], seen + (tag,)) if v[1:] not in seen else []
            elif v:
                out.append(v)
        return out

    def label_of(biomes):
        """생물군계 수정의 'biomes'(태그나 목록) -> 생물군계 이름들."""
        if isinstance(biomes, str) and biomes in BIOME_SET_KO:
            return [BIOME_SET_KO[biomes]]
        ids = tag_biomes(biomes[1:]) if isinstance(biomes, str) and biomes.startswith("#") else (
            [biomes] if isinstance(biomes, str) else [b for b in biomes or [] if isinstance(b, str)])
        return [biome_name(b) for b in ids if not b.startswith("#")] or ["여러 생물군계"]

    out = {}
    for k, biome in _data_files(r"data/[^/]+/worldgen/biome/.+\.json$").items():
        bid = _res(k).replace("worldgen/biome/", "")
        for step in biome.get("features") or []:
            for f in step if isinstance(step, list) else []:
                for log in trunks(f):
                    out.setdefault(log, set()).add(biome_name(bid))
    for k, mod in _data_files(r"data/[^/]+/forge/biome_modifier/.+\.json$").items():
        if mod.get("type") != "forge:add_features" or any(
                c.get("type") == "forge:false" for c in mod.get("forge:conditions", []) if isinstance(c, dict)):
            continue
        labels = label_of(mod.get("biomes"))
        for f in [mod.get("features")] if isinstance(mod.get("features"), str) else (mod.get("features") or []):
            for log in trunks(f):
                out.setdefault(log, set()).update(labels)
    return {log: sorted(b) for log, b in out.items()}


def automation_io():
    """KubeJS로 만든 블록 중 아이템 입출력 기능(CapabilityBuilder.ITEM의 insertItem/extractItem)을 붙인 것:
    블록 ID -> '호퍼·파이프로 아이템을 넣을 수 있음, 뺄 수 있음'. ('호퍼로 배송상자에 넣을 수 있어?', 11/07)"""
    out = {}
    for path in (config.SERVER_DIR / "kubejs" / "startup_scripts").rglob("*.js"):
        src = path.read_text(encoding="utf-8", errors="replace")
        starts = [(m.start(), m.group(1)) for m in re.finditer(r'\.create\(\s*"([a-z0-9_]+:[a-z0-9_/]+)"', src)]
        for n, (pos, block_id) in enumerate(starts):
            part = src[pos:starts[n + 1][0] if n + 1 < len(starts) else len(src)]
            if "CapabilityBuilder.ITEM" not in part:
                continue
            can = [w for w, key in (("넣을", ".insertItem("), ("뺄", ".extractItem(")) if key in part]
            if can:
                out[block_id] = "호퍼·파이프로 아이템을 " + "·".join(can) + " 수 있음"
    # 장인 호퍼가 다루는 설비('case "society:fish_smoker"'): 입출력 기능이 없으면 일반 호퍼로는 못 넣는다
    # ('생선 훈제기에 깔때기로 아이템 들어감?', 11/09).
    hoppers, served = [], set()
    for path in (config.SERVER_DIR / "kubejs" / "startup_scripts").rglob("*.js"):
        src = path.read_text(encoding="utf-8", errors="replace")
        made = re.findall(r'\.create\(\s*"([a-z0-9_]+:[a-z0-9_]*artisan_hopper)"', src)
        if made:
            hoppers += made
            served |= set(re.findall(r'case\s+"([a-z0-9_]+:[a-z0-9_]+)"', src))
    hopper_names = "·".join(display_name(h) for h in dict.fromkeys(hoppers))
    for machine_id in sorted(served):
        if machine_id not in out and machine_id in names():
            out[machine_id] = f"일반 호퍼·파이프로는 아이템을 못 넣고 뺄 수도 없음. {hopper_names}(위에 재료 보관함, 아래에 받을 보관함)로 자동화한다"
    for h in dict.fromkeys(hoppers):
        out[h] = (out.get(h, "") + " / " if h in out else "") + "자동화하는 설비: " + ", ".join(
            display_name(m) for m in sorted(served) if m in names())
    return out


def sell_values():
    """아이템 ID -> 배송 상자 기본 판매가(코인). '<ID>/<변종>' 키는 뺀다."""
    if config.PRICES_FILE.exists():
        raw = _load_json(config.PRICES_FILE.read_text(encoding="utf-8")) or {}
    else:
        raw = (startup_dump() or {}).get("trades", {})
    return {i: int(t["value"]) for i, t in raw.items()
            if "/" not in i and isinstance(t, dict) and t.get("value")}


# 판매가 종류(globalRegistry.js getConfiguredValue의 배수 이름) -> 스킬 트리에서 쓰는 말 ('농부 생산품의 가치 +15%')
SELL_KIND_KO = {"crop": "농부 생산품", "gem": "지질학자 생산품", "wood": "장인 생산품", "meat": "탐험가 생산품"}


def sell_kinds():
    """아이템 ID -> 판매가 종류의 한국어 이름('농부 생산품'). 스킬 보너스가 어느 아이템에 붙는지 잇는다."""
    if config.PRICES_FILE.exists():
        raw = _load_json(config.PRICES_FILE.read_text(encoding="utf-8")) or {}
        kind = lambda t: t.get("kind")
    else:
        raw = (startup_dump() or {}).get("trades", {})
        kind = lambda t: str(t.get("multiplier", "")).split(":")[-1].replace("_sell_multiplier", "")
    return {i: SELL_KIND_KO[kind(t)] for i, t in raw.items() if isinstance(t, dict) and kind(t) in SELL_KIND_KO}


def sell_prices():
    """배송 상자 판매가: 아이템 ID -> 카드 한 줄. 서버 덤프(config.PRICES_FILE, kubejs/askhowto_prices.js)가 없으면
    시작 스크립트를 직접 실행한 표(startup_dump)를 쓴다(값은 같다)."""
    # 품질 배수: globalShippingBinHandlers.js calculateQualityValue (별 1·2·3개 = 1.25·1.5·2배)
    kinds = sell_kinds()
    return {item_id: f"배송 상자에 넣어 팔면 코인 {value:,} ("
                     + (f"{kinds[item_id]}, " if item_id in kinds else "")
                     + "품질 별 1·2·3개면 1.25·1.5·2배, 스킬 보너스는 따로)"
            for item_id, value in sell_values().items()}

# 블록을 꽃 옆에 두면 바뀌는 보타니아 레시피: 종류 -> 꽃 블록 (입력·출력이 아이템이 아니라 블록이라 따로 읽는다)
FLOWER_RECIPES = {"botania:pure_daisy": "botania:pure_daisy", "botania:state_copying_pure_daisy": "botania:pure_daisy",
                  "botania:orechid": "botania:orechid", "botania:orechid_ignem": "botania:orechid_ignem",
                  "botania:marimorphosis": "botania:marimorphosis"}


def _block_side(value):
    """{"type": "block", "block": id} / {"type": "tag", "tag": t} / {"name": id} / id -> (이름, ID들)."""
    if isinstance(value, str):
        return display_name(value), [value]
    if isinstance(value, dict):
        if value.get("tag"):
            return _tag_name(value["tag"]), []
        block = value.get("block") or value.get("name") or value.get("id")
        if isinstance(block, str):
            return display_name(block), [block]
        if isinstance(value.get("blocks"), list) and value["blocks"]:
            return display_name(value["blocks"][0]) + (" 등" if len(value["blocks"]) > 1 else ""), list(value["blocks"])
    return None, []


def _flower_line(recipe, rtype):
    """정화의 데이지 등: (결과 ID, '정화의 데이지 옆에 돌을 놓으면 바뀜', 종류, 재료들) 또는 None.
    ('생명의 돌 반 블록은 어케 얻는데' — 생명의 돌의 시작점이 이 레시피인데 자료에 없었다, 11/08)"""
    src_name, src_ids = _block_side(recipe.get("input"))
    out_name, out_ids = _block_side(recipe.get("output"))
    if not src_name or not out_ids:
        return None
    flower = display_name(FLOWER_RECIPES[rtype])
    return out_ids[0], f"{flower}(꽃) 옆에 블록을 놓아 두면 바뀜: {src_name} → {out_name}", rtype, [(src_name, 1, src_ids)]


def _recipe_line(recipe):
    """레시피 JSON -> (결과 ID, '제작대에서: 막대기 2, 실 3', 종류, 재료들) 또는 None."""
    rtype = recipe.get("type", "")
    if rtype in FLOWER_RECIPES:
        return _flower_line(recipe, rtype)
    # 스크립트가 추가한 레시피는 덤프 시점에 종류가 'unknown'으로 남을 수 있다. 형판+기본+재료 꼴이면 대장장이 작업대다.
    if rtype in ("", "unknown") and all(k in recipe for k in ("template", "base", "addition")):
        rtype = "minecraft:smithing_transform"
    outputs = _stacks(recipe.get("result") or recipe.get("results") or recipe.get("output") or recipe.get("outputs"))
    inputs = _recipe_inputs(recipe)
    if not outputs or not inputs:
        return None
    item, count = outputs[0]
    parts = ", ".join(f"{name} {n}" if n > 1 else name for name, n, _ in inputs)
    return item, f"{_station(rtype)}에서: {parts}" + (f" → {count}개" if count > 1 else ""), rtype, inputs


CONDITION_KEYS = ("conditions", "forge:conditions", "fabric:load_conditions", "neoforge:conditions")


def removed_recipes():
    """모드 jar에는 있는데 서버 최종 레시피(덤프)에 없는 것 = 이 팩이 지운 레시피: 결과 ID -> [줄].
    ('안산암 합금: 제작대에서 안산암, 아연 조각' — 이 서버는 철 코인으로만 만든다, 11/07)
    다른 모드가 있을 때만 켜지는 조건부 레시피는 원래 안 쓰이는 것일 수 있어 뺀다. 덤프가 없으면 빈 dict."""
    if not config.RECIPES_FILE.exists():
        return {}
    dumped = set((_load_json(config.RECIPES_FILE.read_text(encoding="utf-8")) or {}).keys())
    pattern = re.compile(r"data/([^/]+)/recipes?/(.+)\.json$")
    out = {}
    for _, entry, data in _jar_files(re.compile(r"data/[^/]+/recipes?/.+\.json$")):
        m = pattern.match(entry)
        rid = f"{m.group(1)}:{m.group(2)}"
        recipe = _load_json(data)
        if rid in dumped or not isinstance(recipe, dict) or any(k in recipe for k in CONDITION_KEYS):
            continue
        parsed = _recipe_line(_unwrap(recipe))
        if parsed and parsed[1] not in out.get(parsed[0], []):
            out.setdefault(parsed[0], []).append(parsed[1])
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
    made, uses, line_inputs = {}, {}, {}
    for rid, text in raw.items():
        parsed = _recipe_line(_unwrap(_load_json(text) if isinstance(text, str) else text))
        if not parsed:
            continue
        item, line, rtype, inputs = parsed
        made.setdefault(item, []).append((RECIPE_ORDER.index(rtype) if rtype in RECIPE_ORDER else 99, line))
        line_inputs[(item, line)] = {i for _, _, ids in inputs for i in ids[:1]}
        if rtype not in NOISY_USE_TYPES:
            for _, _, ids in inputs:
                for i in ids:
                    if i != item:
                        uses.setdefault(i, []).append(item)
    # 장인 설비 레시피(KubeJS 전역 표라 레시피 덤프에 없다, 11/07 수액 채취기)
    for item, source, line, _ in machine_recipes():
        made.setdefault(item, []).append((len(RECIPE_ORDER), line))
        if not source.startswith("#") and source != item:
            # 설비 쓰임새는 앞에 둔다(원목처럼 재료로 수백 곳에 쓰이면 뒤에 붙인 것은 카드에서 잘린다).
            uses.setdefault(source, []).insert(0, item)
    # 서로를 재료로만 바꾸는 레시피(생명의 돌 ↔ 생명의 돌 반 블록, 블록 ↔ 주괴)는 얻는 방법이 아니다. 표시해서 다른 방법을 찾게 한다.
    for (item, line), srcs in list(line_inputs.items()):
        if len(srcs) == 1:
            other = next(iter(srcs))
            back = [l for l in made.get(other, []) if line_inputs.get((other, l[1])) == {item}]
            if back and len(back) == len(made.get(other, [])):
                made[item] = [(o, l + " (서로 바꾸는 것일 뿐, 처음 얻는 방법은 아님)" if l == line else l) for o, l in made[item]]
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
