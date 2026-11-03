# askhowto — 선릿 밸리 도우미 봇

인게임에서 `/ask <질문>` 또는 `/q <질문>` 으로 물어보면 LLM이 모드팩 자료를 검색해(RAG) 채팅으로 답한다.

```
/ask 계절이 작물에 미치는 영향이 어떻게돼?
[도우미] "계절이 작물에 미치는 영향이 어떻게돼?" 생각 중... (보통 10~20초)
[도우미] 계절은 작물이 자라는 시기에 영향을 미칩니다.
- 제철이 아닌 작물은 자라지 않고 성장이 멈춥니다.
- 작물이 어느 계절에 자라는지는 작물 툴팁과 농업연감에서 확인할 수 있습니다.
참고: 이 서버의 계절 규칙 · 퀘스트 › II - 농장 가꾸기 › 농사 시작
  [도움됨] [도움안됨]
```

## 구조

```
플레이어 /ask ─▶ KubeJS(askhowto.js) ──requests/<id>.json──▶ 사이드카(python3 -m askhowto serve)
                     ▲                                          │ 1. 규칙 검사: 서버 환경·보안·프롬프트 조작 → 거절, 인사·잡담 → 정해진 답
                     │                                          │ 2. 이름 매칭: 질문에 나온 아이템·작물·NPC 이름(한/영) → '확정 정보'로 고정
                     │                                          │ 3. BM25 검색 (data/chunks.jsonl), 1등 점수의 40%(클라우드 모델은 20%) 미만은 버림
                     │                                          │ 4. 이름이 나왔거나 검색 점수 10 이상 → 바로 답변, 아니면 LLM YES/NO 분류
                     │                                          │ 5. LLM 답변 (확정 정보 + 검색 자료 + 질문), 못 찾으면 관련 항목 제안
                     │                                          │    서비스 실패 시 NIM → Gemini → 로컬 순서로 넘어감 (/askmodel 기본)
                     └──────── responses/<id>.json ◀────────────┘   (답·출처·폴백 안내)
                     ──────── feedback/<id>.json ──────────────▶  [도움됨]/[도움안됨] → logs/feedback.jsonl
```

- KubeJS(Rhino)는 네트워크 클래스를 막고 있어서 LLM 호출은 사이드카가 한다. 둘은 서버 폴더의 `local/askhowto/` 파일로만 통신하고, RCON이나 포트는 열지 않는다.
- 사이드카는 `status.json`에 5초마다 하트비트를 쓴다. 30초 넘게 끊기면 `/ask`가 "도우미가 꺼져 있어요"라고 바로 알린다.
- 표준 라이브러리만 쓴다(pip 설치 없음). 서버 RAM이 빠듯해서 임베딩 모델 대신 한글 음절 2-gram BM25로 검색한다.

### LLM 서비스와 폴백 (11/03~)

`config.PROVIDERS`에 서비스 3개가 있고, 기본(`/askmodel default`)은 **NIM → Gemini → 로컬** 순서로 시도한다.
한도 초과(402/429)·인증 실패·잘못된 요청은 바로, 시간 초과·5xx는 재시도 뒤 다음 서비스로 넘어간다.
넘어가면 채팅에 회색으로 `(NVIDIA NIM 응답 실패(시간 초과) → Gemini로 답했어요)`를 보여주고, `logs/worker.log`(`fallback:` 줄)와
`logs/qa.jsonl`(`provider`, `fallbacks`)에 남긴다. 플레이어가 `/askmodel nim|gemini|local`로 직접 고르면 그 서비스만 쓰고 넘어가지 않는다.

| 이름 | 서비스·모델 | 자료 | 재시도·시간 제한 | 비고 |
|---|---|---|---|---|
| `nim` | NVIDIA NIM `z-ai/glm-5.3-flash` | 12,000자, 검색 20개, 점수 컷 0.2 | 2번, 90초 | 무료, 한도 비공개. 추론을 끌 수 없어 분류도 `low` |
| `gemini` | Google AI Studio `gemini-3.1-flash-lite` | 12,000자, 검색 20개, 점수 컷 0.2 | 2번, 60초 | 무료 등급(입력이 구글 학습에 쓰임). 분당 한도·혼잡(503)이 잦다 |
| `local` | 로컬 `Gemma-4-E2B-it` | 4,000자, 검색 10개, 점수 컷 0.4 | 5번, 180초 | 아래 실측 |

