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
            name = clean(lang.get(base, ""))
            if not name:
                continue
            # 다른 키(block.society.ancient_goddess_statue.spring 등)에 붙은 설명도 함께 넣는다.
            lines += [clean(v) for k, v in lang.items()
                      if k.startswith(base + ".") and not ITEM_DESC_RE.match(k) and len(clean(v)) >= 6]
            lines = [l for l in dict.fromkeys(lines) if l and l != name]
            if not lines:
                continue
            kind = "블록" if base.startswith("block.") else "아이템"
            out += chunk("item", base, f"{kind}: {name}", lines, names=aliases(name, lang_key_id(base)))
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
        lines.append(f"접속한 플레이어의 {sleeping}%가 침대에서 자면 밤이 넘어갑니다.")
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
    """아이템마다 '어디서 얻나 / 제철'을 한 카드로. 상점, 슬라임 플로트, 가축 생산품, 계절 태그에서 모은다.
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
            ko, en = clean(entry.get("ko", "")), clean(entry.get("en", ""))
            if "%" in ko or "%" in en or not (ko or en):
                continue
            f.write(json.dumps({"id": item_id, "kind": entry["kind"], "ko": ko, "en": en,
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


def write_recipe_facts():
    """아이템별 제작법·쓰임새(data/recipes.jsonl). 서버 레시피 덤프가 없으면 파일을 지운다
    (레시피를 모르는데 '만들 수 없다'고 답하면 안 되므로)."""
    table = gamedata.recipes()
    if table is None:
        config.RECIPE_FACTS_FILE.unlink(missing_ok=True)
        return 0
    with open(config.RECIPE_FACTS_FILE, "w", encoding="utf-8") as f:
        for item_id, entry in sorted(gamedata.names().items()):
            if entry.get("kind") not in ("item", "block"):
                continue
            facts = table.get(item_id, {"make": [], "uses": []})
            uses = list(dict.fromkeys(clean(gamedata.display_name(i)) for i in facts["uses"]))
            f.write(json.dumps({"id": item_id, "make": facts["make"][:MAX_RECIPES_PER_ITEM],
                                "uses": uses[:MAX_USES_PER_ITEM], "uses_total": len(uses)},
                               ensure_ascii=False) + "\n")
    return len(table)


def build():
    chunks, seen = [], set()
    # 위키는 맨 뒤에 둔다: 같은 이름에 서버 자료 조각이 있으면 그쪽이 먼저 확정 정보로 고정된다.
    sources = [quests, patchouli, acquisition, mod_items, npcs, coins, society, skills, tips, server_facts, manual,
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
    counts = {"recipes": write_recipe_facts(), "biomes": write_places()}
    for c in chunks:
        counts[c["source"]] = counts.get(c["source"], 0) + 1
    return len(chunks), counts
