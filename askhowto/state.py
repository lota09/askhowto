"""KubeJS가 질문과 함께 보낸 플레이어 상태(원본 ID·숫자)를 프롬프트에 넣을 한국어 글로 바꾼다.

원본 예: {"mainhand": {"id": "society:geode", "count": 3}, "offhand": null,
          "inventory": [{"id": "minecraft:wheat", "count": 64}, ...], "weather": "rain", "day": 12, "daytime": 1000,
          "season": "summer", "biome": "minecraft:plains", "dimension": "minecraft:overworld"}
좌표는 받지 않는다.
"""
import json
import math

from . import config

SEASONS = {"spring": "봄", "summer": "여름", "autumn": "가을", "winter": "겨울"}
WEATHER = {"clear": "맑음", "rain": "비", "thunder": "뇌우"}
DIMENSIONS = {"minecraft:overworld": "오버월드", "minecraft:the_nether": "네더", "minecraft:the_end": "엔드"}
MAX_INVENTORY = 10

_places = None


def _place(biome):
    global _places
    if _places is None:
        _places = json.loads(config.PLACES_FILE.read_text(encoding="utf-8")) if config.PLACES_FILE.exists() else {}
    return _places.get(biome) or biome.split(":")[-1].replace("_", " ")


def item_name(index, item_id):
    entry = index.dict_by_id.get(item_id) or {}
    return entry.get("ko") or entry.get("en") or item_id.split(":")[-1].replace("_", " ")


def _clock(daytime):
    """마인크래프트 하루 시각(0 = 오전 6시, 24000틱) -> '오전 7시'"""
    hour = (int(daytime) // 1000 + 6) % 24
    return f"{'오전' if hour < 12 else '오후'} {hour % 12 or 12}시"


def _number(value):
    """KubeJS에서 온 숫자. NaN·무한대·숫자가 아닌 값은 None (11/03: 게임 시각이 NaN으로 와서 사이드카가 죽었다)."""
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def describe(raw, index):
    """-> {"held_name": 손에 든 아이템 한국어 이름 또는 None, "text": 여러 줄 글}. 원본이 없거나 깨졌으면 None.
    상태는 답에 꼭 필요하지 않으므로 어떤 값이 이상해도 질문 처리를 막지 않는다."""
    try:
        return _describe(raw, index)
    except Exception:  # noqa: BLE001 — 상태가 깨져도 질문에는 답한다
        return None


def _describe(raw, index):
    if not isinstance(raw, dict):
        return None
    raw = dict(raw, day=_number(raw.get("day")), daytime=_number(raw.get("daytime")))
    lines, held = [], None
    hands = []
    for key, label in (("mainhand", "주 손"), ("offhand", "보조 손")):
        stack = raw.get(key)
        if isinstance(stack, dict) and stack.get("id") and stack["id"] != "minecraft:air":
            name = item_name(index, stack["id"])
            held = held or name
            hands.append(f"{label}: {name}" + (f" {stack.get('count')}개" if (stack.get("count") or 1) > 1 else ""))
    lines.append("손에 든 것: " + (", ".join(hands) if hands else "없음"))
    inventory = [s for s in raw.get("inventory") or [] if isinstance(s, dict) and s.get("id")]
    if inventory:
        inventory.sort(key=lambda s: -(s.get("count") or 0))
        lines.append("인벤토리(많은 순): " + ", ".join(f"{item_name(index, s['id'])} {s.get('count', 1)}개"
                                                   for s in inventory[:MAX_INVENTORY]))
    value = _inventory_value(raw, index)
    when = []
    if raw.get("day") is not None:
        when.append(f"{int(raw['day'])}일 차")
    if raw.get("daytime") is not None:
        when.append(_clock(raw["daytime"]))
    if raw.get("season") in SEASONS:
        when.append(f"계절 {SEASONS[raw['season']]}")
    if raw.get("weather") in WEATHER:
        when.append(f"날씨 {WEATHER[raw['weather']]}")
    if when:
        lines.append("시간: " + ", ".join(when))
    where = []
    if raw.get("dimension"):
        where.append(DIMENSIONS.get(raw["dimension"], raw["dimension"]))
    if raw.get("biome"):
        where.append(f"바이옴 {_place(raw['biome'])}")
    if where:
        lines.append("위치: " + ", ".join(where))
    # 질문이 '지금·여기'를 물을 때 검색어에 더할 말 (bot.NOW_RE)
    words = [SEASONS[raw["season"]]] if raw.get("season") in SEASONS else []
    if raw.get("biome"):
        words.append(_place(raw["biome"]))
    return {"held_name": held, "text": "\n".join(lines), "search_words": words, "value_text": value}


def _inventory_value(raw, index):
    """인벤토리(손에 든 것 포함)의 배송 상자 기본 판매가 합계 글. 판매가를 모르면 빈 글.
    '내 인벤토리 가치 합한 값 알려줘'(11/04)에 계산해서 답하게 한다. 질문이 값·가치를 물을 때만 넣는다(bot.VALUE_RE)."""
    facts = index.recipe_facts or {}
    counts = {}
    for stack in [raw.get("mainhand"), raw.get("offhand")] + list(raw.get("inventory") or []):
        if isinstance(stack, dict) and stack.get("id"):
            counts[stack["id"]] = counts.get(stack["id"], 0) + int(stack.get("count") or 1)
    priced = [(i, n, (facts.get(i) or {}).get("sell")) for i, n in counts.items()]
    sellable = sorted(((i, n, v) for i, n, v in priced if v), key=lambda x: -x[1] * x[2])
    if not sellable:
        return ""
    total = sum(n * v for _, n, v in sellable)
    parts = [f"{item_name(index, i)} {n}개×{v:,}={n * v:,}" for i, n, v in sellable[:MAX_INVENTORY]]
    unsold = len(priced) - len(sellable)
    return (f"인벤토리 판매가(배송 상자 기본값, 품질·스킬 배수 빼고): 합계 코인 {total:,} / " + ", ".join(parts)
            + (f" / 판매가 없는(팔 수 없는) 아이템 {unsold}종" if unsold else ""))
