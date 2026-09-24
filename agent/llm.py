"""NVIDIA API(OpenAI 호환) 클라이언트 래퍼.

integrate.api.nvidia.com 은 500/503(Service temporarily overloaded)을 산발적으로 반환한다.
실측 기준 체감 20% 수준이므로 지수 백오프 재시도를 명시적으로 구현하고,
재시도 횟수를 호출자에게 돌려줘서 데모 타임라인에 표시할 수 있게 한다.
"""
from __future__ import annotations

import random
import time
from typing import Any, Dict, List, Optional, Tuple

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI

from . import config

_client: Optional[OpenAI] = None
# 재시도 횟수 누적(데모 표시용). llm.RETRY_LOG 로 접근.
RETRY_LOG: List[str] = []
# 호출별 토큰 사용량 누적(데모 표시용).
USAGE_LOG: List[Dict[str, Any]] = []


def reset_logs() -> None:
    RETRY_LOG.clear()
    USAGE_LOG.clear()


def usage_total() -> Dict[str, int]:
    total = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "calls": 0}
    for u in USAGE_LOG:
        for k in ("prompt_tokens", "completion_tokens", "total_tokens"):
            total[k] += int(u.get(k) or 0)
        total["calls"] += 1
    return total


def client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(
            base_url=config.BASE_URL,
            api_key=config.get_api_key(),
            timeout=config.REQUEST_TIMEOUT,
            max_retries=0,  # 재시도는 아래에서 직접 제어
        )
    return _client


def _retryable(exc: Exception) -> bool:
    if isinstance(exc, (APIConnectionError, APITimeoutError)):
        return True
    if isinstance(exc, APIStatusError):
        return exc.status_code in (408, 409, 429, 500, 502, 503, 504)
    return False


def _with_retry(label: str, fn):
    last: Optional[Exception] = None
    for attempt in range(config.MAX_RETRIES):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            if not _retryable(exc):
                raise
            last = exc
            if attempt == config.MAX_RETRIES - 1:
                break
            delay = min(15.0, 1.5 * (2 ** attempt)) + random.uniform(0, 0.8)
            RETRY_LOG.append(f"{label}: {type(exc).__name__} → {delay:.1f}s 후 재시도")
            time.sleep(delay)
    raise RuntimeError(f"{label} 호출이 {config.MAX_RETRIES}회 모두 실패했습니다: {last}") from last


def chat(
    model: str,
    messages: List[Dict[str, Any]],
    tools: Optional[List[Dict[str, Any]]] = None,
    temperature: float = 0.3,
    max_tokens: int = 1200,
) -> Any:
    """chat.completions 1회 호출. 응답 message 객체를 그대로 반환."""

    def _call():
        kwargs: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = "auto"
        return client().chat.completions.create(**kwargs)

    resp = _with_retry(f"chat[{model.split('/')[-1]}]", _call)
    if getattr(resp, "usage", None):
        USAGE_LOG.append(
            {
                "model": model,
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            }
        )
    return resp.choices[0].message


def embed(texts: List[str], input_type: str) -> List[List[float]]:
    """nemotron-3-embed-1b 임베딩. input_type 은 'query' 또는 'passage'."""
    assert input_type in ("query", "passage")

    def _call():
        return client().embeddings.create(
            model=config.MODEL_EMBED,
            input=texts,
            encoding_format="float",
            extra_body={"input_type": input_type, "truncate": "END"},
        )

    resp = _with_retry(f"embed[{input_type}]", _call)
    if getattr(resp, "usage", None):
        USAGE_LOG.append(
            {
                "model": config.MODEL_EMBED,
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
                "completion_tokens": 0,
                "total_tokens": getattr(resp.usage, "total_tokens", 0),
            }
        )
    return [d.embedding for d in resp.data]
