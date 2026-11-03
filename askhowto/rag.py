"""BM25 검색. 임베딩 API가 없고 RAM도 빠듯해서 한글은 음절 2-gram으로 색인한다
(띄어쓰기 없이 '계절이작물에'처럼 써도 '계절', '작물'이 걸린다)."""
import json
import math
import re
from collections import Counter
from functools import lru_cache

from . import config

# 질문에 흔히 붙지만 검색에는 방해만 되는 말
QUERY_STOPWORDS = {
    "어떻게", "어떻게돼", "어떻게되나요", "어떻게해", "어떻게해요", "어떻게하나요", "뭐야", "뭐에요", "뭔가요",
    "뭐임", "무엇", "무엇인가요", "알려줘", "알려주세요", "알려줄래", "있어", "있나요", "있어요", "있음", "돼",
    "되나요", "돼요", "해", "하나요", "해요", "왜", "언제", "어디", "어디서", "어디에서", "방법", "방법은",
    "하는법", "하는", "법", "좀", "그리고", "이거", "그거", "뭐", "혹시", "궁금해", "궁금합니다", "질문",
    "썬릿밸리", "선릿밸리", "썬릿", "선릿", "밸리", "마크", "마인크래프트", "모드", "모드팩", "얻어", "얻나요",
    "얻어요", "얻는", "구해", "구하나요", "구하는", "만들어", "만드나요", "만드는", "써", "쓰나요", "쓰는",
    "미치는", "미쳐", "영향", "영향이", "영향은", "관계", "차이", "차이가", "대해", "대해서", "관련",
    "지어", "짓는", "지으면", "지으려면", "설명", "설명해", "설명해줘", "정체", "정체가", "대한", "모든", "존재하는",
    "여러", "게임", "이게임", "이게임엔", "있는데", "언제야", "도대체",
}
JOSA = "은|는|이|가|을|를|의|에|에서|도|로|으로|와|과|만|이랑|랑|은요|는요"
# 수량 표현 뒤의 짧은 이름은 단위 명사로 쓰인 것이다('몇 가지', '3개', '두 마리'). 가지(작물)·개(펫)와 구분한다.
QUANTITY_BEFORE_RE = re.compile(r"(\d|몇|여러|한|두|세|네|다섯|열|많은|모든)\s*$")
# 사전에만 있는 이름이 문서에서 이만큼 넘게 쓰이면 아이템이 아니라 개념어로 본다('정보', '시간', '작물').
CONCEPT_WORD_DF = 20
MAX_PINNED_NAMES = 3
MAX_CHUNKS_PER_NAME = 3
# 1등 점수의 이 비율보다 낮은 자료는 넣지 않는다. 작은 모델은 관계없는 자료가 섞이면 쉽게 헷갈린다.
RELATIVE_CUT = 0.4
# 문맥 창이 큰 서비스(자료 4,000자 초과)는 더 낮은 점수의 자료도 넣는다. 큰 모델은 관계없는 자료를 더 잘 무시한다.
WIDE_RELATIVE_CUT = 0.2
# 한글 1글자 토큰의 가중치. 2글자 단위만으로는 '죽으면'과 '죽었을'처럼 어미만 다른 말이 안 걸려서 낮게 더한다.
UNIGRAM_WEIGHT = 0.3
# 질문에 나온 아이템 이름이 본문에 그대로 있는 조각은 점수를 이만큼 곱한다('물뿌리개 업그레이드'에서
# '업그레이드'만 걸린 다른 퀘스트보다 '물뿌리개를 강화하면…' 퀘스트가 앞서게).
NAME_BOOST = 1.5
# 질문에 이 말로 시작하는 단어가 있으면 문서 쪽에서 쓰는 말을 검색어에 더한다(바꾸지 않고 더한다).
QUERY_EXPANSIONS = {
    # 표기·어휘 차이
    "썬릿": "선릿",
    "sunlit": "선릿",
    "동물": "가축",
    "팔": "판매",
    # 질문 의도 -> 아이템 정보 카드에 쓰는 말 ('구매:', '획득:', '제철:')
    "어디서": "구매 판매 획득",
    "구해": "구매 판매 획득",
    "구하": "구매 판매 획득",
    "얻": "획득 구매",
    "파는": "구매 판매",
    "사려": "구매 판매",
    "용도": "재료 사용",
    "어따": "재료 사용",
    "어디에": "재료 사용",
    "만드": "제작 재료",
    "만들": "제작 재료",
    "제작": "제작 재료",
    "조합": "제작 재료",
    "템": "아이템 인벤토리",
    "업그레이드": "강화",
    "강화": "업그레이드",
}
# 질문 의도. 이름이 나온 아이템에 제작법·쓰임새·같은 계열 카드를 붙일지 정한다 (모든 질문에 붙이면 작은 모델이 그대로 늘어놓는다).
MAKE_INTENT_RE = re.compile(r"만들|만드|제작|조합|레시피|크래프|업그레이드|강화|구하|구해|얻|어디서|구매|사려|파는|못\s*만")
USE_INTENT_RE = re.compile(r"어따|어디에\s*써|어디다|용도|쓰는|쓰임|쓸모|활용|써\?|쓰지")
FAMILY_INTENT_RE = re.compile(r"업그레이드|강화|버전|등급|종류|단계|상위|하위|티어|더\s*좋은")
# 같은 계열: 질문 속 이름으로 끝나는 다른 이름('물뿌리개' -> '철 물뿌리개'). 짧은 이름('밀')은 엉뚱한 게 붙어서 뺀다.
FAMILY_MIN_KEY = 3
FAMILY_MAX_PREFIX = 6
MAX_FAMILY = 6
MAX_DICT_NAMES = 2
MAX_RELATED_PER_NAME = 2
KIND_KO = {"item": "아이템", "block": "블록", "entity": "생물(몹)", "effect": "효과"}

