"""질문 거절 판정. 보안·서버 환경·권한·해킹·프롬프트 탈취는 키워드 규칙으로 거절한다.
LLM 분류(게임 질문인가)는 거절용이 아니라, 게임 자료를 넣어 답할지 일반 지식으로 답할지 고르는 데 쓴다."""
import re

from . import config, llm

REFUSE_SENSITIVE = "서버 환경이나 보안에 관한 질문에는 답할 수 없어요. 필요하면 관리자에게 문의해 주세요."

# 서버 환경(주소, 사양, 설정, 파일), 권한, 해킹/버그 악용, 프롬프트 탈취 시도
SENSITIVE_PATTERNS = [
    # '서버에서 마을 위치 어디야', '서버에 기차 경로 있어?' 같은 게임 질문이 걸리지 않게 게임에도 쓰는 말(위치, 경로,
    # 관리, 로그, 네트워크)은 넣지 않는다.
    r"(서버|호스트|host|server).{0,8}(ip|아이피|주소|포트|port|사양|스펙|램|ram|cpu|\bos\b|운영\s*체제|리눅스|linux|"
    r"윈도우|호스팅|비밀\s*번호|비번|password|백업|backup|폴더|파일|설정\s*파일|config|콘솔|console|"
    r"플러그인|자바|java|메모리|디스크|방화벽|컴퓨터|하드웨어)",
    r"\bip\b|아이피|rcon|ssh|sudo|\broot\b|루트\s*권한|비밀\s*번호|비번|패스워드|password|api\s*(키|key|토큰|token)|"
    r"액세스\s*토큰|access\s*token|\.toml|\.json|\.properties|whitelist|화이트\s*리스트|밴\s*목록|ban\s*list",
    r"\bop\b|\bdeop\b|오피\s*권한|오피\s*줘|관리자\s*권한|운영자\s*권한|권한\s*(줘|주세요|얻|획득)|"
    r"게임\s*모드\s*(바꿔|변경)|크리에이티브\s*(줘|해줘|로\s*바꿔)",
    r"해킹|해커|hack|핵(?=\b|클라|\s*써|\s*쓰|\s*사용)|치트|cheat|x-?ray|엑스레이|듀프|\bdupe|복사\s*버그|아이템\s*복사|"
    r"버그\s*(악용|이용|써서|사용)|exploit|익스플로잇|취약점|디도스|ddos|크래시\s*(시키|내는|나게)|서버\s*(터트|터뜨|다운\s*시키|렉\s*걸)",
    r"프롬프트|prompt|지시\s*(를|사항을?)?\s*무시|규칙\s*(을)?\s*무시|ignore\s+(all|previous|the)|jailbreak|탈옥|"
    r"개발자\s*모드|developer\s*mode|너는\s*이제|역할\s*극|무슨\s*모델|어떤\s*모델|모델\s*이름|\bllm\b|\bgpt\b|gemma|"
    r"너의?\s*(설정|규칙|지침|명령)|(주어진|받은|정해진)\s*(규칙|지시|지침|명령)|"
    r"(규칙|지시|지침)\s*(을|를)?\s*(전부|다|모두)?\s*(말해|알려|보여|출력|공개)",
    # 역할·말투를 바꾸려는 요청
    r"지금부터\s*(너는|넌)|이제부터\s*(너는|넌)|처럼\s*(말해|대답|행동|굴어)|말투로|(체|투)로\s*(말해|대답)|act\s+as|pretend",
]
SENSITIVE_RE = re.compile("|".join(f"(?:{p})" for p in SENSITIVE_PATTERNS), re.I)

# 2B 모델은 GAME/OTHER/SENSITIVE 3분류에서 거의 모든 질문을 GAME으로 골랐다(실제 로그: 롤 티어, GPU/CPU 통과).
# '게임 플레이 질문인가' 하나만 YES/NO로 물으면 무관한 질문을 훨씬 잘 걸러낸다. 민감한 질문은 정규식이 맡는다.
CLASSIFY_SYSTEM = """너는 마인크래프트 서버 도우미의 질문 필터다. 플레이어는 게임 채팅으로 질문한다.
질문이 마인크래프트(모드 포함) 게임 플레이에 관한 것인지 판단해 YES 또는 NO 한 단어로만 답한다.
게임 안의 블록, 아이템, 작물, 요리, 몹, 동물, 마을 주민, 돈(코인), 농사, 낚시, 광산, 기계, 퀘스트, 조작법에 대한 질문이면 YES.
현실 세계의 지식(컴퓨터 부품, 경제, 언어, 학교 공부, 음식 추천), 다른 게임(롤, 발로란트 등)이면 NO.
게임 속 이야기일 수도 있으면 YES. 확실히 게임과 무관할 때만 NO."""

