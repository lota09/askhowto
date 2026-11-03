"""사이드카 E2E 시나리오: KubeJS가 쓰는 것과 같은 요청을 worker.handle에 넣고 응답 파일을 읽는다.

python3 tests/e2e.py            (. ./llm.env 로 키를 먼저 읽는다)
- 큐는 runs/e2e-queue/ 를 쓴다(서버 큐와 따로). 기억은 테스트용 UUID로 남기고 끝나면 지운다.
- 각 시나리오는 대화 순서가 있는 질문 목록이다. 응답, 답한 서비스, 도구 호출, '그거/이거'로 이어 붙인 이름을 출력한다.
"""
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
QUEUE = ROOT / "runs" / "e2e-queue"
sys.path.insert(0, str(ROOT))

from askhowto import config  # noqa: E402

for name in ("REQUEST_DIR", "RESPONSE_DIR", "FEEDBACK_DIR", "PROGRESS_DIR", "STATUS_FILE"):
    setattr(config, name, QUEUE / getattr(config, name).relative_to(config.QUEUE_DIR))
config.QUEUE_DIR = QUEUE
from askhowto import memory, worker  # noqa: E402

STATE = {"mainhand": {"id": "society:geode", "count": 3}, "offhand": None,
         "inventory": [{"id": "minecraft:wheat", "count": 64}, {"id": "minecraft:coal", "count": 20},
                       {"id": "society:geode", "count": 3}, {"id": "minecraft:wheat_seeds", "count": 40}],
         "weather": "rain", "day": 12, "daytime": 1000, "season": "summer",
         "biome": "minecraft:plains", "dimension": "minecraft:overworld"}

FORGET = "/askforget"
# (시나리오 이름, 모델 선택, [(질문, 상태)])  상태 None이면 상태 없이 보낸다. 질문이 FORGET이면 기억 지우기 요청.
SCENARIOS = [
    ("손에 든 아이템 '이거'", "default", [("이거 어따 써?", STATE)]),
    ("후속 질문 '걔'", "default", [("캐롤라인은 누구야?", None), ("걔한테 뭘 선물하면 좋아해?", None)]),
    ("후속 질문 대상 생략", "default", [("물뿌리개 업그레이드 버전 있음?", None), ("어떻게 만들어?", None)]),
    ("상태 활용(계절·날씨)", "default", [("지금 계절에 심을 만한 작물 추천해줘", STATE)]),
    ("상태와 무관한 질문", "default", [("엔더 드래곤 잡는 법", STATE)]),
    ("도구가 필요한 질문", "default", [("Tiny Gnome은 어디서 얻어?", None)]),
    ("도구가 필요한 질문 2", "default", [("우정 레벨 올리면 뭐가 좋아?", None)]),
    ("직접 지정: 로컬 (도구 없음)", "local", [("이거 어따 써?", STATE)]),
    ("기억 지우기 뒤 '그거'", "default", [("캐롤라인은 누구야?", None), (FORGET, None), ("그거 어떻게 해?", None)]),
]


def run():
    for d in (config.REQUEST_DIR, config.RESPONSE_DIR, config.FEEDBACK_DIR, config.PROGRESS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    total = 0
    for n, (title, choice, turns) in enumerate(SCENARIOS):
        uuid = f"00000000-0000-0000-0000-{n:012d}"
        memory.forget(uuid)
        print(f"\n===== {title} ({choice})")
        for question, state in turns:
            rid = f"e2e-{n}-{time.time_ns()}"
            req = {"id": rid, "player": "e2e", "uuid": uuid, "question": question, "time": 0, "model": choice}
            if question == FORGET:
                req.update(question="", action="forget")
            if state:
                req["state"] = state
            path = config.REQUEST_DIR / f"{rid}.json"
            path.write_text(json.dumps(req, ensure_ascii=False), encoding="utf-8")
            start = time.time()
            worker.handle(path, req, 0)
            elapsed = time.time() - start
            total += elapsed
            resp = json.loads((config.RESPONSE_DIR / f"{rid}.json").read_text(encoding="utf-8"))
            (config.RESPONSE_DIR / f"{rid}.json").unlink()
            rec = worker.recent.get(rid, {})
            print(f"Q: {question}  [{resp['label']} {rec.get('provider')} {elapsed:.1f}s]")
            for line in resp["text"].splitlines():
                print(f"   {line}")
            if resp.get("notice"):
                print(f"   ({resp['notice']})")
            print(f"   참고: {resp['sources'].replace(chr(10), ' · ')}")
            if rec.get("referred"):
                print(f"   ↳ 가리킨 대상: {rec['referred']}")
            for t in rec.get("tools", []):
                print(f"   ↳ 도구 {t['name']} {t['arguments']} -> {t['chars']}자")
        memory.forget(uuid)
    print(f"\n총 {total:.0f}초")


if __name__ == "__main__":
    os.chdir(ROOT)
    run()
