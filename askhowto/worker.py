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
            last_beat = time.time()
        time.sleep(config.POLL_INTERVAL)
