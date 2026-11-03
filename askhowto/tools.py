"""LLM에게 쥐어주는 읽기 전용 도구 (NIM·Gemini처럼 tools를 지원하는 클라우드 모델만).

- search_docs(query): 서버 자료 + 위키 색인 검색. 한국어 이름은 사전으로 영어 이름도 같이 찾는다.
- read_page(title, section): 위키 문서 한 섹션 읽기 (섹션을 비우면 섹션 목록과 첫 부분).
개념은 docs/design/rag-concepts.md 4장.
"""
import json
import re

from . import rag, wiki

MAX_RESULT_CHARS = 3500
SEARCH_RESULTS = 6

SPECS = [
    {"type": "function", "function": {
        "name": "search_docs",
        "description": "선릿 밸리 서버 자료(퀘스트, 농업연감, 상점, 레시피, 아이템 설명)와 공식 위키(영어)를 검색한다. "
                       "주어진 [자료]로 답이 부족할 때만 쓴다. 한국어나 영어 검색어 모두 된다.",
        "parameters": {"type": "object", "properties": {
            "query": {"type": "string", "description": "찾을 내용. 아이템·NPC 이름을 넣으면 잘 찾는다."}},
            "required": ["query"]}}},
    {"type": "function", "function": {
        "name": "read_page",
        "description": "공식 위키 문서의 한 섹션을 읽는다. search_docs 결과의 '위키 <문서> › <섹션>' 제목을 그대로 넘긴다. "
                       "section을 비우면 섹션 목록과 첫 부분을 돌려준다.",
        "parameters": {"type": "object", "properties": {
            "title": {"type": "string", "description": "위키 문서 제목 (영어, 예: Fish Pond) 또는 한국어 이름"},
            "section": {"type": "string", "description": "섹션 제목 (예: Mechanics). 모르면 빈 문자열"}},
            "required": ["title"]}}},
]

_pages = None


def _wiki_pages():
    global _pages
    if _pages is None:
        _pages = {title: wiki.sections(text) for title, text in wiki.pages().items()}
    return _pages


def search_docs(index, query, seen=()):
    """seen: 이미 [자료]로 넣은 조각 ID들 (같은 내용을 다시 주지 않는다)."""
    pinned, extras = index.match_names(query)
    english = [n for c in pinned for n in c.get("names", [])[1:] if n.isascii()] + [e.get("en", "") for e in extras]
    hits = index.search(" ".join([query] + english), k=SEARCH_RESULTS * 2,
                        names=[c["names"][0] for c in pinned if c.get("names")])
    chunks = [c for c in pinned] + [c for _, c in rag.relevant(hits, rag.WIDE_RELATIVE_CUT)]
    out, used, ids = [], 0, set(seen)
    for c in chunks:
        if c["id"] in ids:
            continue
        ids.add(c["id"])
        block = f"### {c['title']}\n{c['text']}"
        if used + len(block) > MAX_RESULT_CHARS or len(out) >= SEARCH_RESULTS:
            break
        out.append(block)
        used += len(block)
    out += [rag.entity_card(e) for e in extras if used < MAX_RESULT_CHARS][:2]
    return "\n\n".join(out) or "검색 결과 없음. 다른 검색어(영어 이름 등)로 다시 찾아볼 수 있다."


def _find_page(title, index):
    pages = _wiki_pages()
    key = re.sub(r"^위키\s*", "", title).split("›")[0].split("(")[0].strip().lower()
    for t in pages:
        if t.lower() == key:
            return t
    # 한국어 이름으로 물으면 위키 조각의 이름 목록(한국어 이름 + 영어 제목)으로 찾는다.
    for c in index.chunks:
        if c["source"] == "wiki" and key in [n.lower() for n in c.get("names", [])]:
            return c["names"][-1]
    return next((t for t in pages if key and key in t.lower()), None)


def read_page(index, title, section=""):
    page = _find_page(title, index)
    if not page:
        return f"'{title}' 위키 문서를 찾지 못했다. search_docs로 먼저 찾아본다."
    secs = _wiki_pages()[page]
    names = [s or "(첫 부분)" for s, _ in secs]
    want = (section or "").split("›")[-1].split("(")[0].strip().lower()
    if want:
        for s, lines in secs:
            if want in (s or "").lower():
                return f"### 위키 {page} › {s}\n" + "\n".join(lines)[:MAX_RESULT_CHARS]
    s, lines = secs[0]
    return (f"### 위키 {page}\n섹션 목록: {', '.join(names)}\n\n" + "\n".join(lines))[:MAX_RESULT_CHARS]


def run(index, name, arguments, seen=()):
    """도구 호출 하나 실행 -> 결과 문자열. 인자가 깨졌거나 모르는 도구면 그 사실을 문자열로 돌려준다."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else (arguments or {})
    except ValueError:
        return "도구 인자를 읽지 못했다(JSON 형식이 아님)."
    if name == "search_docs":
        return search_docs(index, str(args.get("query", "")), seen)
    if name == "read_page":
        return read_page(index, str(args.get("title", "")), str(args.get("section", "")))
    return f"모르는 도구: {name}"
