"""KubeJS(askhowto.js)와 파일 큐로 통신하는 상주 프로세스.

requests/<id>.json  (KubeJS가 씀)  {id, player, uuid, question, time, model, state, action}
                                   model: /askmodel 선택 (default, nim, gemini, local), state: 플레이어 상태(state.py),
                                   action: "forget"이면 질문 대신 그 플레이어의 대화 기억을 지운다(/askforget)
responses/<id>.json (여기서 씀)    {id, uuid, label, text, sources, notice, provider}  text·sources는 줄바꿈으로 이은 문자열,
                                   notice: 서비스 실패로 다음 서비스가 답했을 때의 안내(채팅에 회색으로)
feedback/<id>.json  (KubeJS가 씀)  {id, player, vote}  답 아래 [도움됨]/[도움안됨] 클릭
progress/<id>.json  (여기서 씀)    {notices}  처리 중 서비스가 실패할 때마다 덧붙이는 안내(줄바꿈으로 이은 문자열). 답을 쓰면 지운다
status.json         (여기서 씀)    {time, busy, queue} — KubeJS가 사이드카 생존 여부를 판단하는 하트비트
"""
import json
import os
import subprocess
import sys
import time
import traceback
from collections import OrderedDict

from . import bot, config, llm, memory, state

RESPONSE_TTL = 600  # KubeJS가 가져가지 않은 응답(플레이어 퇴장 등)은 10분 뒤 지운다.
BAD_REQUEST_TTL = 10  # 쓰는 도중이라 깨진 파일은 잠시 기다렸다가, 계속 깨져 있으면 버린다.
RECENT_QA = 200  # 피드백을 질문·답과 묶어 기록하려고 최근 답을 기억해 둔다.

recent = OrderedDict()


def log(msg):
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def heartbeat(busy, queue):
    write_json(config.STATUS_FILE, {"time": int(time.time() * 1000), "busy": busy, "queue": queue})


def pending_requests():
    files = sorted(config.REQUEST_DIR.glob("*.json"), key=lambda p: p.name)
    out = []
    for path in files:
        try:
            req = json.loads(path.read_text(encoding="utf-8"))
            if req.get("id") and req.get("question"):
                out.append((path, req))
                continue
        except (OSError, ValueError):
            pass
        if time.time() - path.stat().st_mtime > BAD_REQUEST_TTL:
            path.unlink(missing_ok=True)
    return out


def cleanup_responses():
    now = time.time()
    for path in [*config.RESPONSE_DIR.glob("*.json"), *config.PROGRESS_DIR.glob("*.json")]:
        if now - path.stat().st_mtime > RESPONSE_TTL:
            path.unlink(missing_ok=True)