- API 키는 `llm.env`(권한 600, run.sh가 사이드카를 띄울 때마다 읽음)에만 있다. 키를 지우면 그 서비스는 "키 없음"으로 건너뛴다.
- 순서는 `ASKHOWTO_CHAIN=gemini,local`처럼, 서비스별 값은 `ASKHOWTO_NIM_MODEL`, `ASKHOWTO_GEMINI_CONTEXT_CHARS` 같은 환경변수로 바꾼다.
- 후보 비교(BAZE·Gemini·NIM 11개 모델, 89문항): `runs/model-compare/REPORT.md`.

### 로컬 LLM 실측 (`Gemma-4-E2B-it`, http://192.168.50.153:8000/v1)
- 컨텍스트 약 3.2K 토큰. 넘으면 오류 없이 연결을 끊는다 → 자료는 4000자(약 1000토큰) 안에서만 넣는다(`PROVIDERS["local"]["context_chars"]`).
- 바쁠 때도 가끔 연결을 끊는다 → 최대 5번 재시도.
- 입력 약 120 tok/s, 출력 약 6 tok/s → 질문 하나에 보통 10~15초. 한 번에 하나씩 처리한다.
- 임베딩 API(`/v1/embeddings`)는 없다.

## 문서 (RAG 자료)

`python3 -m askhowto build` 가 서버의 모드팩 파일에서 한국어 자료를 뽑아 `data/chunks.jsonl`을 만든다.

| 소스 | 내용 |
|---|---|
| `kubejs/assets/ftbquestlocalizer/lang/ko_kr.json` | 퀘스트 설명 (가장 많은 공략 정보) |
| `patchouli_books/almanac`, `fish_finder` (ko_kr) | 농업연감(작물·나무·가축·슬라임), 물고기 도감 + 계절별 목록 자동 생성 |
| `kubejs/assets/*/lang/ko_kr.json` + 모드 jar의 `en_us.json` | 모든 모드의 아이템·블록 설명(약 280개, 가구 모드 제외), 영어 이름 별칭 |
| `kubejs/assets/society/lang/ko_kr.json` | 게임 메시지(축산, 해골 광산, 병원비 등) |
| `kubejs/assets/dialog/lang/ko_kr.json` | 마을 NPC 13명의 이름·역할·대사 |
| `kubejs/server_scripts/globalServer.js` | 동전 9종의 가치 (`calculateCoinValue`) |
| `kubejs/data/society_trading/shops` | 상점 판매 목록 → 아이템별 "구매: 마트에서 코인 4에 판매, 봄에만" (가구 카탈로그 제외) |
| `kubejs/data/splendid_slimes/recipes/plort_ripping` | 슬라임 플로트 분해 결과 → "획득: '부패' 슬라임의 플로트를 분해하면 썩은 살점" (이 팩은 지상 몹이 없어 몹 드롭을 여기서 얻음) |
| 농업연감 가축 항목 | 생산품 역색인 → "획득: 가축 '소'에게서 얻음 (젖 짜기)" |
| Serene Seasons 태그 (모드 jar 기본값 − `handleSeasonTags.js` 제거 + 추가) | 작물 제철 → "제철(자라는 계절): 여름" (농업연감에 없는 바닐라 작물 포함) |
| `kubejs/assets/society_skills`, `society_tips` | 스킬 트리, 팁 |
| `config/sereneseasons/*.toml` | 이 서버의 계절 길이, 제철 아닌 작물 처리 방식 |
| `world/level.dat` 게임룰 | 불 번짐, 죽으면 아이템 유지(keepInventory), 팬텀, 수면 비율 등 → "이 서버의 게임 규칙" |
| `local/askhowto/recipes.json` (서버 시작 때 `kubejs/askhowto_recipes.js`가 덤프) | 최종 레시피 약 2만 개 (KubeJS 추가·삭제 반영) → `data/recipes.jsonl` 아이템별 "만드는 법", "재료로 쓰이는 곳". 검색 색인에는 넣지 않는다 |
| `docs/manual/*.md` | 직접 쓴 문서. `#` 제목 하나가 검색 단위 |
| `data/vanilla/ko_kr.json`(Mojang 에셋 서버, SHA1 확인), 서버 jar의 `en_us.json`, 모든 lang 파일 | 이름 사전 `data/names.jsonl` (아이템·블록·몹·효과 약 1만9천 개, 한/영 + ID 별칭) |