HANGUL_RE = re.compile(r"[가-힣]")
# 같은 이름의 여러 대상 구분 (senses_card): 자료 출처나 제목 머리말로 종류를 정한다.
SENSE_LABELS = {"npc": "마을 주민(NPC)"}
TITLE_SENSES = [("블록:", "블록"), ("아이템:", "아이템"), ("아이템 정보:", "아이템"), ("농업연감 [가축]", "가축"),
                ("농업연감 [펫]", "펫"), ("농업연감 [작물]", "작물"), ("농업연감 [슬라임]", "슬라임"), ("물고기 도감", "물고기")]
NPC_CONTEXT_RE = re.compile(r"마을|주민|npc|대화|선물|초대|호감|우정")
# 오타·맞춤법 차이 허용('낚시대'/'낙시대' -> 낚싯대, '돌곡굉이' -> 돌 곡괭이, '스프링쿨러' -> 스프링클러).
# 같은 글자 수의 이름과 자모 단위로 비교해 이 거리 이하면 같은 이름으로 본다. 받침 차이는 흔한 맞춤법 혼동이라 절반만 센다.
FUZZY_MAX_DISTANCE = 1.0
FUZZY_MIN_LEN = 3
FUZZY_MAX_LEN = 8


def tokenize(text, query=False):
    out = []
    for word in re.findall(r"[0-9a-z]+|[가-힣]+", text.lower()):
        # 문서와 질문 모두 조사를 뗀다('돈은'과 '돈을'이 같은 말로, '제철은'의 '철은'이 '강철은'에 걸리지 않게).
        word = re.sub(rf"(?:{JOSA})$", "", word) or word
        if query and word in QUERY_STOPWORDS:
            continue
        if HANGUL_RE.match(word) and len(word) > 1:
            out += [word[i:i + 2] for i in range(len(word) - 1)]
            out += ["1:" + ch for ch in word]
        else:
            out.append(word)
    return out


def word_stems(text):
    """문서 속 한글 단어(조사 뗀 것)들. 개념어 판별용 문서 빈도에 쓴다."""
    return {re.sub(rf"(?:{JOSA})$", "", w) or w for w in re.findall(r"[가-힣]+", text)}


