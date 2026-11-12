"""도우미 봇 설정. 환경변수(ASKHOWTO_*)로 덮어쓸 수 있다."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = Path(os.environ.get(
    "ASKHOWTO_SERVER_DIR",
    ROOT.parent.parent / "servers" / "SocietySunlitValley415A1201",
)).resolve()

# KubeJS(askhowto.js)와 주고받는 파일 큐. JsonIO는 게임 폴더 안에만 쓸 수 있어서 서버 폴더 아래에 둔다.
QUEUE_DIR = SERVER_DIR / "local" / "askhowto"
REQUEST_DIR = QUEUE_DIR / "requests"
RESPONSE_DIR = QUEUE_DIR / "responses"
FEEDBACK_DIR = QUEUE_DIR / "feedback"
# 처리 중 서비스 실패 안내 (사이드카가 실패할 때마다 쓰고, KubeJS가 기다리는 동안 읽어 채팅에 보여준다)
PROGRESS_DIR = QUEUE_DIR / "progress"
STATUS_FILE = QUEUE_DIR / "status.json"
# 서버 시작 때 kubejs/askhowto_recipes.js가 쓰는 최종 레시피 목록
RECIPES_FILE = Path(os.environ.get("ASKHOWTO_RECIPES_FILE", QUEUE_DIR / "recipes.json"))
# 배송 상자 판매가 표 (kubejs/askhowto_prices.js가 서버 시작 때 씀)
PRICES_FILE = Path(os.environ.get("ASKHOWTO_PRICES_FILE", QUEUE_DIR / "prices.json"))

DATA_DIR = ROOT / "data"
CHUNKS_FILE = DATA_DIR / "chunks.jsonl"
# 서버를 켜지 않고 KubeJS startup_scripts를 Rhino로 실행해 뽑은 전역 표(장인 설비 레시피, 판매가). tools_offline/ 참고.
STARTUP_DUMP_FILE = DATA_DIR / "startup_dump.json"
# 묶음 이름(태그의 한국어 이름) -> 아이템 ID들 ('갑옷' -> 철 흉갑 …)
CATEGORIES_FILE = DATA_DIR / "categories.json"
_HOME_JDK = Path.home() / ".local/jdk/jdk-17.0.21+9/bin"
# apt openjdk-17-jdk-headless (폰 arm64, 데스크톱 amd64)
_SYS_JDK = next((p for p in sorted(Path("/usr/lib/jvm").glob("java-17-openjdk-*/bin")) if (p / "javac").exists()),
                Path("/usr/lib/jvm/java-17-openjdk-arm64/bin"))
JDK_BIN = Path(os.environ.get("ASKHOWTO_JDK_BIN", str(_HOME_JDK if (_HOME_JDK / "javac").exists() else _SYS_JDK)))
NAMES_FILE = DATA_DIR / "names.jsonl"
# 아이템별 제작법·쓰임새 (검색 색인에는 넣지 않고, 질문에 이름이 나왔을 때만 붙인다)
RECIPE_FACTS_FILE = DATA_DIR / "recipes.jsonl"
# 바이옴 ID -> 한국어 이름 (플레이어 상태용)
PLACES_FILE = DATA_DIR / "places.json"
MANUAL_DIR = ROOT / "docs" / "manual"
LOG_FILE = ROOT / "logs" / "qa.jsonl"
FEEDBACK_LOG_FILE = ROOT / "logs" / "feedback.jsonl"

# LLM 서비스들. 주소·모델은 여기, API 키는 llm.env(run.sh가 읽음)에 둔다. 키는 코드와 README에 적지 않는다.
#   reasoning: 답변용 reasoning_effort ("" = 모델 기본값), classify_reasoning: YES/NO 분류용
#   ("none"이면 추론 끔, 추론을 끌 수 없는 모델은 "low"), context_chars·top_k: 자료를 얼마나 넣을지,
#   timeout·retries: 실패로 보고 다음 서비스로 넘어가기까지. 비교 평가는 runs/model-compare/REPORT.md (11/03).
def _provider(name, label, url, model, key="", reasoning="", classify_reasoning="", context_chars=4000, top_k=10,
              max_tokens=300, timeout=180, retries=5, tools=False, max_prompt_chars=0, answer_chars=700,
              backup_models=(), retry_wait=1):
    env = lambda k, default: os.environ.get(f"ASKHOWTO_{name.upper()}_{k}", default)
    return {
        "name": name, "label": label, "url": env("URL", url).rstrip("/"), "model": env("MODEL", model),
        # 같은 서비스 안의 예비 모델: 한도 초과·혼잡·시간 초과면 다음 서비스로 가기 전에 이것부터 (한도가 모델마다 따로다)
        "backup_models": [m for m in env("BACKUP_MODELS", ",".join(backup_models)).split(",") if m],
        # 재시도 간격 = retry_wait × 시도 번호(초). 로컬은 바쁠 때 연결을 바로 끊어서 길게 기다린다.
        "retry_wait": float(env("RETRY_WAIT", retry_wait)),
        "key": env("API_KEY", key), "reasoning": env("REASONING", reasoning),
        "classify_reasoning": env("CLASSIFY_REASONING", classify_reasoning),
        "context_chars": int(env("CONTEXT_CHARS", context_chars)), "top_k": int(env("TOP_K", top_k)),
        "max_tokens": int(env("MAX_TOKENS", max_tokens)), "timeout": int(env("TIMEOUT", timeout)),
        "retries": int(env("RETRIES", retries)), "needs_key": not url.startswith("http://"),
        # 검색·읽기 도구(tools.py)를 쓰는지. tools를 지원하는 큰 모델만 (11/03 NIM glm, Gemini 확인)
        "tools": env("TOOLS", "1" if tools else "0") == "1",
        # 시스템+질문 프롬프트 전체 글자 상한(0이면 없음). 넘치면 이전 대화를 오래된 것부터 뺀다.
        "max_prompt_chars": int(env("MAX_PROMPT_CHARS", max_prompt_chars)),
        # 채팅에 보낼 답의 글자 상한. 넘치면 문장 단위로 줄이고 줄였다고 표시한다.
        "answer_chars": int(env("ANSWER_CHARS", answer_chars)),
    }


PROVIDERS = {
    # 무료. 89문항 분류 89/89, 사실 29/31, 중앙값 5.8초 (가끔 70초). 추론을 끌 수 없다.
    "nim": _provider("nim", "NVIDIA NIM", "https://integrate.api.nvidia.com/v1", "z-ai/glm-5.3-flash",
                     reasoning="low", classify_reasoning="low", context_chars=12000, top_k=20, max_tokens=2000,
                     # 시간 제한 30초: 성공한 호출도 28초까지 걸린 적이 있다(11/06). 실패하면 bot.COOLDOWN으로 5분간 건너뛴다.
                     timeout=30, retries=1, tools=True),
    # 무료 등급. 빠르지만 분당 한도에 자주 걸린다(429면 바로 다음 서비스로).
    # 3.1-flash-lite가 혼잡(503)이거나 가끔 50초 가까이 걸려서, 한도가 따로인 3.5-flash-lite를 예비로 둔다(11/03).
    # 시간 제한 15초: 11/04 밤에 3.1-lite가 30초를 다 쓰고 끊긴 뒤 예비 모델이 1~2초 만에 답한 일이 잦았다.
    # 평소 답은 3~10초라 15초면 느린 요청만 예비 모델로 넘어간다.
    # 출력 3,000토큰: reasoning low여도 생각에 960토큰까지 써서 1,000이면 답이 단어 중간에서 끊겼다(11/04 Altimeter 답).
    "gemini": _provider("gemini", "Gemini", "https://generativelanguage.googleapis.com/v1beta/openai",
                        "gemini-3.1-flash-lite", reasoning="low", classify_reasoning="none", context_chars=12000,
                        top_k=20, max_tokens=3000, timeout=15, retries=1, tools=True,
                        backup_models=("gemini-3.5-flash-lite",)),
    # 이 기기(9700K 데스크톱)의 llama.cpp: 별칭 'local'(Qwen3.8-Flash-Next IQ3_M, CUDA). 11/13 실측 입력 830 tok/s,
    # 출력 48 tok/s, 문맥 슬롯당 약 58K토큰, 도구 호출 됨 -> 클라우드와 같은 방식(도구 사용, 자료 12,000자)으로 1순위.
    # 처음 한 번은 불러오느라 1분쯤 걸릴 수 있다.
    "local": _provider("local", "로컬 Qwen", "http://localhost:8080/v1", "local",
                       context_chars=12000, top_k=20, max_tokens=2000, timeout=90, retries=1, tools=True),
}
# /askmodel default 일 때 이 순서로 시도한다. 직접 고른 서비스는 실패해도 넘어가지 않는다.
DEFAULT_CHAIN = os.environ.get("ASKHOWTO_CHAIN", "local,nim,gemini").split(",")
MODEL_CHOICES = ["default"] + list(PROVIDERS)
SEARCH_K = max(p["top_k"] for p in PROVIDERS.values())

# 채팅에 보낼 답 길이 (자료가 많아져도 채팅창은 그대로라 늘리지 않는다)
ANSWER_MAX_CHARS = 450

QUESTION_MAX_CHARS = 200
POLL_INTERVAL = 0.5
HEARTBEAT_INTERVAL = 5