상점·슬라임·가축·제철 정보는 아이템별로 모아 "아이템 정보: <이름>" 카드 하나로 만든다(`build_docs.acquisition`).

서버 설정 파일 경로를 안내하는 퀘스트 같은 운영 정보는 빼고 만든다.

**자료 추가하기:** `docs/manual/`에 `.md` 파일을 만들고 `# 제목` 아래에 내용을 쓴 뒤 `python3 -m askhowto build`, 사이드카 재시작.
틀린 답은 `logs/feedback.jsonl`(플레이어가 [도움안됨]을 누른 질문·답·자료)과 `logs/qa.jsonl`(전체 기록)에서 찾는다.

**이름 매칭:** 질문에 나온 이름(한국어, 영어, ID 별칭 — 예: "bank terminal", "Ancient Fruit", "Geode")을 찾아
그 자료를 검색 점수와 상관없이 맨 앞에 `[확정 정보]`로 넣는다. 의도 해석과 답 작성은 그대로 LLM이 한다.
- 자료 조각이 없는 이름(대장장이 작업대, Blackcap Snail 등)은 이름 사전에서 찾아 짧은 카드를 만든다. 바닐라면 "일반 지식으로 답해도 됨",
  모드 것이면 "설명 없음, 지어내지 말 것"으로 표시한다. 영어 이름이면 한국어 이름을 검색어에 더한다.
- 손으로 만든 예외 목록 없이 일반 규칙으로 오인을 막는다: 숫자·'몇/여러' 뒤의 짧은 이름은 단위 명사로 보고('몇 가지', '3개'),
  사전에만 있는 이름이 문서에 20번 넘게 나오면 개념어로 본다('정보', '작물'), 슬라임은 '뼈 슬라임'처럼 이름에 '슬라임'을 붙인다.
- 검색은 한글 2글자 단위 + 1글자 단위(가중치 0.3)이고, 문서·질문 모두 조사를 뗀다('돈은'='돈을', '죽으면'~'죽었을').
  질문에서 알아본 이름이 본문에 그대로 있는 조각은 점수 ×1.5.
- 오타 허용: 정확히 맞는 이름이 없는 3글자 이상 한글 단어는 같은 글자 수의 이름과 자모 단위로 비교해 거리 1 이하면 같은 이름으로 본다
  (초성·중성 차이 1, 받침 차이 0.5 — '낚시대'/'낙시대'→낚싯대, '돌곡굉이'→돌 곡괭이, '물뿌리게'→물뿌리개).
- 제작법 카드: 질문이 만들기·구하기(만들/제작/조합/얻/구해…), 쓰임새(어따/용도/쓸모…), 등급(업그레이드/강화/버전/종류…)을 물으면
  그 말 바로 앞에 나온 이름 하나에 서버 레시피로 만든 카드를 붙인다. 레시피가 없으면 "이 서버에서는 만들 수 없음",
  등급을 물으면 그 이름으로 끝나는 같은 계열('철 물뿌리개' 등)과 각각의 만드는 법. 같은 아이템의 "아이템 정보" 카드가 있으면 거기에 합친다.
  레시피 덤프가 없으면 카드를 만들지 않는다(모르는 것을 '만들 수 없다'고 답하지 않게).
- 자료가 없다는 것과 게임에 없다는 것을 구분한다: 사전 카드는 "게임에 실제로 있는 ~ 모드의 블록"으로 쓰고, 답변 규칙에서 "게임에 없다"고 말하지 못하게 한다.

## 개인화와 도구 (11/03~, 계획: docs/design/plan-2026-11.md, 개념: docs/design/rag-concepts.md)

- **대화 기억** (`memory.py`): 플레이어별 최근 5개 질문·답(답은 200자까지)을 `data/memory/<uuid>.json`에 둔다.
  시간이 지나도 지우지 않고 `/askforget`으로만 지운다. 압축(요약)은 하지 않는다.
  - 5개인 이유: 로컬 Gemma는 프롬프트가 약 6,900자(1,730토큰)를 넘으면 연결을 끊는다(11/03 실측).
    자료 4,000자를 꽉 채운 최악의 경우 대화 6개까지 들어가서 하나 적게 잡았다.
  - 로컬은 프롬프트 전체 6,600자(`PROVIDERS["local"]["max_prompt_chars"]`) 안에 들어가는 만큼만 최근 것부터 넣는다. 클라우드는 5개 모두.