CLASSIFY_EXAMPLES = [
    ("겨울에 심을 수 있는 작물 알려줘", "YES"),
    ("다른 게임 랭크 올리는 법", "NO"),
    ("배송 상자는 언제 정산돼?", "YES"),
    ("램이랑 SSD 차이", "NO"),
    ("크리퍼 잡는 법", "YES"),
    ("저녁 메뉴 추천", "NO"),
    ("같이 하면 재밌는 거 추천해줘", "YES"),
]

CHAT_REPLY = "고마워요! 선릿 밸리에서 궁금한 게 생기면 언제든 /ask 로 물어봐 주세요."
INTRO_REPLY = "저는 선릿 밸리 도우미예요. 작물, 계절, 낚시, 가축, 돈 벌기처럼 게임에 관한 걸 /ask 로 물어봐 주세요."
INTRO_RE = re.compile(r"(너|넌|당신)\s*(는|은)?\s*(누구|뭐야|뭐니|정체)|(너|네|니)\s*이름|자기\s*소개|(넌|너는?)\s*뭘\s*할\s*수")
CHAT_RE = re.compile(r"^[\sㅋㅎㅠㅜ!?.~^]*$|ㅋㅋ|ㅎㅎ|히히|헤헤|하하|^안녕|^ㅎㅇ|^하이|고마워|고맙|감사|땡큐|thx|thank|"
                     r"재밌|재미있|귀엽|귀여워|사랑해|잘했", re.I)
# 잡담처럼 시작해도 이런 말이 있으면 질문으로 본다('안녕, 밀 언제 심어?').
QUESTION_RE = re.compile(r"\?|뭐|무엇|어떻게|어디|언제|왜|얼마|몇|알려|추천|방법|하는\s*법")


def rule_check(question):
    return "SENSITIVE" if SENSITIVE_RE.search(question) else None


COMPLAINT_REPLY = ("아쉬워요. 아이템이나 작물 이름을 넣어 다시 물어봐 주시거나, 도움이 안 된 답 아래 [도움안됨]을 눌러 주시면 "
                   "모아서 고칠게요.")
# 도우미 답에 대한 불평(질문이 아님)
COMPLAINT_RE = re.compile(r"도움이?\s*(정말|하나도|진짜|별로)?\s*(안|않|못)|쓸모\s*(없|없네)|엉터리|멍청|바보|답답|"
                          r"이상한\s*(소리|답)|(틀렸|틀린\s*답)|못\s*알아")


def chat_reply(question):
    """인사·감탄·불평 같은 잡담이면 정해진 짧은 답. LLM에 넘기면 지시문을 흘리거나 아무 말이나 했다."""
    if INTRO_RE.search(question):
        return INTRO_REPLY
    if len(question) <= 40 and COMPLAINT_RE.search(question):
        return COMPLAINT_REPLY
    if len(question) <= 30 and CHAT_RE.search(question) and not QUESTION_RE.search(question):
        return CHAT_REPLY
    return None


def classify(question, provider):
    """GAME 또는 OTHER. provider: config.PROVIDERS 항목."""
    messages = [{"role": "system", "content": CLASSIFY_SYSTEM}]
    for q, label in CLASSIFY_EXAMPLES:
        messages += [{"role": "user", "content": q}, {"role": "assistant", "content": label}]
    messages.append({"role": "user", "content": question})
    # 추론 모델이면 생각하느라 3토큰을 다 써서 빈 답이 온다. 분류는 추론 없이 부르고,
    # 추론을 끌 수 없는 모델이면 생각할 토큰을 넉넉히 준다.
    reasoning = provider["classify_reasoning"]
    # Gemini는 추론을 꺼도 출력 3토큰이면 빈 답을 낸다(11/03). 20토큰이면 YES/NO가 온다.
    out = llm.chat(messages, provider, max_tokens=400 if reasoning not in ("", "none") else 20, temperature=0,
                   reasoning=reasoning).strip().upper()
    # 'YES' 대신 '광산'처럼 엉뚱하게 답하는 경우가 있어서, 분명히 NO일 때만 거절한다.
    return "OTHER" if "NO" in out else "GAME"
