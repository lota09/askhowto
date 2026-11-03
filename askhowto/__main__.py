"""사용법:
  python3 -m askhowto build            모드팩 자료로 data/chunks.jsonl 생성
  python3 -m askhowto wiki             공식 위키 문서를 data/wiki/raw.json 으로 다시 받기 (그다음 build)
  python3 -m askhowto search <질문>     검색 결과만 확인
  python3 -m askhowto ask [--model nim|gemini|local] <질문>   서버 없이 답변 테스트 (기본: 폴백 순서대로)
  python3 -m askhowto serve            KubeJS 파일 큐 처리 (상주)
"""
import sys

from . import bot, build_docs, config, rag, wiki, worker


def main(argv):
    cmd, rest = (argv[0], " ".join(argv[1:])) if argv else ("", "")
    if cmd == "build":
        total, counts = build_docs.build()
        print(f"{total} chunks -> {config.CHUNKS_FILE}")
        for source, n in counts.items():
            print(f"  {source}: {n}")
    elif cmd == "wiki":
        print(f"{wiki.fetch()} pages -> {wiki.RAW_FILE}")
    elif cmd == "search" and rest:
        pinned, extras, hits, strong = bot.search(rest)
        print(f"게임 용어 점수(2글자 단위): {strong:.1f}")
        for c in pinned:
            print(f" 고정  {c['title']}  ← '{c.get('matched')}'")
        for e in extras:
            print(f" 사전  {e.get('ko')} / {e.get('en')} ({e['id']})")
        for score, c in hits:
            print(f"{score:5.1f}  {c['title']}")
        print("---\n" + rag.build_context(pinned, hits, extras))
    elif cmd == "ask" and rest:
        choice = "default"
        if argv[1] == "--model" and len(argv) > 3:
            choice, rest = argv[2], " ".join(argv[3:])
        res = bot.answer(rest, choice)
        print(f"[{res['label']}] {res.get('provider')} {res.get('model') or ''}")
        if res.get("notice"):
            print(f"({res['notice']})")
        print("\n".join(res["lines"]))
        if res["sources"]:
            print("참고: " + " · ".join(res["sources"]))
        if res["refs"]:
            print("--- LLM에 넣은 자료: " + " | ".join(res["refs"]))
    elif cmd == "serve":
        if not config.CHUNKS_FILE.exists():
            build_docs.build()
        worker.serve()
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