@lru_cache(maxsize=4096)
def name_pattern(key):
    """띄어쓰기를 무시하고 이름을 찾는 정규식. 짧은 한글 이름('밀', '쌀')과 영어 이름은 다른 단어의 일부일 때는 빼고,
    한글 이름 뒤의 조사는 허용한다."""
    pattern = r"\s*".join(re.escape(ch) for ch in key)
    if key.isascii():
        return re.compile(rf"(?<![a-z]){pattern}(?![a-z])")
    if HANGUL_RE.match(key) and len(key) <= 2:
        return re.compile(rf"(?<![가-힣]){pattern}(?:{JOSA})?(?![가-힣])")
    return re.compile(pattern)


def jamo(ch):
    code = ord(ch) - 0xAC00
    return (code // 588, code % 588 // 28, code % 28) if 0 <= code < 11172 else (ch, None, None)


def name_distance(a, b, limit=FUZZY_MAX_DISTANCE):
    """같은 글자 수의 한글 단어 사이 거리: 초성·중성이 다르면 1, 받침만 다르면 0.5. limit을 넘으면 바로 멈춘다."""
    d = 0.0
    for x, y in zip(a, b):
        if x != y:
            (c1, v1, j1), (c2, v2, j2) = jamo(x), jamo(y)
            d += (c1 != c2) + (v1 != v2) + 0.5 * (j1 != j2)
            if d > limit:
                return d
    return d


def entity_card(entry):
    """사전에만 있는 항목을 위한 짧은 확정 정보. 바닐라면 일반 지식으로 답해도 된다고 알려 준다."""
    name = entry.get("ko") or entry.get("en") or entry["alias"]
    if entry.get("en") and entry.get("en") != name:
        name += f" ({entry['en']})"
    kind = KIND_KO.get(entry.get("kind"), "아이템")
    ns = entry["id"].split(":")[0]
    # 지시문처럼 쓰면 작은 모델이 그 문장을 그대로 답으로 옮겨서 사실만 적는다.
    if ns == "minecraft":
        desc = f"게임에 있는 바닐라 마인크래프트 {kind}(바닐라와 같음)."
    else:
        desc = f"게임에 실제로 있는 '{ns}' 모드의 {kind}."
    return f"### [확정 정보] {name}{matched_note(name, entry.get('matched'))}\n{desc}"


def matched_note(name, matched):
    """질문에서 다른 이름(영어 이름, 별칭)으로 불렸으면 그 말을 제목에 적어 둔다. 'Geode'로 물었는데 카드가 '정동석'이면
    작은 모델은 둘이 같은 것인지 모른다."""
    if not matched:
        return ""
    compact = lambda t: re.sub(r"\s+", "", t.lower())
    return "" if compact(matched) in compact(name) else f" (질문 속 '{matched}')"


class Index:
    K1, B = 1.4, 0.6

    def __init__(self, chunks, dictionary=(), recipe_facts=None):
        self.chunks = chunks
        # 아이템 ID -> 제작법·쓰임새 (data/recipes.jsonl). None이면 서버 레시피 덤프가 없어 모르는 것이다.
        self.recipe_facts = recipe_facts
        self.docs = []
        df = Counter()
        self.word_df = Counter()  # 조사 뗀 한글 단어의 문서 빈도 (개념어 판별용)
        for c in chunks:
            self.word_df.update(word_stems(c["text"]))
            # 제목에 걸리면 더 관련 있다고 보고 제목 토큰을 두 번 넣는다.
            # 퀘스트 챕터명([II - 농장 가꾸기] 등)은 수백 개 조각에 반복돼 '농사' 같은 말의 가중치만 깎으므로 뺀다.
            title = re.sub(r"\[[^\]]*\]", "", c["title"])
            tf = Counter(tokenize(title) * 2 + tokenize(c["text"]))
            self.docs.append((tf, sum(tf.values())))
            df.update(tf.keys())
        n = len(chunks)
        self.avgdl = sum(l for _, l in self.docs) / max(n, 1)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

        # 이름 사전: 정규화한 이름 -> (정규식, 조각 번호들). 긴 이름부터 맞춰서 '금 코인'이 '금'보다 먼저 잡히게 한다.
        by_name = {}
        for i, c in enumerate(chunks):
            for name in c.get("names", []):
                key = re.sub(r"\s+", "", name.lower())
                if not key or (key.isascii() and len(key) < 4):
                    continue
                # 위키 문서 제목을 옮긴 한국어 이름이 흔한 말('작물', '가을')이면 그 말이 나올 때마다
                # 위키 문서가 고정돼 버린다. 사전 이름과 같은 개념어 규칙으로 뺀다.
                if c["source"] == "wiki" and not key.isascii() and self.word_df[key] >= CONCEPT_WORD_DF:
                    continue
                by_name.setdefault(key, []).append(i)
        # 정규식은 질문에 그 이름이 실제로 들어 있을 때만 만든다(이름이 수만 개라 미리 만들면 로딩이 느리다).
        self.names = [(key, list(dict.fromkeys(idxs)))
                      for key, idxs in sorted(by_name.items(), key=lambda kv: -len(kv[0]))]
        self.by_name = dict(self.names)

        # 자료 조각은 없지만 이름은 아는 아이템·블록·몹·효과(data/names.jsonl). 바닐라를 먼저 둬서 같은 이름이면 바닐라로 본다.
        by_key = {}
        for entry in sorted(dictionary, key=lambda e: not e["id"].startswith("minecraft:")):
            for name in (entry.get("ko"), entry.get("en"), entry.get("alias")):
                key = re.sub(r"\s+", "", (name or "").lower())
                if (not key or key in by_name or key in by_key or self.word_df[key] >= CONCEPT_WORD_DF
                        or (key.isascii() and len(key) < 4) or (not key.isascii() and len(key) < 2)):
                    continue
                by_key[key] = entry
        self.dict_names = sorted(by_key.items(), key=lambda kv: -len(kv[0]))
        self.all_names = sorted([(k, v, False) for k, v in self.names] + [(k, v, True) for k, v in self.dict_names],
                                key=lambda x: -len(x[0]))
        # 오타 허용 비교용: 글자 수 -> 한글로만 된 이름들 (자료 조각 이름 먼저)
        self.fuzzy_keys = {}
        for key in list(self.by_name) + list(by_key):
            if FUZZY_MIN_LEN <= len(key) <= FUZZY_MAX_LEN and all("가" <= ch <= "힣" for ch in key):
                self.fuzzy_keys.setdefault(len(key), []).append(key)
        self.dict_by_key = by_key
        # 이름 -> 그 이름을 쓰는 모든 ID (같은 한국어 이름의 아이템이 여러 모드에 있다). 제작법 카드에 쓴다.
        self.ids_by_key, self.dict_by_id = {}, {}
        for entry in dictionary:
            self.dict_by_id[entry["id"]] = entry
            for name in (entry.get("ko"), entry.get("en"), entry.get("alias")):
                key = re.sub(r"\s+", "", (name or "").lower())
                if key and entry["id"] not in self.ids_by_key.setdefault(key, []):
                    self.ids_by_key[key].append(entry["id"])

    def match_names(self, question):
        """질문에 나온 이름 -> (확정 정보로 넣을 조각들, 조각은 없고 사전에만 있는 항목들). 질문에 나온 순서."""
        q = question.lower()
        compact = re.sub(r"\s+", "", q)
        taken, found, extras = [], [], []

        def first_free(key):
            for m in name_pattern(key).finditer(q):
                if len(key) <= 2 and QUANTITY_BEFORE_RE.search(q[:m.start()]):
                    continue
                if not any(m.start() < e and s < m.end() for s, e in taken):
                    taken.append((m.start(), m.end()))
                    return m
            return None

        keys = []  # (위치, 이름) 제작법 카드용
        # 자료 조각 이름과 사전 이름을 합쳐 긴 것부터 맞춘다('철 낚싯대'가 '낚싯대'보다 먼저).
        for key, value, is_dict in self.all_names:
            if key in compact and (m := first_free(key)):
                if is_dict:
                    extras.append((m.start(), key, dict(value, matched=m.group().strip())))
                else:
                    found.append((m.start(), value, m.group().strip()))
                keys.append((m.start(), key))
        for start, key, surface in self.fuzzy_matches(q, taken):
            if key in self.by_name:
                found.append((start, self.by_name[key], surface))
            else:
                extras.append((start, key, dict(self.dict_by_key[key], matched=surface)))
            keys.append((start, key))
        extras = sorted(extras, key=lambda x: x[0])[:MAX_DICT_NAMES]
        for start, key, entry in extras:
            # 사전에만 있는 이름('호박')이면 그 이름으로 시작하는 자료 카드('호박씨')를 대신 붙인다.
            related = [i for k, idxs in self.by_name.items() if k.startswith(key) and k != key for i in idxs]
            related.sort(key=lambda i: self.chunks[i]["source"] != "acquire")
            if related:
                found.append((start, related[:MAX_RELATED_PER_NAME], entry["matched"]))
        chunks = []
        npc_first = NPC_CONTEXT_RE.search(q)
        for _, idxs, surface in sorted(found, key=lambda x: x[0])[:MAX_PINNED_NAMES + MAX_DICT_NAMES]:
            # 같은 이름이 마을 NPC와 블록·아이템을 함께 가리키면, 질문에 '마을·주민' 같은 말이 있을 때 NPC 자료를 먼저 둔다.
            if npc_first:
                idxs = sorted(idxs, key=lambda i: self.chunks[i]["source"] != "npc")
            note = self.senses_card(surface, idxs)
            chunks += ([note] if note else []) + [dict(self.chunks[i], matched=surface) for i in idxs[:MAX_CHUNKS_PER_NAME]]
        key = self.intent_target(question, sorted(keys))
        card = self.recipe_card(key, question) if key else None
        if card:
            # 같은 아이템의 획득 카드('구매: 어부에서 …')가 이미 있으면 거기에 합친다.
            # 따로 두면 작은 모델이 '만들 수 없음'만 옮기고 어디서 사는지는 빠뜨린다.
            same = next((c for c in chunks if c["source"] == "acquire" and card["names"][0] in c.get("names", [])), None)
            if same:
                chunks[chunks.index(same)] = dict(same, text=same["text"] + "\n" + card["text"], has_recipe=True)
            else:
                chunks.append(card)
        chunks = list({c["id"]: c for c in chunks}.values())
        extras = list({e["id"]: e for _, _, e in extras}.values())
        return chunks, extras

    def senses_card(self, surface, idxs):
        """한 이름이 여러 종류(마을 NPC, 블록, 아이템, 생물 …)를 가리키면 그 사실을 알려 주는 확정 정보 조각.
        없으면 작은 모델은 첫 자료만 보고 엉뚱한 쪽으로 답한다('노움' 질문에 NPC 대신 장식 블록)."""
        senses = []
        for i in idxs:
            c = self.chunks[i]
            kind = SENSE_LABELS.get(c["source"]) or next(
                (label for prefix, label in TITLE_SENSES if c["title"].startswith(prefix)), None)
            if kind and (kind, c["title"]) not in senses and kind not in [k for k, _ in senses]:
                senses.append((kind, c["title"]))
        if len(senses) < 2:
            return None
        name = (self.chunks[idxs[0]].get("names") or [surface])[0]
        lines = [f"- {kind}: {title.split(': ', 1)[-1]}" for kind, title in senses]
        return {"id": f"senses:{name}", "source": "senses", "title": f"'{name}'은(는) 여러 대상을 가리키는 이름",
                "text": "\n".join(lines) + "\n질문 맥락(마을·주민이면 NPC, 설치·장식이면 블록 등)에 맞는 대상을 골라 답한다. "
                        "맥락으로 알 수 없으면 각각 한 줄씩 짧게 설명한다.", "matched": name}

    def fuzzy_matches(self, q, taken):
        """정확히 맞는 이름이 없는 한글 단어를 오타 허용으로 이름과 맞춘다 -> [(위치, 이름 키, 질문 속 표기)].
        단어 앞부분도 본다('스프링쿨러좀' -> '스프링쿨러')."""
        out = []
        for m in re.finditer(r"[가-힣]+", q):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            # 조사를 뗀 말도 원래 단어의 앞부분이라 원래 단어로 본다('돌곡굉이'의 '이'를 조사로 떼면 안 된다).
            word = m.group()
            if (re.sub(rf"(?:{JOSA})$", "", word) or word) in QUERY_STOPWORDS:
                continue
            for length in range(min(len(word), FUZZY_MAX_LEN), FUZZY_MIN_LEN - 1, -1):
                part = word[:length]
                scored = [(name_distance(part, k), k) for k in self.fuzzy_keys.get(length, ())]
                best = min((x for x in scored if x[0] <= FUZZY_MAX_DISTANCE), default=None)
                if best:
                    out.append((m.start(), best[1], part))
                    taken.append((m.start(), m.start() + length))
                    break
        return out

    @staticmethod
    def intent_target(question, keys):
        """만들기·쓰임새를 묻는 말이 어느 이름에 걸리는지: 그 말 바로 앞에 나온 이름, 없으면 첫 이름.
        ('석탄 캤더니 Geode가 나오네? 어따 써?' -> Geode)"""
        if not keys:
            return None
        found = [m.start() for r in (MAKE_INTENT_RE, USE_INTENT_RE, FAMILY_INTENT_RE) for m in r.finditer(question.lower())]
        if not found:
            return keys[0][1]
        before = [k for k in keys if k[0] < min(found)]
        return (before[-1] if before else keys[0])[1]

    def family(self, key):
        """'물뿌리개' -> [('구리 물뿌리개', [ID들]), ('철 물뿌리개', [...]), ...] (아이템·블록만, 제작법 있는 것 먼저)"""
        if len(key) < FAMILY_MIN_KEY:
            return []
        out = {}
        for k, ids in self.ids_by_key.items():
            if k != key and k.endswith(key) and len(k) - len(key) <= FAMILY_MAX_PREFIX:
                for i in ids:
                    entry = self.dict_by_id[i]
                    if entry.get("kind") in ("item", "block") and entry.get("ko") and i not in out.get(entry["ko"], []):
                        out.setdefault(entry["ko"], []).append(i)
        make = {name: " ".join(m for i in ids for m in (self.recipe_facts.get(i) or {}).get("make", []))
                for name, ids in out.items()}

        def depth(name, seen=()):
            # 재료에 같은 계열의 다른 아이템이 들어 있으면 그다음 등급이다(철 물뿌리개 = 구리 물뿌리개 + 철 주괴).
            before = [o for o in out if o != name and o not in seen and o in make[name]]
            return 1 + max(depth(o, seen + (name,)) for o in before) if before else 0
        return sorted(out.items(), key=lambda kv: (not make[kv[0]], depth(kv[0])))

    def recipe_card(self, key, question):
        """질문이 만들기·쓰임새·업그레이드를 물을 때, 이름이 나온 아이템의 실제 서버 레시피로 만든 확정 정보 조각."""
        if self.recipe_facts is None:
            return None
        want_make = MAKE_INTENT_RE.search(question)
        want_use = USE_INTENT_RE.search(question)
        want_family = FAMILY_INTENT_RE.search(question)
        if not (want_make or want_use or want_family):
            return None
        ids = [i for i in self.ids_by_key.get(key, []) if i in self.recipe_facts]
        if not ids:
            return None
        entry = self.dict_by_id[ids[0]]
        name = entry.get("ko") or entry.get("en") or entry["alias"]
        facts = [self.recipe_facts[i] for i in ids]
        lines = []
        members = self.family(key)[:MAX_FAMILY]
        if want_make or want_family:
            make = list(dict.fromkeys(l for f in facts for l in f["make"]))[:3]
            lines += [f"만드는 법: {l}" for l in make] or ["만드는 법: 이 서버에서는 조합·가공으로 만들 수 없음"]
            # 이름 자체는 못 만들어도 같은 계열('구리 물뿌리개' 등)은 만들 수 있으면 그쪽을 함께 보여준다.
            # ('물뿌리개 어떻게 만들어?'의 답이 '만들 수 없음'으로 끝나지 않게)
            if not make and any((self.recipe_facts.get(i) or {}).get("make") for _, ids in members for i in ids):
                want_family = True
        if want_use:
            uses = list(dict.fromkeys(u for f in facts for u in f["uses"]))
            total = max(f["uses_total"] for f in facts)
            # 재료로 안 쓰이면 말하지 않는다(정동석처럼 손에 들고 쓰는 아이템을 '쓸 데 없다'고 오해하게 만든다).
            if uses:
                lines.append(f"재료로 쓰이는 곳: {', '.join(uses)}" + (f" 등 {total}가지" if total > len(uses) else ""))
        if want_family:
            if members:
                lines.append(f"다른 등급·종류({name} 계열): {', '.join(m for m, _ in members)}")
            for member, member_ids in members:
                # 같은 이름이 여러 모드에 있으면 각각의 첫 제작법을 보여준다.
                make = list(dict.fromkeys(m[0] for i in member_ids if (m := (self.recipe_facts.get(i) or {}).get("make"))))
                if make:
                    lines.append(f"{member} 만드는 법: {' / '.join(make[:2])}")
        if not lines:
            return None
        return {"id": f"recipe:{key}", "source": "recipe", "title": f"제작법·쓰임새: {name}",
                "text": "\n".join(lines), "names": [name], "matched": name}

    @classmethod
    def load(cls, path=config.CHUNKS_FILE, names_path=config.NAMES_FILE):
        with open(path, encoding="utf-8") as f:
            chunks = [json.loads(line) for line in f]
        dictionary = []
        if names_path.exists():
            with open(names_path, encoding="utf-8") as f:
                dictionary = [json.loads(line) for line in f]
        recipe_facts = None
        if config.RECIPE_FACTS_FILE.exists():
            with open(config.RECIPE_FACTS_FILE, encoding="utf-8") as f:
                recipe_facts = {d["id"]: d for d in map(json.loads, f)}
        return cls(chunks, dictionary, recipe_facts)

    def search(self, query, k=config.SEARCH_K, unigram_weight=UNIGRAM_WEIGHT, names=(), skip_sources=()):
        """BM25. unigram_weight=0이면 2글자 단위만 본다(게임 용어가 실제로 걸렸는지 판단할 때).
        names: 질문에서 알아본 이름들. 본문에 그 이름이 있는 조각을 앞세운다."""
        names = [re.sub(r"\s+", "", n.lower()) for n in names if n]
        words = re.findall(r"[0-9a-z]+|[가-힣]+", query.lower())
        extra = [v for prefix, v in QUERY_EXPANSIONS.items() if any(w.startswith(prefix) for w in words)]
        terms = set(tokenize(" ".join([query] + extra), query=True))
        scored = []
        for chunk, (tf, length) in zip(self.chunks, self.docs):
            if chunk["source"] in skip_sources:
                continue
            score = 0.0
            for t in terms:
                f = tf.get(t)
                if f:
                    weight = unigram_weight if t.startswith("1:") else 1.0
                    score += weight * self.idf[t] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * length / self.avgdl))
            if score > 0:
                if names and any(n in re.sub(r"\s+", "", chunk["text"].lower()) for n in names):
                    score *= NAME_BOOST
                scored.append((score, chunk))
        scored.sort(key=lambda x: -x[0])
        return scored[:k]


def relevant(hits, cut=RELATIVE_CUT):
    """1등 점수에 비해 너무 낮은 자료를 뺀다."""
    if not hits:
        return []
    top = hits[0][0]
    return [(s, c) for s, c in hits if s >= top * cut]


def build_context(pinned, hits, extras=(), budget=4000, cut=RELATIVE_CUT):
    """질문에 이름이 나온 조각을 '확정 정보'로 맨 앞에 두고, 나머지는 점수 순으로 글자 예산 안에서 넣는다."""
    parts, used = [], 0
    pinned_ids = {c["id"] for c in pinned}
    blocks = [f"### [확정 정보] {c['title']}{matched_note(c['title'], c.get('matched'))}\n{c['text']}" for c in pinned]
    blocks += [entity_card(e) for e in extras]
    blocks += [f"### {c['title']}\n{c['text']}" for _, c in relevant(hits, cut) if c["id"] not in pinned_ids]
    for block in blocks:
        if used + len(block) > budget:
            continue
        parts.append(block)
        used += len(block) + 2
    return "\n\n".join(parts)