def append_jsonl(path, record):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def handle(path, req, queue_left):
    heartbeat(True, queue_left)
    path.unlink(missing_ok=True)
    start = time.time()
    before = dict(llm.USAGE)
    if req.get("action") == "forget":
        memory.forget(req.get("uuid"))
        write_json(config.RESPONSE_DIR / f"{req['id']}.json", {
            "id": req["id"], "uuid": req.get("uuid"), "label": "CHAT", "text": "대화 기억을 지웠어요.", "sources": ""})
        log(f"{req.get('player')} 대화 기억 지움")
        return
    choice = str(req.get("model") or "default")
    if choice not in config.MODEL_CHOICES:
        choice = "default"
    history = memory.load(req.get("uuid"))
    player_state = state.describe(req.get("state"), bot.index())
    progress = []

    def on_failure(notice, name, err):
        # 실패할 때마다 바로 로그와 진행 파일에 남긴다. KubeJS가 진행 파일을 읽어 채팅으로 바로 보여준다.
        log(f"fallback: {name} 실패 ({err.kind}) {' '.join(str(err).split())[:150]}")
        progress.append(notice)
        write_json(config.PROGRESS_DIR / f"{req['id']}.json", {"notices": "\n".join(progress)})

    res = bot.answer(str(req["question"]), choice, history, player_state, on_failure)
    (config.PROGRESS_DIR / f"{req['id']}.json").unlink(missing_ok=True)
    if res["label"] in ("GAME", "OTHER", "NOTFOUND"):
        memory.remember(req.get("uuid"), str(req["question"]), res["lines"], res.get("names", []))
    elapsed = time.time() - start
    provider = config.PROVIDERS.get(res.get("provider") or "", {})
    write_json(config.RESPONSE_DIR / f"{req['id']}.json", {
        "id": req["id"], "uuid": req.get("uuid"), "label": res["label"],
        "text": "\n".join(res["lines"]), "sources": "\n".join(res["sources"]),
        # notices: 실패 안내 전체(진행 파일로 이미 보여준 것은 KubeJS가 건너뛴다). notice는 예전 KubeJS용 한 줄 요약.
        "notices": "\n".join(res.get("notices", [])),
        "notice": res.get("notice", ""), "provider": provider.get("label", ""),
        # items: 답에 나온 아이템 "표기\tID" 줄들 (KubeJS가 이름에 마우스를 올리면 툴팁이 뜨게 한다)
        # unused: 넣어 준 자료 중 답에 안 쓴 것의 제목들
        "items": "\n".join(f"{name}\t{item_id}" for name, item_id in res.get("items", [])),
        "unused": "\n".join(res.get("unused", [])),
    })
    for t in res.get("tools", []):
        log(f"tool: {t['name']} {str(t['arguments'])[:120]} -> {t['chars']}자")
    log(f"{req.get('player')} [{res['label']} {res.get('provider') or '-'} {elapsed:.0f}s] {req['question']!r}"
        f" -> {' / '.join(res['lines'])[:120]}")
    record = {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": req["id"], "player": req.get("player"),
        "question": req["question"], "label": res["label"], "answer": res["lines"],
        "sources": res["refs"], "seconds": round(elapsed, 1),
        "choice": choice, "provider": res.get("provider"), "model": res.get("model"), "fallbacks": res.get("fallbacks", []),
        "tools": res.get("tools", []), "referred": res.get("referred", []), "memory_turns": len(history),
        # LLM 호출마다 [모델, 초, 결과] (어디가 느린지 보려고)
        "calls": llm.CALLS[:],
        "state": player_state["text"] if player_state else None,
        # 이 질문에 쓴 토큰 (분류 + 답변). 월 사용량·비용 확인용
        "tokens": {k: llm.USAGE[k] - before[k] for k in ("calls", "prompt", "completion")},
    }
    append_jsonl(config.LOG_FILE, record)
    recent[req["id"]] = record
    while len(recent) > RECENT_QA:
        recent.popitem(last=False)


def collect_feedback():
    for path in config.FEEDBACK_DIR.glob("*.json"):
        try:
            fb = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            if time.time() - path.stat().st_mtime > BAD_REQUEST_TTL:
                path.unlink(missing_ok=True)
            continue
        path.unlink(missing_ok=True)
        qa = recent.get(fb.get("id"), {})
        append_jsonl(config.FEEDBACK_LOG_FILE, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"), "id": fb.get("id"), "player": fb.get("player"),
            "vote": fb.get("vote"), "question": qa.get("question"), "answer": qa.get("answer"),
            "sources": qa.get("sources"),
        })
        log(f"feedback {fb.get('vote')} from {fb.get('player')}: {qa.get('question')!r}")


# 서버가 재시작하면서 새로 쓴 덤프(레시피·판매가·장인 설비)가 자료보다 새로우면 자료를 다시 만들고 색인을 다시 읽는다.
# 11/06 판매가 덤프가 생겼는데 아무도 build를 안 해서 하루 넘게 자료에 안 들어갔다.
REBUILD_CHECK_INTERVAL = 300
REBUILD_SETTLE_SECONDS = 90  # 서버가 덤프를 차례로 쓰는 중일 수 있어서, 마지막으로 쓴 뒤 이만큼 지나면 만든다
rebuild = {"proc": None, "tried": None, "checked": 0}


