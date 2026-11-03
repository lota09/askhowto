"""선릿 밸리 공식 위키(https://sunlitvalley.miraheze.org, 영어, CC BY-SA 4.0)를 받아 검색 조각으로 만든다.

- `python3 -m askhowto wiki` 가 일반 문서 전부를 data/wiki/raw.json 으로 다시 받는다(150개 문서, 몇 초).
- 번역하지 않고 영어 원문 그대로 넣는다. 한국어 질문과는 이름 사전으로 잇는다(문서 제목 'Caroline' = 캐롤라인).
- 빼는 문서: 넘겨주기, 다른 팩(Cobblemon) 문서, 위키 운영 문서, 아주 큰 표 문서(판매가 표 등, 글 조각으로는 쓸모없음).
"""
import json
import re
import time
import urllib.parse
import urllib.request

from . import config

API = "https://sunlitvalley.miraheze.org/w/api.php"
SITE = "https://sunlitvalley.miraheze.org/wiki/"
USER_AGENT = "askhowto-bot/0.1 (Sunlit Valley Minecraft server helper)"
RAW_FILE = config.DATA_DIR / "wiki" / "raw.json"
# 이보다 큰 문서는 거대한 표라 조각으로 나눠 넣어 봐야 검색만 어지럽힌다('Sell Values' 28만 자, 'Longwings' 20만 자).
MAX_PAGE_CHARS = 100_000
# 같은 위키에 섞여 있는 변형 팩(Society: Sunlit Cobblemon) 문서. 제목에 있거나 본문에 여러 번 나오면 뺀다.
OTHER_PACK_RE = re.compile(r"cobblemon|pokémon|pokemon", re.I)
OTHER_PACK_MENTIONS = 3
META_TITLES = {"Main Page", "Contributor Guidelines"}


def _api(params):
    url = API + "?" + urllib.parse.urlencode(dict(params, format="json", formatversion="2"))
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def fetch():
    """일반 문서(이름공간 0) 전부를 받아 RAW_FILE에 쓴다 -> 문서 수."""
    titles = [p["title"] for p in _api({"action": "query", "list": "allpages", "aplimit": "500",
                                         "apnamespace": "0"})["query"]["allpages"]]
    pages = {}
    for i in range(0, len(titles), 50):
        data = _api({"action": "query", "prop": "revisions", "rvprop": "content|timestamp", "rvslots": "main",
                     "titles": "|".join(titles[i:i + 50])})
        for p in data["query"]["pages"]:
            rev = (p.get("revisions") or [{}])[0]
            pages[p["title"]] = {"text": rev.get("slots", {}).get("main", {}).get("content", ""),
                                 "time": rev.get("timestamp")}
        time.sleep(1)  # 위키에 부담을 주지 않게
    RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    RAW_FILE.write_text(json.dumps(pages, ensure_ascii=False), encoding="utf-8")
    return len(pages)


def usable(title, text):
    if title in META_TITLES or text.lower().lstrip().startswith("#redirect") or len(text) > MAX_PAGE_CHARS:
        return False
    return not (OTHER_PACK_RE.search(title) or len(OTHER_PACK_RE.findall(text)) >= OTHER_PACK_MENTIONS)


def _template(body):
    """{{이름|a=b|c}} 하나 -> 읽을 수 있는 글. 정보 상자는 'key: value' 줄로, 그 밖은 값만 남긴다."""
    parts = [p.strip() for p in body.split("|")]
    name, params = parts[0].lower(), parts[1:]
    lines = []
    for p in params:
        key, eq, value = p.partition("=")
        key, value = (key.strip(), value.strip()) if eq else ("", p)
        if not value or key in ("title", "image", "subheader", "font") or value.lower().startswith("[[file:"):
            continue
        if name.startswith("infobox") and key and not key.isdigit():
            lines.append(f"{key}: {value}")
        elif not key or key.isdigit():
            lines.append(value)
    return "\n".join(lines)


