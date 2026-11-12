"""모드팩에 들어있는 한국어 자료로 RAG 문서 조각(data/chunks.jsonl)을 만든다.

소스: FTB 퀘스트 설명, 농업연감/물고기 도감(Patchouli), 모드 아이템·블록 설명, Society 시스템 메시지,
NPC, 동전 가치, 스킬 트리, 팁, 서버 설정(계절), docs/manual/*.md (직접 쓴 문서).

조각에 names(한국어 이름 + 영어 이름)가 있으면 질문에 그 이름이 나올 때 검색 점수와 상관없이
'확정 정보'로 맨 앞에 넣는다(rag.Index.match_names).
"""
import json
import re

from . import config, gamedata, llm, wiki

CHUNK_MAX_CHARS = 700

EMOJI_RE = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️]")


def clean(text):
    """마인크래프트/Patchouli/FTB 서식 코드를 지운 평문."""
    text = text.replace("$(li)", "\n- ").replace("$(br2)", "\n").replace("$(br)", "\n")
    text = re.sub(r"\$\([^)]*\)", "", text)
    text = text.replace(":coin:", "코인 ")
    text = re.sub(r"§.", "", text)
    text = re.sub(r"&[0-9a-fk-or]", "", text)
    text = text.replace("\\&", "&")
    text = re.sub(r"\{image:[^}]*\}", "", text)
    text = re.sub(r"%(\d\$)?s", "○", text)
    text = text.replace("%%", "%")
    text = EMOJI_RE.sub("", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return re.sub(r"\n{2,}", "\n", text).strip()


def load_lang(namespace):
    path = config.SERVER_DIR / "kubejs" / "assets" / namespace / "lang" / "ko_kr.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def all_ko_langs():
    out = {}
    for path in sorted((config.SERVER_DIR / "kubejs" / "assets").glob("*/lang/ko_kr.json")):
        try:
            out[path.parent.parent.name] = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    return out


def aliases(name, item_id=None):
    """한국어 이름, 같은 아이템의 영어 이름, ID에서 만든 영어 별칭('ancient fruit').
    영어 이름으로 물어봐도(예: 'bank terminal') 찾을 수 있게 한다."""
    out = [name] if name else []
    if item_id:
        entry = gamedata.names().get(item_id, {})
        for alias in (clean(entry.get("en", "")), gamedata.id_alias(item_id)):
            if alias and alias.lower() not in (a.lower() for a in out):
                out.append(alias)
    return out


def lang_key_id(key):
    """'block.numismatics.bank_terminal' -> 'numismatics:bank_terminal'"""
    parts = key.split(".")
    return f"{parts[1]}:{parts[2]}" if len(parts) >= 3 else None


def split_lines(lines, limit=CHUNK_MAX_CHARS):
    """줄 단위로 limit 글자를 넘지 않게 묶는다."""
    parts, cur = [], []
    for line in lines:
        if cur and sum(len(x) + 1 for x in cur) + len(line) > limit:
            parts.append(cur)
            cur = []
        cur.append(line)
    if cur:
        parts.append(cur)
    return parts


def chunk(source, cid, title, lines, names=None):
    out = []
    parts = split_lines([l for l in lines if l])
    for i, part in enumerate(parts):
        suffix = f" ({i + 1}/{len(parts)})" if len(parts) > 1 else ""
        c = {"id": f"{source}:{cid}:{i}", "source": source, "title": title + suffix, "text": "\n".join(part)}
        if names:
            c["names"] = names
        out.append(c)
    return out


def quests():
    lang = load_lang("ftbquestlocalizer")
    chapters = {}
    data = {}
    for key, value in lang.items():
        m = re.match(r"ftbquests\.chapter\.([^.]+)\.title$", key)
        if m:
            chapters[m.group(1)] = clean(value)
            continue
        m = re.match(r"ftbquests\.chapter\.([^.]+)\.quest([0-9A-F]+)\.(.+)$", key)
        if m:
            data.setdefault((m.group(1), m.group(2)), {})[m.group(3)] = clean(value)

    out = []
    for (chapter, quest), fields in data.items():
        desc = sorted((int(k[11:]), v) for k, v in fields.items() if re.fullmatch(r"description\d+", k))
        body = [v for _, v in desc]
        # 서버 설정 파일 안내 같은 운영 정보는 플레이어 도우미에 넣지 않는다.
        if any("config/" in line for line in body):
            continue
        tasks = [v for k, v in fields.items() if k.startswith("task.") and len(v) > 2]
        if not body and not fields.get("subtitle"):
            continue
        # 제목이 아이템 이름에서 오는 퀘스트는 lang에 title이 없어서 첫 문장으로 대신한다.
        title = fields.get("title") or (tasks[0] if tasks else "") or ([*body, fields.get("subtitle", "")][0][:30])
        lines = [fields.get("subtitle", "")] + body
        if tasks:
            lines.append("목표: " + ", ".join(tasks))
        out += chunk("quest", f"{chapter}.{quest}",
                     f"퀘스트 [{chapters.get(chapter, chapter)}] {title}", lines)
    return out


SEASONS = ["봄", "여름", "가을", "겨울"]


def season_lists(book_name, category, by_season):
    """'겨울 작물 뭐 있어?' 같은 목록 질문용으로 계절별 이름 목록을 따로 만든다."""
    out = []
    for season in SEASONS:
        names = by_season.get(season)
        if names:
            out += chunk("season_list", f"{book_name}.{category}.{season}",
                         f"{season}에 나오는 {category} 목록 ({book_name})",
                         [f"{season}: " + ", ".join(names)])
    return out


# 농업연감 가축 항목에서 '무엇을 주는지' 적힌 줄 -> 획득처 역색인(acquisition)에 쓴다.
PRODUCE_LABELS = {"드롭 아이템": "잡으면 드롭", "가위질 드롭": "가위질", "착유": "젖 짜기", "산란": "알을 낳음", "채집": "채집"}
producers = {}  # 생산품 한국어 이름 -> ['소 (젖 짜기)', ...]


def collect_producers(name, text):
    for m in re.finditer(r"^- ([^:\n]+): (.+)$", text, re.M):
        how = PRODUCE_LABELS.get(m.group(1).strip())
        if how:
            for product in re.split(r",\s*", m.group(2)):
                product = product.strip()
                if product and len(product) <= 20:
                    producers.setdefault(product, []).append(f"가축 '{name}'에게서 얻음 ({how})")


def patchouli():
    out = []
    producers.clear()
    for book, book_name in [("almanac", "농업연감"), ("fish_finder", "물고기 도감")]:
        base = config.SERVER_DIR / "patchouli_books" / book / "ko_kr"
        categories = {}
        for f in (base / "categories").glob("*.json"):
            d = json.loads(f.read_text(encoding="utf-8"))
            categories["patchouli:" + f.stem] = clean(d.get("name", f.stem))
        by_season = {}
        for f in sorted((base / "entries").rglob("*.json")):
            d = json.loads(f.read_text(encoding="utf-8"))
            lines = []
            for page in d.get("pages", []):
                for field in ("title", "name", "text"):
                    if page.get(field):
                        lines.append(clean(page[field]))
            category = categories.get(d.get("category"), "")
            name = clean(d.get("name", f.stem))
            title = f"{book_name} [{category}] {name}"
            icon = d.get("icon", "").replace(":", ".")
            text = "\n".join(lines)
            m = re.search(r"^- ((?:(?:봄|여름|가을|겨울) ?)+)$", text, re.M)
            if m:
                seasons = m.group(1).split()
                for season in seasons:
                    by_season.setdefault(category, {}).setdefault(season, []).append(name)
                # '- 여름 가을'만으로는 작은 모델이 계절인 줄 모르고 하나만 고르기도 해서 풀어 쓴다.
                # '계절'이라고 쓰면 계절 질문에 작물 카드 수백 개가 걸리므로 '제철'로 쓴다.
                lines = (text[:m.start()] + f"- 제철: {', '.join(seasons)}" + text[m.end():]).splitlines()
            if category == "가축":
                collect_producers(name, text)
            # 슬라임은 '뼈', '모래', '달콤'처럼 아이템·형용사와 같은 이름이라 '뼈 슬라임'으로 등록한다.
            # 아이콘이 생산품(뼈 등)이라 아이콘의 영어 이름도 슬라임에는 붙이지 않는다.
            names = [f"{name} 슬라임"] if category == "슬라임" else aliases(name, d.get("icon"))
            out += chunk(book, str(f.relative_to(base / "entries").with_suffix("")), title, lines, names=names)
        for category, seasons in by_season.items():
            out += season_lists(book_name, category, seasons)
    return out


SOCIETY_GROUP_TITLES = {
    "society.husbandry": "가축 기르기(축산)",
    "society.skull_cavern": "해골 광산",
    "society.shipping_bin": "배송 상자(판매)",
    "society.pig_race": "돼지 경주",
    "society.prize_machine": "경품 머신",
    "society.hospital_receipt": "병원비와 빚",
    "society.fish_radar": "물고기 레이더",
    "society.advancement": "발전 과제",
    "society.endless_entrana": "끝없는 엔트라나",
    "society.item": "아이템 특성",
    "info.society": "안내",
}
SOCIETY_SKIP = ("jei.", "tooltip.", "command.", "society.trofers", "entity.", "effect.", "fluid.",
                "biome.", "tag.", "jade.", "item.", "block.")


# 아이템·블록 설명으로 쓰는 lang 키 접미사 (예: block.numismatics.bank_terminal.tooltip.summary)
ITEM_DESC_RE = re.compile(r"((item|block)\.[^.]+\.[^.]+)\.(description(\.warn)?|desc|tooltip(\.summary|\.condition\d+|\.behaviour\d+)?|flavor_text|info)$")
# 가구 모드는 색·재질별 변형이 수백 개라 검색만 어지럽힌다.
ITEM_DESC_SKIP_NS = {"fantasyfurniture", "everycomp"}


def mod_items():
    """모든 모드의 아이템·블록 중 설명이 있는 것을 이름(한국어+영어)과 묶는다."""
    out = []
    for ns, lang in all_ko_langs().items():
        if ns in ITEM_DESC_SKIP_NS:
            continue
        descs = {}
        for key, value in lang.items():
            m = ITEM_DESC_RE.match(key)
            if m:
                descs.setdefault(m.group(1), []).append(clean(value))
        for base, lines in descs.items():
            name, item_id = clean(lang.get(base, "")), lang_key_id(base)
            if not name:
                # 이름 키가 없는 설명: 같은 ID의 사전 이름(영어만 있는 모드), 그래도 없으면 다른 모드의 같은 이름 ID.
                # 이 팩은 다른 모드 아이템의 설명을 item.society.<이름>.description 으로 단다(collar_tag 등, 11/04).
                entry = gamedata.names().get(item_id) or {}
                if not entry.get("ko") and not entry.get("en"):
                    path = base.split(".")[-1]
                    item_id = next((i for i, e in gamedata.names().items()
                                    if i.split(":")[-1] == path and (e.get("ko") or e.get("en"))), item_id)
                    entry = gamedata.names().get(item_id) or {}
                name = clean(entry.get("ko") or entry.get("en") or "")
            if not name:
                continue
            # 다른 키(block.society.ancient_goddess_statue.spring 등)에 붙은 설명도 함께 넣는다.
            lines += [clean(v) for k, v in lang.items()
                      if k.startswith(base + ".") and not ITEM_DESC_RE.match(k) and len(clean(v)) >= 6]
            lines = [l for l in dict.fromkeys(lines) if l and l != name]
            if not lines:
                continue
            kind = "블록" if base.startswith("block.") else "아이템"
            out += chunk("item", base, f"{kind}: {name}", lines, names=aliases(name, item_id))
    return out


NPC_ROLES = {
    "banker": "은행원", "blacksmith": "대장장이", "carpenter": "목수", "fisher": "어부", "librarian": "사서",
    "market": "마트 주인", "shepherd": "양치기", "trader": "상인", "witch": "마녀", "goddess": "여신",
    # 역할을 알 수 없는 NPC
    "gnome": None, "wise_oak": None, "blueprints": None,
}


def mostly_hangul(text):
    """노움 대사처럼 마법부여 문자로 쓴 줄은 뺀다."""
    letters = [ch for ch in text if ch.isalpha()]
    return letters and sum("가" <= ch <= "힣" for ch in letters) / len(letters) >= 0.5


# 마법부여대 문자(Standard Galactic Alphabet)로 쓴 대사(노움 등)를 영어로 푼다. 그대로 두면 한국어가 아니라고 걸러진다.
SGA = {"ᔑ": "a", "ʖ": "b", "ᓵ": "c", "↸": "d", "ᒷ": "e", "⎓": "f", "⊣": "g", "⍑": "h", "╎": "i", "⋮": "j", "ꖌ": "k",
       "ꖎ": "l", "ᒲ": "m", "リ": "n", "𝙹": "o", "!¡": "p", "ᑑ": "q", "∷": "r", "ᓭ": "s", "ℸ": "t", "⚍": "u", "⍊": "v",
       "∴": "w", " ̇/": "x", "||": "y", "ǁ": "y", "⨅": "z"}
SGA_RE = re.compile("|".join(re.escape(k) for k in sorted(SGA, key=len, reverse=True)))


def decode_sga(text):
    """마법부여대 문자가 섞인 문장 -> 영어. 섞여 있지 않으면 그대로."""
    return SGA_RE.sub(lambda m: SGA[m.group()], text)


def npcs():
    """마을 NPC 이름·역할과 대사 일부. '노움이 뭐야?' 같은 질문이 장식 블록 노움으로만 가지 않게 한다."""
    lang = load_lang("dialog")
    out = []
    for npc, role in NPC_ROLES.items():
        name = clean(lang.get(f"dialog.npc.{npc}.name", ""))
        if not name:
            continue
        role_text = f" 역할: {role}." if role else ""
        lines = [f"{name}은(는) 선릿 밸리 마을에 사는 NPC(주민)입니다.{role_text} 우클릭으로 대화할 수 있습니다."]
        said = [clean(v) for k, v in lang.items() if k.startswith(f"dialog.npc.{npc}.") and ".line_" in k]
        lines += [f"대사: {s}" for s in said if mostly_hangul(s)][:6]
        # clean()이 일부 기호(⚍ 등)를 지우므로 원문에서 먼저 푼다.
        coded = [clean(decode_sga(v)) for k, v in lang.items()
                 if k.startswith(f"dialog.npc.{npc}.") and ".line_" in k and SGA_RE.search(v)]
        if coded:
            lines.append("대사(마법부여대 문자로 말함, 영어로 풀면): " + " / ".join(coded[:4]))
        # 다른 NPC가 이 NPC를 언급한 대사
        lines += [f"다른 주민의 말: {clean(v)}" for k, v in lang.items()
                  if ".line_" in k and not k.startswith(f"dialog.npc.{npc}.") and name in v][:3]
        title = f"마을 NPC: {name} ({role})" if role else f"마을 NPC: {name}"
        out += chunk("npc", npc, title, lines, names=[n for n in (name, role) if n])
    return out


def coins():
    """동전 가치표. 값은 KubeJS의 global.calculateCoinValue 에서 읽는다."""
    script = config.SERVER_DIR / "kubejs" / "server_scripts" / "globalServer.js"
    if not script.exists():
        return []
    src = script.read_text(encoding="utf-8")
    body = src[src.find("global.calculateCoinValue"):]
    values = re.findall(r'case "([a-z_]+)":\s*value = (\d+)', body[:body.find("default:")])
    if not values:
        return []
    names = {}
    for lang in all_ko_langs().values():
        for key, value in lang.items():
            m = re.match(r"item\.(numismatics|society)\.([a-z_]+)$", key)
            if m:
                names.setdefault(m.group(2), clean(value))
    lines = ["동전(코인) 종류와 가치. 값은 가장 작은 단위(스퍼) 기준이며 위로 갈수록 비쌉니다."]
    coin_names = []
    for coin_id, value in values:
        name = names.get(coin_id, coin_id)
        coin_names.append(name)
        lines.append(f"- {name}: {int(value):,}")
    return chunk("coins", "values", "동전 종류와 가치", lines, names=["동전", "코인"] + coin_names)


def society():
    lang = load_lang("society")
    out = []
    groups = {}
    for key, value in lang.items():
        if key.startswith(SOCIETY_SKIP):
            continue
        group = ".".join(key.split(".")[:2])
        text = clean(value)
        if len(text) >= 6:
            groups.setdefault(group, []).append(text)
    for group, lines in groups.items():
        title = SOCIETY_GROUP_TITLES.get(group, group.split(".")[-1].replace("_", " "))
        out += chunk("society_msg", group, f"게임 메시지: {title}", lines)
    return out


PONDER_RE = re.compile(r"^([a-z0-9_]+)\.ponder\.([a-z0-9_]+)\.(header|text_(\d+))$")


def tree_places(biomes):
    """생물군계 이름들 -> 한 줄. 너무 많으면(참나무는 바다 생물군계까지 수십 곳) 줄인다."""
    if len(biomes) > 20:
        return f"지상 여러 곳(생물군계 {len(biomes)}곳, 예: {', '.join(biomes[:6])})"
    return ", ".join(biomes) + " 생물군계"


def trees():
    """'나무(원목)별 자라는 생물군계' 요약 카드 (worldgen). '소나무 어느 생물군계에서 구해?'(11/11)"""
    rows = [(clean(gamedata.display_name(log)), tree_places(b)) for log, b in gamedata.tree_biomes().items()]
    if not rows:
        return []
    lines = ["나무(원목)가 자연히 자라는 생물군계(바이옴). 이 서버의 worldgen 자료에서 뽑았다. 묘목을 사서 심어도 된다.",
             "생물군계 위치는 자연의 나침반(생물군계를 골라 찾는 나침반)으로 찾을 수 있다."]
    lines += [f"- {name}: {where}" for name, where in sorted(rows)]
    return chunk("tip", "trees", "나무별 자라는 생물군계 (어디서 찾나, 바이옴 위치)", lines)


def ponders():
    """Create 계열 모드의 ponder(게임 안에서 W키로 보는 사용 설명) 한국어 문장 -> 장면마다 카드 '사용법: 기계 팔'.
    ('기계팔이 작동을 안하는 이유', 11/10 — 설명 841개가 lang에 있는데 자료에 없었다)"""
    ko, en = gamedata.langs()
    names = gamedata.names()
    scenes = {}
    for key in set(ko) | set(en):
        m = PONDER_RE.match(key)
        if m:
            text = clean(ko.get(key) or en.get(key) or "")
            if text:
                scene = scenes.setdefault((m.group(1), m.group(2)), {"header": "", "lines": []})
                if m.group(3) == "header":
                    scene["header"] = text
                else:
                    scene["lines"].append((int(m.group(4)), text))
    out = []
    for (ns, sid), scene in sorted(scenes.items()):
        if not scene["lines"]:
            continue
        # 장면 이름이 블록 ID와 같으면('mechanical_arm') 그 블록 이름으로 찾게 한다
        item = next((i for i in (f"{ns}:{sid}", f"create:{sid}") if i in names), None)
        item_name = clean(gamedata.display_name(item)) if item else ""
        title = item_name or scene["header"] or sid.replace("_", " ")
        lines = [f"사용법({scene['header'] or title})"] + [t for _, t in sorted(scene["lines"])]
        out += chunk("ponder", f"{ns}.{sid}", f"사용법: {title}", lines,
                     names=aliases(item_name, item) if item_name else None)
    return out


def item_hints():
    """lang의 아이템 설명 중 카드에 없던 것: Relics 능력 설명, JEI 안내(Quark 등) -> {아이템 ID: [줄]}.
    아이템으로 이을 수 없는 JEI 안내는 따로 카드로 만든다(이름 없음)."""
    ko, en = gamedata.langs()
    names = gamedata.names()
    text = lambda k: clean(ko.get(k) or en.get(k) or "")
    by_item, loose = {}, []
    for key in sorted(set(ko) | set(en)):
        m = re.match(r"^tooltip\.relics\.([a-z0-9_]+)\.ability\.([a-z0-9_]+)\.description$", key)
        if m and f"relics:{m.group(1)}" in names and text(key):
            ability = text(f"tooltip.relics.{m.group(1)}.ability.{m.group(2)}") or m.group(2)
            by_item.setdefault(f"relics:{m.group(1)}", []).append(f"능력 '{ability}': {text(key)}")
            continue
        m = re.match(r"^([a-z0-9_]+)\.jei\.(?:hint|info|description)\.(.+)$", key)
        if m and text(key):
            parts = m.group(2).split(".")
            item = next((i for i in ([f"{parts[0]}:{parts[1]}"] if len(parts) == 2 else []) + [f"{m.group(1)}:{parts[-1]}"]
                         if i in names), None)
            if item:
                by_item.setdefault(item, []).append(f"설명: {text(key)}")
            elif parts[-1] != "preamble":
                loose.append((m.group(1), parts[-1], text(key)))
    # 아이템이 한두 개뿐인 모드는 그 모드의 화면 문구(string.<모드>.*, gui.<모드>.*)를 붙인다. 설명 문장이 없는 도구 모드
    # (자연의 나침반: '생물군계 선택', '검색 시작', '거리', '순간이동')도 무엇을 하는지 짐작할 근거가 된다(11/10 '바이옴 찾는 법').
    per_ns = {}
    for item_id, entry in names.items():
        if entry.get("kind") in ("item", "block"):
            per_ns.setdefault(item_id.split(":")[0], []).append(item_id)
    for ns, items in per_ns.items():
        if len(items) > 2:
            continue
        ui = [text(k) for k in sorted(set(ko) | set(en)) if re.match(rf"^(string|gui|screen)\.{re.escape(ns)}\.", k) and text(k)]
        ui = list(dict.fromkeys(t for t in ui if len(t) <= 20))
        if len(ui) >= 5:
            for item in items:
                by_item.setdefault(item, []).append("사용 화면에 나오는 말: " + ", ".join(ui[:40]))
    return by_item, loose


def hint_cards():
    _, loose = item_hints()
    out = []
    for ns, hid, body in loose:
        out += chunk("tip", f"jei.{ns}.{hid}", f"도움말({ns}): {hid.replace('_', ' ')}", [body])
    return out


def enchantments():
    """마법 부여마다 카드: 이름(한국어·영어), 설명, 어느 모드 것인지. lang의 enchantment.<모드>.<이름>(.desc).
    ('무적 프레임이랑 빠른 크랭크 설명해줘', 11/08 — 이름·설명이 lang에 있는데 자료에 없었다)"""
    ko, en = gamedata.langs()
    out = []
    for key in sorted(set(ko) | set(en)):
        parts = key.split(".")
        if len(parts) != 3 or parts[0] != "enchantment":
            continue
        name_ko, name_en = clean(ko.get(key, "")), clean(en.get(key, ""))
        if not (name_ko or name_en) or "%" in name_ko + name_en:
            continue
        desc = clean(ko.get(key + ".desc") or en.get(key + ".desc") or ko.get(key + ".description") or en.get(key + ".description") or "")
        mod = parts[1]
        name = name_ko or name_en
        lines = [f"마법 부여 '{name}'" + (f" ({name_en})" if name_en and name_en != name else "")
                 + (" — 바닐라 마인크래프트" if mod == "minecraft" else f" — '{mod}' 모드")]
        lines.append(f"효과: {desc}" if desc else "효과 설명은 게임 자료에 없다.")
        out += chunk("enchant", f"{mod}.{parts[2]}", f"마법 부여: {name}", lines,
                     names=[n for n in dict.fromkeys([name_ko, name_en]) if n])
    return out


def ores():
    """'광석 생성 높이 (이 서버)' 요약 카드: 광석마다 가장 많이 나오는 범위 한 줄. ('금좌표', '다이아 어디서 캐')"""
    rows = {}
    for item_id, lines in gamedata.ore_generation().items():
        if "deepslate_" in item_id:
            continue  # 심층암 광석은 같은 줄이다
        name = clean(gamedata.display_name(item_id))
        rows[name] = "; ".join(lines[:3])
    if not rows:
        return []
    lines = ["광석이 자연 생성되는 높이(Y 좌표, F3 화면의 XYZ 중 Y). 이 서버의 worldgen 자료에서 뽑았다. 심층암 광석도 같은 범위다."]
    lines += [f"- {name}: {text}" for name, text in sorted(rows.items())]
    return chunk("tip", "ores", "광석 생성 높이 (이 서버, 좌표·Y 높이·어디서 캐는지)", lines)


def machines():
    """장인 설비마다 '이 설비로 만드는 것' 카드 (수액 채취기: 원목 종류별 생산물과 기간 등).
    설비 이름으로 물으면 확정 정보로 붙는다('나무 종류별 수액 채취기 생산물 비교해줘', 11/06)."""
    by_machine = {}
    for _, _, line, machine_id in gamedata.machine_recipes():
        lines = by_machine.setdefault(machine_id, [])
        text = line.split("에서: ", 1)[-1]
        if text not in lines:
            lines.append(text)
    out = []
    for machine_id, lines in by_machine.items():
        name = clean(gamedata.display_name(machine_id))
        out += chunk("machine", machine_id, f"설비 레시피: {name}", [f"{name}로 만드는 것(재료 → 생산물, 걸리는 날):"] + lines,
                     names=aliases(name, machine_id))
    return out


SELL_BONUS_RE = re.compile(r"^(.+?):\s*(.+?생산품)의 가치\s*\+(\d+)%")


def selling():
    """'판매 가치 올리기' 카드 하나: 스킬 트리의 '○○ 생산품의 가치 +N%'와 품질 배수를 모은다.
    ('배송상자 판매 비율 높이는 방법' 질문이 '가치'로 쓰인 스킬 자료에 닿지 않았다, 11/07)"""
    bonus = {}
    for c in skills():
        for line in c["text"].split("\n"):
            m = SELL_BONUS_RE.match(line.strip())
            if m:
                bonus.setdefault(m.group(2), []).append(f"{m.group(1)} +{m.group(3)}% ({c['title'].split(': ')[-1]})")
    if not bonus:
        return []
    lines = ["배송 상자 판매 가격(판매가, 판매 비율, 수익)을 올리는 방법:",
             "- 품질(별 등급): 작물·생산품에 철·금·이리듐 별이 붙으면 별 1·2·3개마다 1.25·1.5·2배로 팔린다."]
    lines += [f"- 스킬: {kind}의 가치 " + ", ".join(items) for kind, items in bonus.items()]
    lines.append("- 장인 설비(와인통, 저장용 병, 숙성용 나무통 등)로 가공하면 재료보다 비싸게 팔린다.")
    lines.append("어느 아이템이 어느 생산품인지는 아이템 정보의 '판매:' 줄에 적혀 있다.")
    return chunk("tip", "selling", "판매 가치 올리기 (배송 상자 판매가 높이기)", lines)


XP_SOURCE_KO = {"kill_entity": "몹 처치", "eat_food": "음식 먹기", "craft_item": "제작", "increase_stat": "통계(행동 횟수)",
                "mine_block": "블록 캐기", "fish_item": "낚시로 건지기"}
XP_STAT_KO = {"minecraft.custom:minecraft.animals_bred": "동물 번식", "minecraft.custom:minecraft.open_chest": "상자 열기",
              "minecraft.custom:lootr.looted_stat": "전리품 상자(구조물 상자) 처음 열기"}
# KubeJS 스크립트의 추가 경험치: 파일 이름 -> 무엇을 할 때인지
XP_SCRIPT_KO = {"animalBreeding": "가축 번식", "animalBase": "가축 돌보기(쓰다듬기·먹이·채집 등)", "havestHandling": "작물 수확",
                "pamsTreeLoot": "과일나무 수확", "experienceBooks": "경험치 책 사용", "cornucopia": "풍요의 뿔 사용",
                "globalBlockEntityHandlers": "장인 설비에서 생산품 꺼내기", "globalAnimalHandlers": "가축 관련 행동",
                "globalFish": "물고기 연못에서 어란 수확", "espressoMachine": "에스프레소 머신", "prizeMachine": "경품 머신 보상",
                "chargingRod": "피뢰침에서 배터리 꺼내기", "regretCrystals": "후회의 수정(경험치 되돌리기)"}


def skill_xp():
    """스킬마다 '경험치 얻는 법' 카드: puffish_skills 설정(experience.json)과 KubeJS 스크립트의 giveExperience.
    ('탐험 경험치는 어떻게 빠르게 얻어?', 11/11 — 자료에 없었다)"""
    base = config.SERVER_DIR / "kubejs" / "data"
    lang = load_lang("society_skills")
    out = []
    script_xp = {}
    for path in (config.SERVER_DIR / "kubejs").rglob("*.js"):
        if "server_scripts" not in str(path) and "startup_scripts" not in str(path):
            continue
        for m in re.finditer(r'giveExperience\(\s*server\s*,\s*player\s*,\s*"([a-z_]+)"\s*,\s*([^,)]+(?:\([^)]*\))?[^,)]*)',
                             path.read_text(encoding="utf-8", errors="replace")):
            what = XP_SCRIPT_KO.get(path.stem, path.stem)
            script_xp.setdefault(m.group(1), []).append(f"{what}: {m.group(2).strip()}")
    for exp in sorted(base.glob("*/puffish_skills/categories/*/experience.json")):
        cat = exp.parent.name
        data = gamedata._load_json(exp.read_text(encoding="utf-8")) or {}
        title = clean(lang.get(f"society_skills.{cat}.category.title", cat))
        lines = [f"'{title}' 스킬 경험치를 얻는 방법:"]
        for src in data.get("sources") or []:
            kind = XP_SOURCE_KO.get(src.get("type", "").split(":")[-1], src.get("type", ""))
            d = src.get("data") or {}
            names = {}
            for var, spec in (d.get("variables") or {}).items():
                for op in spec.get("operations") or []:
                    test = op.get("data") or {}
                    target = test.get("item") or test.get("block") or test.get("entity_type") or test.get("stat")
                    if target:
                        target = target if ":" in target or target.startswith("#") else f"minecraft:{target}"
                        names[var] = XP_STAT_KO.get(target) or (gamedata._tag_name(target[1:]) if target.startswith("#")
                                                                else clean(gamedata.display_name(target)))
                    if "nbt" in test and "silk_touch" in str(test["nbt"]):
                        names[var] = "섬세한 손길 도구"
            for rule in d.get("experience") or []:
                cond = rule.get("condition", "")
                for var in sorted(names, key=len, reverse=True):
                    cond = re.sub(rf"!\s*{re.escape(var)}\b", f"{names[var]} 아님", cond)
                    cond = re.sub(rf"\b{re.escape(var)}\b", names[var], cond)
                cond = cond.replace("&", ", 그리고 ").replace("|", " 또는 ")
                expr = rule.get("expression", "").replace("max_health", "몹 최대 체력")
                lines.append(f"- {kind}: {re.sub(r'\s+', ' ', cond).strip() or '모두'} → 경험치 {expr}")
            if (d.get("anti_farming") or {}).get("limit_per_chunk"):
                lines.append(f"  (같은 청크에서 {d['anti_farming']['limit_per_chunk']}번까지만, 반복 사냥 제한)")
        for line in script_xp.get(cat, []):
            lines.append(f"- {line} (경험치)")
        levels = ((data.get("experience_per_level") or {}).get("data") or {}).get("values")
        if levels:
            lines.append("레벨마다 필요한 경험치: " + ", ".join(f"{v:,}" for v in levels))
        out += chunk("skill", f"xp.{cat}", f"스킬 경험치 얻는 법: {title}", lines)
    return out


def skill_items():
    """스킬 트리를 스킬 하나씩 카드로('목축업자: 치즈 착제기 생산량 두 배'). 트리 전체 카드는 검색에 잘 안 걸렸다(11/11 '치즈나 우유 스킬')."""
    out = []
    for c in skills():
        tree = c["title"].split(": ", 1)[-1].split(" (")[0]
        for n, line in enumerate(c["text"].split("\n")):
            name = line.split(":")[0].strip()
            if ":" in line and name:
                out += chunk("skill", f"{c['id']}.{n}", f"스킬: {name} ({tree})", [f"{tree} 스킬 트리의 '{name}'", line])
    return out


def skills():
    lang = load_lang("society_skills")
    out = []
    cats = {}
    for key in lang:
        parts = key.split(".")
        if len(parts) == 3:
            cats.setdefault(parts[1], []).append(key)
    for cat, keys in cats.items():
        cat_title = clean(lang.get(f"society_skills.{cat}.category.title", cat))
        lines = []
        for key in keys:
            desc = lang.get(key + ".description")
            lines.append(f"{clean(lang[key])}: {clean(desc)}" if desc else clean(lang[key]))
        out += chunk("skill", cat, f"스킬 트리: {cat_title}", lines)
    return out


def tips():
    lang = load_lang("society_tips")
    lines = [clean(v) for k, v in lang.items() if ".tip." in k]
    return chunk("tip", "tips", "선릿 밸리 팁", lines)


def server_facts():
    """서버 설정에서 플레이어가 알아도 되는 게임 규칙만 뽑는다."""
    def toml_value(path, key):
        if not path.exists():
            return None
        m = re.search(rf"^\s*{key}\s*=\s*(\S+)", path.read_text(encoding="utf-8"), re.M)
        return m.group(1).strip('"') if m else None

    ss = config.SERVER_DIR / "config" / "sereneseasons"
    lines = []
    sub = toml_value(ss / "seasons.toml", "sub_season_duration")
    if sub:
        sub = int(sub)
        lines.append(f"이 서버의 계절은 봄, 여름, 가을, 겨울이 있고 각 계절은 초(이른)·중·늦 3단계로 나뉩니다. "
                     f"단계마다 {sub}일, 계절 하나는 {sub * 3}일, 1년은 {sub * 12}일입니다.")
    if toml_value(ss / "seasons.toml", "progress_season_while_offline") == "false":
        lines.append("접속한 플레이어가 없을 때는 계절이 진행되지 않습니다.")
    behavior = toml_value(ss / "fertility.toml", "out_of_season_crop_behavior")
    behavior_text = {"0": "천천히 자랍니다", "1": "자라지 않습니다(성장이 멈춥니다)",
                     "2": "자라려고 할 때 부서집니다"}.get(behavior)
    if behavior_text:
        lines.append(f"제철이 아닌 작물은 {behavior_text}. 작물이 어느 계절에 자라는지는 작물 툴팁과 농업연감에서 확인할 수 있습니다.")
    level = toml_value(ss / "fertility.toml", "underground_fertility_level")
    if level is not None and int(level) <= -64:
        lines.append("지하에 심어도 계절 제한은 그대로 적용됩니다. 계절과 상관없이 기르려면 온실 유리(아래 16블록까지 적용)나 정원용 화분을 쓰세요.")
    dims = toml_value(ss / "seasons.toml", "whitelisted_dimensions")
    if dims and "overworld" in dims:
        lines.append("계절은 오버월드에만 적용됩니다.")
    return chunk("server", "seasons", "이 서버의 계절 규칙", lines) + gamerule_facts()


# 플레이어가 묻는 게임룰 -> (켜졌을 때 문장, 꺼졌을 때 문장). 'None'이면 그 값일 때는 말하지 않는다.
GAMERULE_TEXT = {
    "keepInventory": ("죽어도 인벤토리 아이템을 잃지 않습니다(keepInventory 켜짐).", "죽으면 인벤토리 아이템을 떨어뜨립니다."),
    "doFireTick": ("불은 바닐라처럼 주변 나무·양털 등 타는 블록으로 옮겨붙고 번집니다(불 번짐 켜짐).",
                   "불이 주변으로 옮겨붙거나 번지지 않습니다(불 번짐 꺼짐)."),
    "mobGriefing": ("몹이 블록을 부수거나 바꿀 수 있습니다(크리퍼 폭발 등, mobGriefing 켜짐).",
                    "몹이 블록을 부수지 못합니다(크리퍼 폭발에도 지형이 안 부서짐)."),
    "doInsomnia": ("오래 잠을 안 자면 팬텀이 나옵니다.", "잠을 안 자도 팬텀이 나오지 않습니다."),
    "doPatrolSpawning": (None, "약탈자 순찰대가 나오지 않습니다."),
    "disableRaids": ("습격(레이드)이 일어나지 않습니다.", None),
    "doTraderSpawning": (None, "떠돌이 상인이 나오지 않습니다."),
    "fallDamage": (None, "낙하 피해가 없습니다."),
    "fireDamage": (None, "불 피해가 없습니다."),
    "drowningDamage": (None, "물에 빠져도 피해가 없습니다."),
    "freezeDamage": (None, "얼어도 피해가 없습니다."),
    "naturalRegeneration": (None, "배가 불러도 체력이 저절로 회복되지 않습니다."),
    "doDaylightCycle": (None, "낮과 밤이 바뀌지 않습니다."),
    "doWeatherCycle": (None, "날씨가 바뀌지 않습니다."),
    "doSeasonCycle": (None, "계절이 바뀌지 않습니다."),
    "lavaSourceConversion": ("용암도 물처럼 무한 수원을 만들 수 있습니다.", None),
    "waterSourceConversion": (None, "물 무한 수원을 만들 수 없습니다."),
}


def gamerule_facts():
    """서버 월드의 게임룰 중 플레이어가 궁금해할 것('불이 번지나', '죽으면 템 잃나')을 문장으로."""
    rules = gamedata.gamerules()
    lines = []
    for rule, (on, off) in GAMERULE_TEXT.items():
        text = {"true": on, "false": off}.get(rules.get(rule))
        if text:
            lines.append(text)
    sleeping = rules.get("playersSleepingPercentage")
    if sleeping:
        # '시간 빠르게 지나게 하는법'(11/08)이 이 문장에 닿게 시간 넘기기라는 말도 쓴다.
        lines.append(f"접속한 플레이어의 {sleeping}%가 침대에서 자면 밤이 넘어갑니다(밤 건너뛰기, 시간을 빠르게 넘기는 방법).")
    return chunk("server", "gamerules", "이 서버의 게임 규칙 (게임룰)", lines)


def _ask_any(messages):
    """빌드 중 한 번 쓰는 LLM 호출: 기본 폴백 순서대로 되는 서비스에 묻는다."""
    for name in config.DEFAULT_CHAIN:
        try:
            return llm.chat(messages, config.PROVIDERS[name], max_tokens=4000)
        except (llm.LLMError, KeyError):
            continue
    return ""


def proper_names_ko():
    """영어 고유 이름(소문자) -> 한국어 이름. 아이템·블록·몹 이름, NPC 이름, 두 단어 이상의 lang 문구만.
    'Create'(-> 생성), 'Crops'(-> 작물) 같은 한 단어 일반어는 빼서 위키 본문에 엉뚱한 한국어가 붙지 않게 한다."""
    out = {}
    for item_id, entry in gamedata.names().items():
        if entry.get("ko"):
            for en in (entry.get("en"), gamedata.id_alias(item_id)):
                if en and len(en) >= 4:
                    out.setdefault(en.strip().lower(), clean(entry["ko"]))
    ko, en = gamedata.langs()
    for key, en_text in en.items():
        if not isinstance(en_text, str) or not isinstance(ko.get(key), str) or not ko[key].strip():
            continue
        if (key.startswith("dialog.npc.") and key.endswith(".name")) or len(en_text.split()) >= 2:
            out.setdefault(en_text.strip().lower(), clean(ko[key]))
    return {k: v for k, v in out.items() if not v.isascii()}


WORD_RE = re.compile(r"[A-Za-z][A-Za-z'’-]*")


def annotate_ko(text, proper, done):
    """위키 본문의 영어 이름 뒤에 한국어 이름을 붙인다: 'Fish Smoker' -> 'Fish Smoker(생선 훈제기)'.
    대문자로 시작하는 1~4단어 묶음을 긴 것부터 사전에서 찾고, 문서마다 처음 나올 때 한 번만 붙인다(done)."""
    words = list(WORD_RE.finditer(text))
    out, last, i = [], 0, 0
    while i < len(words):
        for n in range(min(4, len(words) - i), 0, -1):
            start, end = words[i].start(), words[i + n - 1].end()
            phrase = re.sub(r"\s+", " ", text[start:end])
            if phrase[0].isupper() and phrase.lower() in proper:
                ko = proper[phrase.lower()]
                if phrase.lower() not in done and ko not in text:
                    done.add(phrase.lower())
                    out.append(text[last:end] + f"({ko})")
                    last = end
                i += n
                break
        else:
            i += 1
    return "".join(out) + text[last:]


def wiki_docs():
    """공식 위키(영어 원문)를 섹션 단위 조각으로. 문서 제목을 한국어 이름과 이어서 '캐롤라인'으로 물어도 걸리게 한다.
    lang 파일로 이을 수 없는 제목('Getting Started')은 처음 한 번만 LLM이 붙인 한국어 이름을 쓴다(data/wiki/title_ko.json)."""
    pages = wiki.pages()
    if not pages:
        return []
    ko = gamedata.translations()
    linked = {t: ko.get(t.lower()) or ko.get(t.lower().rstrip("s")) for t in pages}
    extra = wiki.title_aliases([t for t, k in linked.items() if not k], _ask_any)
    proper = proper_names_ko()
    out = []
    for title, text in sorted(pages.items()):
        done = set()
        names = [n for n in [linked[title], *extra.get(title, []), title] if n]
        names = list(dict.fromkeys(clean(n) for n in names))
        for i, (section, lines) in enumerate(wiki.sections(text)):
            # 제목에 한국어 이름도 넣어 검색(BM25)이 한국어 질문으로도 위키를 찾게 한다('초반' -> Getting Started).
            ko_names = [n for n in names if not n.isascii()]
            heading = f"위키 {title}" + (f" ({', '.join(ko_names)})" if ko_names else "") + (f" › {section}" if section else "")
            # 영어 이름에 한국어 이름을 붙여 서버 자료(한국어)와 같은 대상임을 알게 하고, 한국어 검색에도 걸리게 한다.
            lines = [annotate_ko(line, proper, done) for line in lines]
            out += chunk("wiki", f"{title}.{i}", heading, lines, names=names)
    return out


def manual():
    """docs/manual/*.md 를 ## 제목 단위로 나눈다."""
    out = []
    for f in sorted(config.MANUAL_DIR.glob("*.md")):
        title, lines, sections = f.stem, [], []
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.startswith("#"):
                if lines:
                    sections.append((title, lines))
                title, lines = line.lstrip("#").strip(), []
            elif line.strip() and not line.startswith("<!--"):
                lines.append(line.strip())
        if lines:
            sections.append((title, lines))
        for i, (title, lines) in enumerate(sections):
            out += chunk("manual", f"{f.stem}.{i}", title, lines)
    return out


def acquisition():
    """아이템마다 '어디서 얻나 / 얼마에 파나 / 제철'을 한 카드로. 상점, 슬라임 플로트, 배송 상자 판매가, 가축 생산품, 계절 태그에서 모은다.
    같은 한국어 이름의 아이템(예: 여러 모드의 '토마토')은 한 카드로 합친다. patchouli() 뒤에 불러야 한다."""
    facts = {}  # 한국어 이름 -> {"ids": [...], "lines": [...]}

    def add(item_id, line):
        name = clean(gamedata.names().get(item_id, {}).get("ko", ""))
        if not name or "%" in name:
            return
        entry = facts.setdefault(name, {"ids": [], "lines": []})
        if item_id not in entry["ids"]:
            entry["ids"].append(item_id)
        if line not in entry["lines"]:
            entry["lines"].append(line)

    for item_id, lines in gamedata.shop_sources().items():
        for line in lines:
            add(item_id, f"구매: {line}")
    for item_id, lines in gamedata.slime_sources().items():
        for line in lines:
            add(item_id, f"획득: {line}")
    for item_id, line in gamedata.sell_prices().items():
        add(item_id, f"판매: {line}")
    for item_id, line in gamedata.automation_io().items():
        add(item_id, f"자동화: {line}")
    for item_id, lines in item_hints()[0].items():
        for line in lines:
            add(item_id, line)
    for log, biomes in gamedata.tree_biomes().items():
        add(log, "자연히 자라는 곳: " + tree_places(biomes))
    names = gamedata.names()
    for item_id, lines in gamedata.ore_generation().items():
        for line in lines:
            add(item_id, f"자연 생성(광석 높이·몇 층): {line}")
        # 광석에서 나오는 아이템 카드에도 붙인다('다이아몬드는 몇 층에서 캐?'가 광석 카드에 닿지 않았다, 11/09)
        ns, path = item_id.split(":")
        base = re.sub(r"^(deepslate_|nether_)|_ore$", "", path)
        base = re.sub(r"^(deepslate_|nether_)", "", base)
        ore_name = clean(gamedata.display_name(item_id))
        for drop in (f"{ns}:{base}", f"{ns}:raw_{base}", f"minecraft:{base}", f"minecraft:raw_{base}"):
            if drop != item_id and drop in names and "deepslate_" not in path:
                for line in lines:
                    add(drop, f"캐는 곳: {ore_name} — {line}")
                break
    for item_id, seasons in gamedata.crop_seasons().items():
        add(item_id, f"제철(자라는 계절): {', '.join(seasons)}")
    by_ko = {}
    for item_id, entry in gamedata.names().items():
        if entry.get("kind") == "item" and entry.get("ko"):
            by_ko.setdefault(clean(entry["ko"]), item_id)
    for product, sources in producers.items():
        if product in by_ko:
            for line in sources:
                add(by_ko[product], f"획득: {line}")

    out = []
    for name, entry in sorted(facts.items()):
        lines = list(entry["lines"])
        if any(i.startswith("minecraft:") for i in entry["ids"]):
            lines.insert(0, "바닐라 마인크래프트에도 있는 아이템입니다.")
        out += chunk("acquire", entry["ids"][0], f"아이템 정보: {name}", lines, names=aliases(name, entry["ids"][0]))
    return out


def write_names():
    """질문 속 이름을 알아보기 위한 사전(data/names.jsonl). 자료 조각이 없는 아이템·몹도 여기서 알아본다."""
    with open(config.NAMES_FILE, "w", encoding="utf-8") as f:
        for item_id, entry in sorted(gamedata.names().items()):
            # 같은 ID의 몹 이름이 따로 있으면 몹 줄을 먼저 쓴다(같은 ID면 나중 줄인 아이템이 대표가 된다).
            for kind, names in (("entity", entry.get("mob") or {}), (entry["kind"], entry)):
                ko, en = clean(names.get("ko", "")), clean(names.get("en", ""))
                if "%" in ko or "%" in en or not (ko or en):
                    continue
                f.write(json.dumps({"id": item_id, "kind": kind, "ko": ko, "en": en,
                                    "alias": gamedata.id_alias(item_id)}, ensure_ascii=False) + "\n")


def write_places():
    """바이옴 ID -> 한국어 이름 (data/places.json). 플레이어 상태('지금 있는 바이옴')를 한국어로 보여 줄 때 쓴다."""
    ko, en = gamedata.langs()
    places = {}
    for key in set(ko) | set(en):
        parts = key.split(".")
        if len(parts) == 3 and parts[0] == "biome":
            name = ko.get(key) or en.get(key)
            if isinstance(name, str) and name:
                places[f"{parts[1]}:{parts[2]}"] = clean(name)
    config.PLACES_FILE.write_text(json.dumps(places, ensure_ascii=False), encoding="utf-8")
    return len(places)


MAX_RECIPES_PER_ITEM = 3
MAX_USES_PER_ITEM = 8


def write_categories():
    """묶음 이름 -> 아이템 ID들 (data/categories.json). rag.Index.category_card가 쓴다."""
    table = {w: sorted(ids) for w, ids in gamedata.categories().items()}
    config.CATEGORIES_FILE.write_text(json.dumps(table, ensure_ascii=False), encoding="utf-8")
    return len(table)


def write_recipe_facts():
    """아이템별 제작법·쓰임새(data/recipes.jsonl). 서버 레시피 덤프가 없으면 파일을 지운다
    (레시피를 모르는데 '만들 수 없다'고 답하면 안 되므로)."""
    table = gamedata.recipes()
    if table is None:
        config.RECIPE_FACTS_FILE.unlink(missing_ok=True)
        return 0
    drops = gamedata.loot_sources()
    removed = gamedata.removed_recipes()
    sell = gamedata.sell_values()
    with open(config.RECIPE_FACTS_FILE, "w", encoding="utf-8") as f:
        for item_id, entry in sorted(gamedata.names().items()):
            if entry.get("kind") not in ("item", "block"):
                continue
            facts = table.get(item_id, {"make": [], "uses": []})
            uses = list(dict.fromkeys(clean(gamedata.display_name(i)) for i in facts["uses"]))
            f.write(json.dumps({"id": item_id, "make": facts["make"][:MAX_RECIPES_PER_ITEM],
                                "uses": uses[:MAX_USES_PER_ITEM], "uses_total": len(uses),
                                # 전리품 테이블(몹·블록 드롭, 구조물 상자, 낚시). get_item 도구와 제작법 카드에 쓴다.
                                "drops": drops.get(item_id, []),
                                # 모드 기본 제작법 중 이 팩이 지운 것 ('원래 아연으로 만들지 않았나?' 질문용)
                                # (같은 레시피를 다른 ID로 다시 넣은 것은 지운 게 아니다: 화로)
                                "removed": [r for r in removed.get(item_id, []) if r not in facts["make"]][:2],
                                # 배송 상자 기본 판매가 (플레이어 인벤토리 가치 계산용)
                                "sell": sell.get(item_id)},
                               ensure_ascii=False) + "\n")
    return len(table)


def build():
    chunks, seen = [], set()
    # 위키는 맨 뒤에 둔다: 같은 이름에 서버 자료 조각이 있으면 그쪽이 먼저 확정 정보로 고정된다.
    sources = [quests, patchouli, acquisition, mod_items, machines, enchantments, ponders, hint_cards, trees, npcs, coins, society, skills,
               skill_items, skill_xp, selling, ores, tips, server_facts, manual,
               wiki_docs]
    for c in [c for source in sources for c in source()]:
        # 제목만 있는 퀘스트처럼 내용이 같은 조각은 하나만 남긴다. 이름이 다르면(수박 조각/수박씨) 따로 둔다.
        key = (c["text"], tuple(c.get("names", ())))
        if key in seen:
            continue
        seen.add(key)
        chunks.append(c)
    config.DATA_DIR.mkdir(parents=True, exist_ok=True)
    with open(config.CHUNKS_FILE, "w", encoding="utf-8") as f:
        for c in chunks:
            f.write(json.dumps(c, ensure_ascii=False) + "\n")
    write_names()
    counts = {"recipes": write_recipe_facts(), "biomes": write_places(), "categories": write_categories()}
    for c in chunks:
        counts[c["source"]] = counts.get(c["source"], 0) + 1
    return len(chunks), counts
