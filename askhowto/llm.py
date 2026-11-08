"""OpenAI 호환 /v1/chat/completions 클라이언트 (표준 라이브러리만 사용). 서비스 설정은 config.PROVIDERS."""
import json
import time
import urllib.error
import urllib.request

from . import config


class LLMError(Exception):
    """kind: 'limit'(사용 한도·요청 과다), 'timeout', 'busy'(503), 'server'(그 외 5xx), 'auth', 'nokey', 'network',
    'bad'(그 외 4xx), 'empty'(빈 답)"""

    def __init__(self, message, kind="network"):
        super().__init__(message)
        self.kind = kind


KIND_KO = {"limit": "사용 한도 초과", "timeout": "시간 초과", "server": "서버 오류", "auth": "인증 실패",
           "nokey": "API 키 없음", "network": "연결 실패", "bad": "요청 거부", "empty": "빈 답", "busy": "서버 혼잡"}

# 지금까지 쓴 토큰 (worker가 요청마다 차이를 기록해 비용을 본다)
USAGE = {"calls": 0, "prompt": 0, "completion": 0}
# 마지막으로 답한 모델 (예비 모델이 답했을 수 있다. 로그에 실제 모델을 적으려고)
LAST = {"model": None, "truncated": False}
# 이번 질문의 LLM 호출마다 (모델, 걸린 초, 결과). 어느 호출이 느린지 로그로 보려고(11/07).
CALLS = []


def chat(messages, provider, max_tokens=None, temperature=0.2, reasoning=None):
    """provider: config.PROVIDERS의 항목 -> 답 문자열. 실패하면 LLMError(kind로 원인)."""
    return message(messages, provider, max_tokens, temperature, reasoning).get("content") or ""


def message(messages, provider, max_tokens=None, temperature=0.2, reasoning=None, tools=None, tool_choice=None):
    """답 메시지 전체(dict)를 돌려준다. tools를 주면 tool_calls가 들어 있을 수 있다.
    Gemini는 tool_calls에 붙은 서명(extra_content)을 다음 요청에 그대로 돌려줘야 해서 메시지를 고치지 않고 넘긴다."""
    if provider["needs_key"] and not provider["key"]:
        raise LLMError(f"{provider['name']}: API 키 없음", "nokey")
    payload = {
        "model": provider["model"],
        "messages": messages,
        "max_tokens": max_tokens or provider["max_tokens"],
        "temperature": temperature,
    }
    reasoning = provider["reasoning"] if reasoning is None else reasoning
    if reasoning:
        payload["reasoning_effort"] = reasoning
    if tools:
        payload["tools"] = tools
        if tool_choice:
            payload["tool_choice"] = tool_choice
    headers = {"Content-Type": "application/json"}
    if provider["key"]:
        headers["Authorization"] = "Bearer " + provider["key"]
    models = [provider["model"]] + provider.get("backup_models", [])
    for i, model in enumerate(models):
        try:
            msg = _request(provider, dict(payload, model=model), headers)
            # 빈 답(11/04 Gemini)도 예비 모델이 있으면 다시 해 본다. 마지막 모델의 빈 답은 부른 쪽이 판단한다.
            if i + 1 < len(models) and not (msg.get("content") or "").strip() and not msg.get("tool_calls"):
                raise LLMError(f"{model}: 빈 답", "empty")
            LAST["model"] = model
            return msg
        except LLMError as err:
            # 한도·혼잡·시간 초과·빈 답은 모델마다 따로라 예비 모델로 다시 해 본다. 그 밖의 오류는 예비 모델도 같다.
            if i + 1 < len(models) and err.kind in ("limit", "busy", "timeout", "empty"):
                continue
            raise


def _request(provider, payload, headers):
    body = json.dumps(payload, ensure_ascii=False).encode()
    last = None
    for attempt in range(provider["retries"]):
        started = time.time()
        try:
            req = urllib.request.Request(provider["url"] + "/chat/completions", data=body, headers=headers)
            with urllib.request.urlopen(req, timeout=provider["timeout"]) as r:
                data = json.loads(r.read())
            usage = data.get("usage") or {}
            USAGE["calls"] += 1
            USAGE["prompt"] += usage.get("prompt_tokens") or 0
            USAGE["completion"] += usage.get("completion_tokens") or 0
            # 출력 한도에 걸려 끊긴 답인지 (부른 쪽이 '잘림' 표시를 붙인다)
            LAST["truncated"] = data["choices"][0].get("finish_reason") == "length"
            CALLS.append([payload["model"], round(time.time() - started, 1), "ok"])
            return data["choices"][0]["message"]
        except urllib.error.HTTPError as e:
            detail = e.read()[:200].decode("utf-8", "replace")
            kind = ("limit" if e.code in (402, 429) else "auth" if e.code in (401, 403)
                    else "busy" if e.code == 503 else "server" if e.code >= 500 else "bad")
            last = LLMError(f"HTTP {e.code}: {detail}", kind)
            # 한도·인증·잘못된 요청은 다시 보내도 같다. 다음 서비스로 바로 넘어간다.
            if e.code < 500:
                break
        except (TimeoutError, OSError) as e:
            # urllib은 읽기 시간 초과를 TimeoutError 또는 'timed out' URLError로 낸다.
            # 로컬 Gemma는 바쁘면 요청을 받자마자 연결을 끊는다(11/03: 0.1초 만에 끊고 몇 초 뒤엔 받음).
            kind = "timeout" if isinstance(e, TimeoutError) or "timed out" in str(e) else "network"
            last = LLMError(f"{type(e).__name__}: {e}", kind)
        except (ValueError, KeyError) as e:
            last = LLMError(f"{type(e).__name__}: {e}", "server")
        CALLS.append([payload["model"], round(time.time() - started, 1), last.kind])
        if attempt + 1 < provider["retries"]:
            time.sleep(provider.get("retry_wait", 1) * (attempt + 1))
    raise last
