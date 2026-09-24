"""에이전트가 호출할 수 있는 툴 구현 + OpenAI 함수 스키마.

메인 모델(nemotron-3-ultra)이 tool calling 으로 '어떤 툴을 언제 쓸지' 스스로 결정한다.
여기서는 각 툴의 실제 동작만 제공한다.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from . import config, llm

# ---------------------------------------------------------------- 안전 필터

_SAFE_RE = re.compile(r"(User|Response)\s*Safety:\s*(safe|unsafe)", re.IGNORECASE)


def _parse_safety(raw: str) -> Dict[str, Optional[str]]:
    out: Dict[str, Optional[str]] = {"user_safety": None, "response_safety": None}
    for who, verdict in _SAFE_RE.findall(raw or ""):
        key = "user_safety" if who.lower() == "user" else "response_safety"
        out[key] = verdict.lower()
    return out


def check_safety(text: str, kind: str = "review", review_context: str = "") -> Dict[str, Any]:
    """nemotron-3.5-content-safety 로 텍스트를 판정한다.

    kind="review"       : 들어온 리뷰 자체를 판정 (User Safety)
    kind="draft_reply"  : 우리가 쓴 답글 초안을 판정 (Response Safety)
                          -> 리뷰를 user, 초안을 assistant 로 넣어야 Response Safety 가 나온다.
    """
    kind = kind if kind in ("review", "draft_reply") else "review"
    if kind == "draft_reply":
        messages = [
            {"role": "user", "content": review_context or "(원문 리뷰 없음)"},
            {"role": "assistant", "content": text},
        ]
    else:
        messages = [{"role": "user", "content": text}]

    msg = llm.chat(config.MODEL_SAFETY, messages, temperature=0.0, max_tokens=64)
    raw = (msg.content or "").strip()
    parsed = _parse_safety(raw)
    verdict = parsed["response_safety"] if kind == "draft_reply" else parsed["user_safety"]
    verdict = verdict or "unknown"

    if kind == "review":
        note = (
            "unsafe = 폭언/위협/심각한 컴플레인 신호. 답글 자동 게시를 막는 것이 아니라 "
            "사람(매니저) 개입이 필요한 건으로 분류하라는 뜻이다."
            if verdict == "unsafe"
            else "일반 리뷰로 처리 가능."
        )
    else:
        note = (
            "답글 초안이 부적절로 판정되었다. 표현을 순화해 다시 작성하고 재검사하라."
            if verdict == "unsafe"
            else "답글 초안 게시 가능."
        )

    return {
        "kind": kind,
        "verdict": verdict,
        "raw": raw,
        "model": config.MODEL_SAFETY,
        "note": note,
    }


# ---------------------------------------------------------------- 정책 검색(임베딩)


def _chunk_policy() -> List[Dict[str, str]]:
    text = config.POLICY_PATH.read_text(encoding="utf-8")
    chunks: List[Dict[str, str]] = []
    current_title = "머리말"
    buf: List[str] = []

    def flush():
        body = "\n".join(buf).strip()
        if body:
            chunks.append({"title": current_title, "text": body})

    for line in text.splitlines():
        if line.startswith("## "):
            flush()
            buf = []
            current_title = line[3:].strip()
        else:
            buf.append(line)
    flush()
    return chunks


def _policy_hash() -> str:
    return hashlib.sha256(config.POLICY_PATH.read_bytes()).hexdigest()[:16]


_index_cache: Optional[Dict[str, Any]] = None


def build_index(force: bool = False) -> Dict[str, Any]:
    """policy.md 를 섹션 단위로 쪼개 passage 임베딩 인덱스를 만든다(디스크 캐시)."""
    global _index_cache
    h = _policy_hash()
    if _index_cache and _index_cache.get("hash") == h and not force:
        return _index_cache

    if config.INDEX_PATH.exists() and not force:
        try:
            cached = json.loads(config.INDEX_PATH.read_text(encoding="utf-8"))
            if cached.get("hash") == h and cached.get("model") == config.MODEL_EMBED:
                _index_cache = cached
                return cached
        except Exception:  # noqa: BLE001 - 캐시 깨지면 그냥 재생성
            pass

    chunks = _chunk_policy()
    vectors = llm.embed([f"{c['title']}\n{c['text']}" for c in chunks], input_type="passage")
    index = {
        "hash": h,
        "model": config.MODEL_EMBED,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "chunks": chunks,
        "vectors": vectors,
    }
    config.INDEX_PATH.write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    _index_cache = index
    return index


def _cosine(a: List[float], b: List[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def search_policy(query: str, top_k: int = 2) -> Dict[str, Any]:
    """매장 정책 문서에서 질의와 가장 가까운 섹션을 찾는다."""
    index = build_index()
    qvec = llm.embed([query], input_type="query")[0]
    scored = [
        {"title": c["title"], "text": c["text"], "score": round(_cosine(qvec, v), 4)}
        for c, v in zip(index["chunks"], index["vectors"])
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    hits = scored[: max(1, min(top_k, 4))]
    return {
        "query": query,
        "model": config.MODEL_EMBED,
        "hits": hits,
        "citation": " / ".join(h["title"] for h in hits),
    }


# ---------------------------------------------------------------- 매니저 알림

SEVERITIES = ("low", "medium", "high", "critical")


def notify_manager(summary: str, severity: str, review_excerpt: str = "") -> Dict[str, Any]:
    """심각 건을 매장 매니저에게 에스컬레이션한다(1단계: logs/alerts.jsonl 기록)."""
    sev = severity.lower().strip()
    if sev not in SEVERITIES:
        sev = "high"
    record = {
        "alert_id": f"AL-{uuid.uuid4().hex[:8].upper()}",
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "severity": sev,
        "summary": summary,
        "review_excerpt": review_excerpt[:300],
        "channel": "jsonl(local)",  # 2단계: Slack/Webhook 연동 예정
    }
    config.ALERTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    with config.ALERTS_PATH.open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(record, ensure_ascii=False) + "\n")
    return {
        "status": "logged",
        "alert_id": record["alert_id"],
        "severity": sev,
        "path": str(config.ALERTS_PATH.relative_to(config.ROOT)),
    }


def read_alerts(limit: int = 20) -> List[Dict[str, Any]]:
    if not config.ALERTS_PATH.exists():
        return []
    lines = config.ALERTS_PATH.read_text(encoding="utf-8").strip().splitlines()
    out = []
    for line in lines[-limit:][::-1]:
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


# ---------------------------------------------------------------- 툴 스키마

TOOL_SCHEMAS: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "check_safety",
            "description": (
                "NVIDIA content-safety 모델로 텍스트의 안전성을 판정한다. "
                "리뷰 원문을 검사할 때는 kind='review', 네가 작성한 답글 초안을 "
                "게시 전에 검증할 때는 kind='draft_reply' 를 쓴다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "판정할 텍스트"},
                    "kind": {
                        "type": "string",
                        "enum": ["review", "draft_reply"],
                        "description": "검사 대상 종류",
                    },
                },
                "required": ["text", "kind"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_policy",
            "description": (
                "매장 운영 정책 문서(환불/위생/대기시간/중량/다국어/답글규칙/영업정보/알레르기)를 "
                "임베딩 검색한다. 답글에서 보상이나 절차를 언급하려면 반드시 먼저 근거를 찾아라."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "검색 질의(한국어 권장)"},
                    "top_k": {"type": "integer", "description": "가져올 섹션 수(기본 2)"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "notify_manager",
            "description": (
                "위생/이물질/건강 이상/법적 위협 등 사람이 즉시 개입해야 하는 건을 "
                "매장 매니저에게 에스컬레이션한다. severity 가 high 또는 critical 이면 필수."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "summary": {"type": "string", "description": "매니저용 한국어 요약(1~2문장)"},
                    "severity": {
                        "type": "string",
                        "enum": list(SEVERITIES),
                        "description": "심각도",
                    },
                    "review_excerpt": {"type": "string", "description": "리뷰 핵심 발췌"},
                },
                "required": ["summary", "severity"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "submit_reply",
            "description": (
                "최종 결과를 제출하고 작업을 종료한다. 반드시 마지막에 이 툴을 호출해야 한다. "
                "reply_text 는 리뷰 작성자가 쓴 언어와 동일한 언어로 작성한다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {
                        "type": "string",
                        "enum": ["ko", "en", "vi", "ru", "other"],
                        "description": "리뷰 작성자의 언어",
                    },
                    "category": {
                        "type": "string",
                        "enum": [
                            "칭찬",
                            "음식품질",
                            "위생/식품안전",
                            "서비스/응대",
                            "대기시간",
                            "가격/중량",
                            "예약/환불",
                            "기타",
                        ],
                        "description": "리뷰 분류",
                    },
                    "sentiment": {
                        "type": "string",
                        "enum": ["positive", "neutral", "negative"],
                    },
                    "severity": {"type": "string", "enum": list(SEVERITIES)},
                    "reply_text": {
                        "type": "string",
                        "description": "고객에게 게시할 답글(리뷰어 언어, 3~6문장)",
                    },
                    "rationale": {
                        "type": "string",
                        "description": "이렇게 판단·작성한 이유(한국어, 운영자 확인용)",
                    },
                },
                "required": [
                    "language",
                    "category",
                    "sentiment",
                    "severity",
                    "reply_text",
                    "rationale",
                ],
            },
        },
    },
]