def dump_files():
    """자료의 원본 중 바뀔 수 있는 것: 서버 덤프 파일들과 KubeJS 시작 스크립트(설비 레시피·판매가 표)."""
    files = [p for p in (config.RECIPES_FILE, config.PRICES_FILE) if p.exists()]
    return files + list((config.SERVER_DIR / "kubejs" / "startup_scripts").rglob("*.js"))


def check_rebuild():
    """쉬는 동안 불린다. 다시 만들 때가 되면 build를 따로 띄우고, 끝나면 색인을 다시 읽는다."""
    proc = rebuild["proc"]
    if proc is not None:
        if proc.poll() is None:
            return
        rebuild["proc"] = None
        if proc.returncode == 0:
            bot.reload_index()
            bot.index()
            log("자료 다시 만듦 -> 색인 다시 읽음")
        else:
            log(f"자료 다시 만들기 실패 (exit {proc.returncode}), logs/build.log 확인")
        return
    if time.time() - rebuild["checked"] < REBUILD_CHECK_INTERVAL:
        return
    rebuild["checked"] = time.time()
    built = config.CHUNKS_FILE.stat().st_mtime if config.CHUNKS_FILE.exists() else 0
    newest = max((p.stat().st_mtime for p in dump_files()), default=0)
    if newest <= built or time.time() - newest < REBUILD_SETTLE_SECONDS or rebuild["tried"] == newest:
        return
    rebuild["tried"] = newest  # 실패해도 같은 덤프로 계속 다시 하지 않는다
    log("서버 덤프가 자료보다 새로움 -> 자료 다시 만드는 중")
    out = open(config.ROOT / "logs" / "build.log", "w")
    rebuild["proc"] = subprocess.Popen([sys.executable, "-m", "askhowto", "build"], cwd=config.ROOT,
                                       stdout=out, stderr=subprocess.STDOUT)


def serve():
    for d in (config.REQUEST_DIR, config.RESPONSE_DIR, config.FEEDBACK_DIR, config.PROGRESS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    bot.index()
    chain = " -> ".join(f"{p['name']}({p['model']}{'' if p['key'] or not p['needs_key'] else ', 키 없음'})"
                        for p in bot.provider_chain("default"))
    log(f"askhowto worker ready: {config.QUEUE_DIR}, default: {chain}")
    last_beat = 0
    while True:
        reqs = pending_requests()
        if reqs:
            # 한 번에 하나씩 처리하되, 처리마다 새 요청을 다시 읽어 순서를 지킨다.
            path, req = reqs[0]
            try:
                handle(path, req, len(reqs) - 1)
            except Exception:  # noqa: BLE001
                # 예상 못 한 오류로 사이드카가 죽으면 플레이어는 시간 제한(3분)까지 기다리게 된다(11/03).
                # 기록을 남기고 바로 오류 답을 보낸 뒤 다음 요청을 계속 처리한다.
                log("요청 처리 오류: " + traceback.format_exc().replace("\n", " | ")[-1500:])
                path.unlink(missing_ok=True)
                (config.PROGRESS_DIR / f"{req['id']}.json").unlink(missing_ok=True)
                write_json(config.RESPONSE_DIR / f"{req['id']}.json", {
                    "id": req["id"], "uuid": req.get("uuid"), "label": "ERROR",
                    "text": "도우미에 오류가 생겼어요. 잠시 후 다시 물어봐 주세요.", "sources": ""})
            last_beat = 0
            continue
        if time.time() - last_beat >= config.HEARTBEAT_INTERVAL:
            heartbeat(False, 0)
            cleanup_responses()
            collect_feedback()
            check_rebuild()
            last_beat = time.time()
        time.sleep(config.POLL_INTERVAL)