def plain(text):
    """위키 문법 -> 평문. 표는 칸을 ' | '로 잇는다."""
    text = re.sub(r"<!--.*?-->|<ref[^>]*>.*?</ref>|<ref[^/]*/>", "", text, flags=re.S)
    text = re.sub(r"\[\[(?:File|Image|Category):[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]", "", text, flags=re.I)
    text = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[\[([^\]]*)\]\]", r"\1", text)
    text = re.sub(r"\[https?://\S+ ([^\]]*)\]", r"\1", text)
    # 안쪽 틀부터 풀어서 겹친 틀도 처리한다.
    for _ in range(5):
        new = re.sub(r"\{\{([^{}]*)\}\}", lambda m: _template(m.group(1)), text)
        if new == text:
            break
        text = new
    text = re.sub(r"</?[a-zA-Z][^>]*>", "", text)
    text = text.replace("'''", "").replace("''", "")
    lines = []
    for line in text.splitlines():
        s = line.strip()
        if s.startswith(("{|", "|}", "|-", "|+")):
            continue
        if s.startswith(("|", "!")):
            # 표 칸: 'style="..." | 값' 꼴의 서식은 버린다.
            cells = [c.split("|")[-1].strip() for c in re.split(r"\|\||!!", s[1:])]
            s = " | ".join(c for c in cells if c)
        s = re.sub(r"^[*#:;]+\s*", "- ", s)
        if s:
            lines.append(s)
    return "\n".join(lines)


def sections(text):
    """평문으로 바꾼 문서 -> [(섹션 제목, 줄들)]. 맨 앞(제목 없는 부분)은 ''."""
    out, title, lines = [], "", []
    for line in plain(text).splitlines():
        m = re.match(r"^=+\s*(.*?)\s*=+$", line)
        if m:
            if lines:
                out.append((title, lines))
            title, lines = m.group(1), []
        else:
            lines.append(line)
    if lines:
        out.append((title, lines))
    return out


def pages():
    """쓸 수 있는 문서 {제목: 원문}. 받은 적이 없으면 {}."""
    if not RAW_FILE.exists():
        return {}
    raw = json.loads(RAW_FILE.read_text(encoding="utf-8"))
    return {t: v["text"] for t, v in raw.items() if usable(t, v["text"])}


TITLE_KO_FILE = config.DATA_DIR / "wiki" / "title_ko.json"
TITLE_PROMPT = """마인크래프트 모드팩 'Society: Sunlit Valley' 위키 문서 제목들이다.
플레이어가 한국어로 이 문서 내용을 물을 때 쓸 만한 한국어 이름을 제목마다 1~3개 붙여라.
JSON 객체 하나만 출력한다. 형식: {"Getting Started": ["시작하기", "초반 공략"], ...}"""


def title_aliases(titles, ask):
    """사전으로 이을 수 없는 문서 제목 -> 한국어 이름들. 처음 한 번만 LLM에 묻고 TITLE_KO_FILE에 둔다(손으로 고쳐도 된다).
    ask: 메시지 목록을 받아 답 문자열을 돌려주는 함수(LLM 호출)."""
    known = json.loads(TITLE_KO_FILE.read_text(encoding="utf-8")) if TITLE_KO_FILE.exists() else {}
    missing = [t for t in titles if t not in known]
    if missing:
        text = ask([{"role": "system", "content": TITLE_PROMPT},
                    {"role": "user", "content": "\n".join(missing)}])
        m = re.search(r"\{.*\}", text, re.S)
        found = json.loads(m.group()) if m else {}
        for t in missing:
            names = found.get(t) or []
            known[t] = [n for n in (names if isinstance(names, list) else [names]) if isinstance(n, str)]
        TITLE_KO_FILE.write_text(json.dumps(known, ensure_ascii=False, indent=1), encoding="utf-8")
    return known
