"""플레이어별 최근 대화 기억 (data/memory/<uuid>.json). 압축(요약)은 하지 않고 최근 몇 개만 둔다.
시간이 지나도 지우지 않는다. /askforget 으로만 지운다."""
import json
import re
import time

from . import config

# 로컬 Gemma가 안정적으로 넣을 수 있는 최대 개수보다 하나 적게 (11/03 실측: 자료 4,000자를 꽉 채운 최악의 경우
# 답 200자짜리 대화 6개까지 성공, 7개부터 연결 끊김. 5·6개도 가끔 끊겨서 5개로 둔다).
# 그래도 넘칠 때를 위해 로컬은 프롬프트 전체 글자 수(PROVIDERS["local"]["max_prompt_chars"])에 맞춰 오래된 것부터 뺀다.
MAX_TURNS = 5
# 다음 질문이 무엇을 이어 묻는지 알 만큼만 남긴다.
ANSWER_CHARS = 200
MEMORY_DIR = config.DATA_DIR / "memory"


def _path(uuid):
    # UUID 형식만 파일 이름으로 쓴다(요청 파일에서 온 값이라 경로 문자를 막는다).
    return MEMORY_DIR / (re.sub(r"[^0-9a-fA-F-]", "", str(uuid))[:36] + ".json") if uuid else None


def load(uuid):
    """최근 대화 [{time, question, answer, names}] (오래된 것부터)."""
    path = _path(uuid)
    if not path or not path.exists():
        return []
    try:
        turns = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return turns[-MAX_TURNS:] if isinstance(turns, list) else []


def remember(uuid, question, lines, names):
    path = _path(uuid)
    if not path:
        return
    turns = load(uuid) + [{"time": time.time(), "question": question,
                           "answer": " ".join(lines)[:ANSWER_CHARS], "names": list(names)}]
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(turns[-MAX_TURNS:], ensure_ascii=False), encoding="utf-8")


def forget(uuid):
    path = _path(uuid)
    if path:
        path.unlink(missing_ok=True)