- **답 길이**: 서비스별 `answer_chars`(클라우드 700자, 로컬 450자). 넘치면 문장 단위로 자르고 "…(길어서 여기까지만 보여줘요)"를 붙인다.
- **같은 이름 여러 대상** (`rag.senses_card`): '노움'처럼 한 이름이 마을 NPC·블록·아이템 등을 함께 가리키면 그 사실을 확정 정보 맨 앞에 알린다.
  질문에 '마을·주민·선물·우정' 같은 말이 있으면 NPC 자료를 먼저 둔다.
- **한 번 건너 찾기** (`bot.linked_chunks`, 클라우드만): 상위 위키 자료에 나온 다른 대상(아래 한국어 이름 표시)의 카드를 3개까지 함께 넣는다.
- **가리키는 말** (`bot.referent_names`): 질문에 이름이 없을 때
  - "이거/이것/이건" → 손에 든 아이템
  - "그거/걔/거기/아까" → 직전 대화의 대상
  - 대상 없이 "어떻게 만들어?" → 직전 대화의 대상
  찾은 이름으로 다시 검색하고 이름 매칭을 한다(`qa.jsonl`의 `referred`).
- **플레이어 상태** (`kubejs/askhowto.js` `askhowtoState` → `state.py`): 손에 든 것, 인벤토리 상위 15개, 날씨, 날짜·시각, 계절(바이옴 기준), 바이옴, 차원.
  "질문과 관련 있을 때만 참고"로 넣는다. 좌표·근처 구조물은 넣지 않는다.
  질문에 "지금/현재/여기" 같은 말이 있으면 계절·바이옴 이름도 검색어에 더한다.
- **팀 공유** (KubeJS만): 답이 오면 같은 FTB Teams 파티(혼자인 개인 팀 제외), 없으면 스코어보드 팀의 접속 중인 팀원에게 "OO님의 질문: …"과 같은 답을 보낸다.
  평가 버튼은 질문한 사람만 받는다. `/askteam on|off`로 받기를 끈다(플레이어 데이터에 저장).
- **도구** (`tools.py`, `config.PROVIDERS[...]["tools"]`): NIM·Gemini는 `search_docs`(서버 자료+위키 검색), `read_page`(위키 섹션 읽기)를 쓸 수 있다.
  [자료]로 부족할 때만 쓰게 하고, 질문당 2번·30초까지다. '모른다'고 답하기 전에는 한 번 찾게 한다(`bot.MAX_TOOL_CALLS`, `TOOL_BUDGET_SECONDS`). 호출은 `worker.log`의 `tool:` 줄과 `qa.jsonl`의 `tools`에 남는다.
- **공식 위키** (`wiki.py`): https://sunlitvalley.miraheze.org (영어, CC BY-SA 4.0) 일반 문서 129개를 영어 원문 그대로 섹션 조각으로 넣는다.
  - 넘겨주기, Cobblemon(같은 위키의 다른 팩), 운영 문서, 10만 자 넘는 표 문서는 뺀다.
  - 제목은 lang 파일과 아이템 ID 별칭으로 한국어 이름에 잇는다(106개).
  - 나머지 23개 개념 문서 제목은 LLM이 한 번 붙인 한국어 이름을 쓴다(`data/wiki/title_ko.json`, 손으로 고쳐도 됨).
  - 본문의 영어 아이템·NPC 이름 뒤에 한국어 이름을 붙인다: `Fish Smoker(생선 훈제기)` (`build_docs.annotate_ko`, 한 단어 일반어 제외).
    서버 자료와 같은 대상임을 모델이 알고, 한국어 검색에도 걸린다.
  - 위키는 팩 버전이 다를 수 있어 서버 자료가 우선이다(예: 리빗 오두막 수확 시각, 위키 8시 / 서버 7시).
  - 갱신: `python3 -m askhowto wiki` 다음 `build`.
- **평가 도구**: `tests/eval.py`(93문항), `tests/tools_eval.py`(도구 있음/없음 비교 6문항), `tests/e2e.py`(시나리오).
  조합법 문항은 서버가 덤프한 레시피로 돌린다(`ASKHOWTO_RECIPES_FILE=runs/model-compare/recipes_real.json`).

