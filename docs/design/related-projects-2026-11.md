# 비슷한 AI 도우미 프로젝트 조사 (2026-11-03)

서브에이전트 조사(CurseForge, Modrinth, GitHub, Hangar, BuiltByBit)를 정리한 것이다. ARR 프로젝트는 소스가 없어 기능 소개 글만 근거로 했다.

## 결론
- 서버 쪽에서 돌고, 모드팩 자체 자료로 RAG를 하고, 한국어로 답하는 프로젝트는 찾지 못했다.
- 가장 비슷한 것은 Modpack AI Assistant(MIT)인데 클라이언트 전용이고 1.20.1 Forge가 없다.
- **플랫폼은 옮기지 않고, 아이디어 몇 가지만 가져온다.**

## 후보

| 프로젝트 | 로더·버전 | 라이선스 | 최근 | 구조 | 눈여겨볼 점 |
|---|---|---|---|---|---|
| [Modpack AI Assistant](https://github.com/skps00/Modpack-AI-Assistant) | Forge 1.19.2 (1.20.1 없음) | MIT | 2026-10-28 | 클라이언트 전용, 플레이어마다 키 | JEI 레시피·퀘스트·전리품·몹 드롭·주민 거래·광물 높이 자료, 채팅 레시피 카드, "답에 안 넣은 관련 자료" 줄, 아이템에 마우스 올리고 Y로 질문 |
| [Artificial Intelligence (AI)](https://www.curseforge.com/minecraft/mc-mods/artificial-intelligence-ai) | Forge 1.20.1 / 1.21.1 | ARR | 2026-09-29 | 클라이언트 전용 | 스트리밍 GUI, 제작 애니메이션, Minecraft Wiki 보강. 퀘스트·서버 자료 없음 |
| [Nexa AI](https://modrinth.com/mod/nexa-ai) | Fabric/Forge/NeoForge/Paper 1.20.x | ARR | 약 2개월 전 | 서버만 또는 서버+클라이언트 | 플레이어별 요청 한도, 관리자 권한(LuckPerms), 위험한 명령 실행 전 확인. 문서 RAG 없음 |
| [MineAgent](https://github.com/ControlNet/MineAgent) | Forge/Fabric 1.20.1 | AGPL-3.0 | 2026-06-15, 알파 | 클라이언트·서버, 인게임 터미널 | 레시피 조회(만드는 법·쓰이는 곳)를 도구로, `mcp.json`으로 외부 MCP 도구 연결 |
| [NotchNet](https://github.com/aaravchour/NotchNet) + mod | Forge/Fabric 1.20.1 외 | MIT(mod는 README에만) | 2026-07-20 | Python 백엔드 + 클라이언트 모드 (우리와 가장 비슷한 구조) | MediaWiki 위키 색인, Ollama만 지원 |
| [LLMjs](https://www.curseforge.com/minecraft/mc-mods/llmjs), [Chat.JS](https://github.com/gizmo-ds/chatjs-mod) | Forge 1.20.1 | MIT / LGPL-3.0 | 2025 | KubeJS에서 LLM 호출 | RAG는 대신할 수 없음 |
| [ServerAssistantAI](https://github.com/CodeSolutionsDev/ServerAssistantAI) | Paper/Spigot + Discord | 확인 못 함 (유료로 보임) | – | 서버 플러그인 + Discord 봇 | 문서 폴더 임베딩 검색, 질문 자동 감지, Discord 채널에서 답하기 |

## (a) 가져올 것 (가치 높은 순)

| 순서 | 할 일 | 출처 | 작업량 |
|---|---|---|---|
| 1 | 레시피를 아이템 ID로 바로 찾는 도구 `get_recipe` / `get_uses` (지금은 이름이 질문에 나올 때만 카드로 붙음) | MineAgent, Modpack AI | 1~2일 |
| 2 | 답에 나온 아이템 이름에 마우스를 올리면 실제 툴팁이 뜨게 하고(`show_item` 호버), 재료 목록은 `[레시피 보기]`로 접어 두기 | Modpack AI | 1~2일 (KubeJS) |
| 3 | 고정한 자료 중 답에 쓰이지 않은 것을 회색 한 줄로 보여주기 (환각 확인, [도움안됨] 분석에 도움) | Modpack AI | 반나절 |
| 4 | 자료 늘리기: 전리품 테이블, 몹 드롭, 주민·상인 거래, 광물 생성 높이 (모드 jar의 `data/*/loot_tables`, worldgen JSON) | Modpack AI | 2~4일 |
| 5 | 플레이어별 분당 요청 한도, 관리자 권한 분리 (지금은 플레이어당 15초 쿨다운과 전체 대기열 5개뿐) | Nexa | 반나절 |
| 6 | Discord에서도 같은 파이프라인으로 답하기 (플레이어 상태 자료는 빼고) | ServerAssistantAI | 2~5일, 선택 |
| 7 | 임베딩 혼합 검색: 빌드 때 클라우드 임베딩으로 벡터를 미리 계산하고, 클라우드가 될 때만 BM25에 더함 | NotchNet, ServerAssistantAI | 측정 후 결정 |
| 보류 | 스트리밍, 인게임 GUI, 마우스 올리고 단축키로 질문 (클라이언트 모드가 필요) | | |

라이선스: MIT는 코드를 가져와도 된다. LGPL은 라이브러리로 쓰는 것만, AGPL과 ARR은 아이디어만 가져온다.

## (b) 우리 것을 넘기거나 옮기기

| 선택지 | 장점 | 단점 | 판단 |
|---|---|---|---|
| Modpack AI Assistant로 옮기기 | MIT, 철학이 가장 비슷함, JEI·퀘스트 처리가 이미 있음 | 1.20.1 포팅 필요, 클라이언트 전용이라 서버에서 키·폴백 관리 불가, 서버 고유 자료(상점·계절·레시피 덤프)를 잃음 | 권하지 않음 (3~6주) |
| Modpack AI에 기여 | 한글 2-gram BM25(중국어에도 유효), 한/영 이름 연결, 폴백 체인 | Java로 다시 써야 함 | 먼저 issue로 제안 |
| NotchNet에 기여 | Python이라 수집기·검색 코드를 거의 그대로 옮길 수 있음 | 사용자가 매우 적음 | 이득 작음 |
| 사이드카를 MCP 서버로 공개 | MCP를 받는 모드나 데스크톱 AI가 우리 팩 지식을 쓸 수 있음, 우리 구조는 그대로 | MCP를 쓰는 MC 모드는 아직 알파 | 여유 있을 때 (2~3일) |
| LLMjs/Chat.JS로 파일 교환 대체 | 교환 단계가 하나 줄어듦 | RAG는 여전히 사이드카가 필요, 의존성만 늘어남 | 권하지 않음 |

## 확인하지 못한 것
- Nexa AI와 MineAgent의 정확한 업데이트 날짜.
- ServerAssistantAI의 가격·라이선스.
- Modpack AI Assistant가 임베딩을 쓰는지(README에 없음).
