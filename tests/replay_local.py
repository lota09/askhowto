"""플레이어가 [도움됨]을 누른 질문을 로컬 Gemma로 다시 답하게 해서, 당시의 좋은 답과 나란히 본다.

python3 tests/replay_local.py [nim|gemini|local]   (기본 local, . ./llm.env 먼저)
당시의 플레이어 상태(qa.jsonl의 state 글)를 그대로 다시 넣는다. 대화 기억은 넣지 않는다(당시 기억은 기록이 없다).
"""
import json
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from askhowto import bot, config  # noqa: E402


def good_cases():
    qa = {}
    for line in open(config.LOG_FILE, encoding="utf-8"):
        d = json.loads(line)
        if d.get("id"):
            qa[d["id"]] = d
    out = []
    for line in open(config.FEEDBACK_LOG_FILE, encoding="utf-8"):
        fb = json.loads(line)
        if fb.get("vote") == "good" and fb.get("question"):
            # qa.jsonl에 있으면 당시 상태까지, 없으면 평가 기록의 질문·답만 쓴다.
            out.append(qa.get(fb.get("id")) or {"question": fb["question"], "answer": fb.get("answer") or []})
    return out


def run(choice):
    total = 0.0
    for rec in good_cases():
        state = None
        if rec.get("state"):
            held = re.search(r"주 손: ([^,\n]+?)(?: \d+개)?(?:,|\n|$)", rec["state"])
            state = {"text": rec["state"], "held_name": held.group(1) if held else None, "search_words": []}
        start = time.time()
        res = bot.answer(rec["question"], choice, [], state)
        elapsed = time.time() - start
        total += elapsed
        print(f"\n### {rec['question']}  [{res['label']} {res.get('provider')} {elapsed:.1f}s]"
              + (f"  ({' / '.join(res.get('notices', []))})" if res.get("notices") else ""))
        print("  지금: " + " / ".join(res["lines"])[:400])
        print(f"  당시({rec.get('provider') or '?'}): " + " / ".join(rec["answer"])[:400])
    print(f"\n총 {total:.0f}초")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "local")