## 거절 규칙

보안 관련 질문만 거절한다. 게임과 무관한 질문도 일반 지식으로 짧게 답한다(11/03 결정).

| 분류 | 판정 | 답 |
|---|---|---|
| SENSITIVE | 정규식(`guard.py`): 서버 IP/사양/설정 파일, OP·관리자 권한, 해킹·복사 버그, 프롬프트 공개·역할극 요청 | "서버 환경이나 보안에 관한 질문에는 답할 수 없어요…" (거절) |
| CHAT | 정규식: 인사·감탄(30자 이하, 질문 표현 없음), 불평, "너는 누구야" | 정해진 짧은 답 |
| OTHER | 이름 매칭도 없고 검색 점수도 10 미만일 때만 LLM에 "게임 플레이 질문인가? YES/NO"를 묻고, 분명히 NO일 때 | 게임 자료 없이 일반 지식으로 답한다(`bot.GENERAL_SYSTEM`). 게임 이야기일 수도 있으면 선릿 밸리 플레이 관점으로 |
| GAME | 나머지 | 게임 자료(RAG)로 답한다 |

`python3 tests/eval.py` 로 질문(실제 플레이어 질문 포함)의 분류와 답에 들어가야 할 사실을 확인한다(`--no-answer`는 분류만).
'수학 숙제', '비트코인'은 게임 문서의 단어('풀어', '비트')에 걸려 GAME으로 가는 것을 허용했다.

## 설치 / 운영

```bash
./install.sh                     # 문서 생성 + kubejs/server_scripts/askhowto.js, askhowto_recipes.js 복사
# 서버 재시작 (/reload는 이 기기에서 서버를 죽이므로 쓰지 않는다)
# 재시작하면 서버가 local/askhowto/recipes.json을 새로 쓴다 → python3 -m askhowto build 다시 하고 사이드카 재시작
tmux new -d -s askhowto "$PWD/run.sh"   # 사이드카 시작 (죽으면 10초 뒤 재시작, 중복 실행 방지)
```

- 서버 쪽 파일은 `links/`에서 바로 볼 수 있다: `server-queue`(큐 폴더, KubeJS JsonIO가 서버 폴더 안에만 쓸 수 있어 거기 둠),
  `server-script-askhowto.js`(설치된 복사본), `server-kubejs.log`(KubeJS 로그).
- **서버 콘솔에서 `/reload` 금지**: 이 기기에서는 레시피 재처리가 60초를 넘어 워치독이 서버를 죽인다(11/02). KubeJS 스크립트는 `./install.sh` 후 서버 재시작으로 적용.
- 상태 확인: `tmux attach -t askhowto`, `tail -f logs/worker.log`, `cat ../../servers/SocietySunlitValley415A1201/local/askhowto/status.json`
- KubeJS 문법 검사(서버를 켜지 않고): `tests/check_kubejs.sh` (서버 mods의 Rhino로 파싱)
- E2E 시나리오(대화 기억·상태·도구·폴백): `. ./llm.env; python3 tests/e2e.py` (큐는 `runs/e2e-queue/`, 서버 큐와 따로)
- 테스트: `. ./llm.env; python3 -m askhowto ask [--model nim|gemini|local] "질문"` (서버 없이 답변, 답한 서비스·폴백 표시),
  `python3 -m askhowto search "질문"` (검색 결과만)
- 명령어: `/ask`, `/q` (`ASKHOWTO_COMMANDS`), `/askmodel [default|nim|gemini|local]` (탭 자동완성, 인자 없이 쓰면 지금 설정과 클릭 버튼),
  `/askforget` (내 대화 기억 지우기), `/askteam [on|off]` (팀원 질문·답 받기).
  쿨다운(15초)·대기열(5개)·시간 제한(3분)도 `kubejs/askhowto.js`에 있다.
- 사이드카만 고쳤으면 `pkill -f '^python3 -m askhowto serve'` (run.sh가 10초 뒤 새 코드와 llm.env로 다시 띄운다).
  KubeJS 스크립트를 고쳤으면 `./install.sh` 후 서버 재시작.
- 서버 폴더는 환경변수 `ASKHOWTO_SERVER_DIR`로 바꿀 수 있다.
