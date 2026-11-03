"""도구(search_docs, read_page)의 효과를 잰다: 같은 질문을 NIM으로 도구 있음/없음 두 번 답하게 해 비교한다.

python3 tests/tools_eval.py   (. ./llm.env 먼저)
문항은 첫 검색만으로는 한 번에 안 풀리는 것들이다(위키 문서 두 개를 이어야 하는 질문 등).
사실은 11/03 위키·서버 자료에서 확인한 것만 넣었다.
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from askhowto import bot, config  # noqa: E402

CASES = [
    # 캐롤라인 문서(5하트 선물: Slouching Towards Artistry) -> Books 문서(장인 호퍼가 스파크스톤을 덜 씀)
    ("캐롤라인이 우정 보상으로 주는 스킬북은 무슨 효과야?", ["스파크스톤"]),
    # 레온 문서(Universal Methods of Farming = 농사 방법론) -> 책 효과(마트가 모든 기본 씨앗을 계절과 상관없이 판매)
    # ('씨앗'은 이미 읽었을 때 주는 '스파크포드 씨앗'에도 걸려서 '계절'로 본다)
    ("레온이 5하트에 주는 책은 무슨 효과야?", ["계절"]),
    # Ancient Roe 문서: 상인에게서 사고 생선 훈제기에 쓴다
    ("고대 어란은 어디에 써?", ["훈제"]),
    # Black Opal 문서: 상인이 판매
    ("결정생성기에 쓰는 검은 오팔은 어디서 사?", ["상인"]),
    # Day Cycle 문서: 황금 시계와 성장 오벨리스크는 오전 7시
    ("황금 시계는 몇 시에 작동해?", ["7시"]),
    # 위키는 오전 8시라고 하지만 서버 아이템 설명은 '매일 아침 7시'. 서버 자료가 우선이라 7시가 맞다.
    ("리빗 오두막은 몇 시부터 수확해?", ["7시"]),
]


def run():
    nim = config.PROVIDERS["nim"]
    totals = {}
    for tools_on in (False, True):
        nim["tools"] = tools_on
        ok, seconds, calls = 0, 0.0, 0
        print(f"\n===== 도구 {'있음' if tools_on else '없음'}")
        for question, must in CASES:
            start = time.time()
            res = bot.answer(question, "nim")
            elapsed = time.time() - start
            text = " ".join(res["lines"])
            missing = [m for m in must if m not in text]
            ok += not missing
            seconds += elapsed
            calls += len(res.get("tools", []))
            print(f"{'OK ' if not missing else 'BAD'} {elapsed:5.1f}s  {question}" + (f"  (빠진 내용: {missing})" if missing else ""))
            print(f"      {text[:200]}")
            for t in res.get("tools", []):
                print(f"      ↳ {t['name']} {t['arguments']}")
        totals[tools_on] = (ok, seconds, calls)
    for tools_on, (ok, seconds, calls) in totals.items():
        print(f"도구 {'있음' if tools_on else '없음'}: {ok}/{len(CASES)} 맞음, 평균 {seconds / len(CASES):.1f}초, 도구 호출 {calls}번")


if __name__ == "__main__":
    run()
