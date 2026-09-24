"""에이전트 루프.

고정 파이프라인이 아니다. 메인 모델(nemotron-3-ultra-550b-a55b)이 매 턴
tool calling 으로 '다음에 무엇을 할지' 스스로 결정하고, 이 루프는 그 결정을 실행한 뒤
결과를 되돌려줄 뿐이다.

여기에 더해 코드 레벨 가드레일 3종을 둔다. 모델이 잊어버려도 시스템이 강제한다.
  G1 리뷰 안전검사 없이 답글 제출 금지
  G2 severity high/critical 인데 매니저 알림 없이 제출 금지
  G3 제출된 답글 초안은 content-safety 로 자동 재검증(unsafe 면 1회 재작성 요구)
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional

from . import config, llm, tools

SYSTEM_PROMPT = """너는 베트남 다낭 미케비치의 해산물 레스토랑 "Hải Đăng Seafood"의 리뷰 응대 담당 AI 에이전트다.

[임무]
고객 리뷰 1건을 받아, 필요한 툴을 스스로 골라 호출하면서 처리한 뒤 최종 결과를 제출한다.

[반드시 지킬 절차]
1. 가장 먼저 check_safety(kind="review") 로 리뷰 원문을 판정한다.
   - 판정이 unsafe 여도 고객을 차단하지 않는다. 사람이 개입해야 할 심각 건이라는 신호로 해석한다.
2. 답글에서 환불·보상·대기 보상·중량 분쟁·영업정보 등 매장 규정에 해당하는 내용을 언급하려면,
   반드시 먼저 search_policy 로 근거 조항을 찾는다. 정책에 없는 보상은 절대 약속하지 않는다.
   필요하면 서로 다른 질의로 두 번 이상 검색해도 된다.
3. 이물질·위생·섭취 후 건강 이상·법적 위협·폭언이 포함된 리뷰는 severity 를 high 또는 critical 로
   판단하고, 반드시 notify_manager 를 호출해 매니저에게 에스컬레이션한다.
4. 답글은 리뷰 작성자가 사용한 언어와 똑같은 언어로 쓴다.
   (한국어 리뷰→한국어, English→English, tiếng Việt→tiếng Việt, русский→русский)
   번역투를 쓰지 말고 그 언어권에서 자연스러운 존대 표현을 쓴다. 3~6문장.
   **오직 그 언어 하나로만 쓴다. 다른 언어 단어를 절대 섞지 마라.**
   escalation / critical / refund / manager 같은 영어 단어를 베트남어·러시아어·한국어 답글에
   그대로 쓰지 말고, 반드시 해당 언어 표현으로 번역해서 써라
   (예: escalation → báo cáo khẩn cấp, critical → nghiêm trọng).
   단, 매장명 "Hải Đăng Seafood" 와 이메일·전화번호는 원형 그대로 쓴다.
5. 마지막에 submit_reply 를 호출해 분류 결과와 답글을 제출하고 종료한다.

[답글 작성 규칙]
- 감사 → 지적 사항의 구체적 인정 → 정책 근거에 따른 조치 안내 → 재방문 유도 순서.
- 매장명 "Hải Đăng Seafood" 를 언급한다.
- **search_policy 로 실제로 읽은 조항에 있는 조치만 약속한다.** 정책 문서에 없는 조치
  (식재료 폐기, 직원 징계·해고, 위생 점검 일정, 설비 교체, 무료 식사권, 현금 배상 등)는
  그럴듯해 보여도 절대 쓰지 마라.
- **정책 문서에 있더라도 `[내부 조치]` 로 표시된 항목은 답글에 쓰지 않는다.**
  (예: 동일 로트 식재료 사용 중단, 조리·입고 기록 보존, 직원 재교육 → 전부 내부 절차)
  고객에게 안내할 수 있는 것은 `[고객 안내]` 항목과 각 정책의 고객 대상 조항뿐이다.
- 고객의 외모·국적·성별을 언급하지 않는다. 경쟁 매장을 언급하지 않는다.
- 심각 건이면 매니저 직접 연락 예정임을 답글에 포함한다.

한 번에 여러 툴을 호출해도 되고, 결과를 보고 다음 툴을 정해도 된다. 불필요한 툴은 부르지 마라."""


class Trace:
    """데모 화면에 뿌릴 단계 기록."""

    def __init__(self) -> None:
        self.steps: List[Dict[str, Any]] = []

    def add(self, **kw: Any) -> Dict[str, Any]:
        kw["n"] = len(self.steps) + 1
        self.steps.append(kw)
        return kw


def _execute_tool(name: str, args: Dict[str, Any], review: str) -> Dict[str, Any]:
    if name == "check_safety":
        return tools.check_safety(
            text=args.get("text", ""),
            kind=args.get("kind", "review"),
            review_context=review,
        )
    if name == "search_policy":
        return tools.search_policy(args.get("query", ""), int(args.get("top_k", 2) or 2))
    if name == "notify_manager":
        return tools.notify_manager(
            summary=args.get("summary", ""),
            severity=args.get("severity", "high"),
            review_excerpt=args.get("review_excerpt", "") or review,
        )
    return {"error": f"알 수 없는 툴: {name}"}


def run_agent(
    review: str,
    on_step: Optional[Callable[[Dict[str, Any]], None]] = None,
    max_steps: int = config.MAX_AGENT_STEPS,
) -> Dict[str, Any]:
    """리뷰 1건을 에이전트 루프로 처리한다."""
    llm.reset_logs()
    trace = Trace()
    started = time.time()

    def emit(step: Dict[str, Any]) -> None:
        if on_step:
            on_step(step)

    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"다음 고객 리뷰를 처리해줘.\n\n---\n{review}\n---"},
    ]

    called: Dict[str, int] = {}
    # 에이전트가 스스로 답글 초안을 검사했다면 G3 에서 같은 호출을 반복하지 않는다.
    draft_checks: Dict[str, Dict[str, Any]] = {}
    rewrite_used = 0
    final: Optional[Dict[str, Any]] = None
    nudged = False

    for _ in range(max_steps):
        t0 = time.time()
        msg = llm.chat(config.MODEL_MAIN, messages, tools=tools.TOOL_SCHEMAS,
                       temperature=0.3, max_tokens=1400)
        think = (getattr(msg, "reasoning_content", None) or "").strip()

        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            # 툴 없이 평문만 왔다 -> 한 번만 submit_reply 를 요구한다.
            content = (msg.content or "").strip()
            if not nudged:
                nudged = True
                emit(trace.add(actor="guardrail", type="nudge", tool="submit_reply",
                               reasoning=think, elapsed=round(time.time() - t0, 2),
                               result={"note": "툴 호출 없이 평문 응답 → submit_reply 재요청"}))
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "평문 대신 submit_reply 툴을 호출해서 결과를 제출해줘.",
                })
                continue
            final = {
                "language": "other", "category": "기타", "sentiment": "neutral",
                "severity": "low", "reply_text": content,
                "rationale": "모델이 submit_reply 를 호출하지 않아 평문 응답을 그대로 사용함.",
                "degraded": True,
            }
            break

        # 어시스턴트의 tool_calls 를 대화에 그대로 되돌려 넣는다.
        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        submitted = False
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            called[name] = called.get(name, 0) + 1

            if name == "submit_reply":
                # ---- 가드레일 검사 ----
                violations: List[str] = []
                if not called.get("check_safety"):
                    violations.append(
                        "G1 위반: 리뷰 원문에 대한 check_safety(kind='review') 를 아직 호출하지 않았다. "
                        "먼저 안전 판정을 수행하라."
                    )
                sev = str(args.get("severity", "low")).lower()
                if sev in ("high", "critical") and not called.get("notify_manager"):
                    violations.append(
                        f"G2 위반: severity='{sev}' 로 판단했으면 notify_manager 를 반드시 호출해야 한다."
                    )
                if violations:
                    payload = {"accepted": False, "violations": violations}
                    emit(trace.add(actor="guardrail", type="reject", tool="submit_reply",
                                   args=args, reasoning=think,
                                   elapsed=round(time.time() - t0, 2), result=payload))
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(payload, ensure_ascii=False)})
                    continue

                # ---- G3: 제출된 답글 초안 자동 재검증 ----
                draft = (args.get("reply_text", "") or "").strip()
                cached = draft_checks.get(draft)
                if cached:
                    verify = dict(cached, note=cached["note"] + " (에이전트가 이미 검사함 → 재사용)")
                    emit(trace.add(actor="guardrail", type="verify", tool="check_safety",
                                   args={"kind": "draft_reply", "cached": True},
                                   elapsed=0.0, result=verify))
                else:
                    t1 = time.time()
                    verify = tools.check_safety(draft, kind="draft_reply", review_context=review)
                    emit(trace.add(actor="guardrail", type="verify", tool="check_safety",
                                   args={"kind": "draft_reply"},
                                   elapsed=round(time.time() - t1, 2), result=verify))
                if verify["verdict"] == "unsafe" and rewrite_used < 1:
                    rewrite_used += 1
                    payload = {"accepted": False,
                               "violations": ["G3 위반: 답글 초안이 content-safety 에서 unsafe 로 "
                                              "판정되었다. 표현을 순화해 다시 제출하라."]}
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(payload, ensure_ascii=False)})
                    continue

                args["reply_safety"] = verify["verdict"]
                final = args
                submitted = True
                emit(trace.add(actor="agent", type="final", tool="submit_reply",
                               args=args, reasoning=think,
                               elapsed=round(time.time() - t0, 2),
                               result={"accepted": True}))
                break

            # ---- 일반 툴 실행 ----
            t1 = time.time()
            try:
                result = _execute_tool(name, args, review)
            except Exception as exc:  # noqa: BLE001 - 툴 실패도 모델에게 돌려준다
                result = {"error": f"{type(exc).__name__}: {exc}"}
            if name == "check_safety" and args.get("kind") == "draft_reply" and "verdict" in result:
                draft_checks[(args.get("text", "") or "").strip()] = result
            emit(trace.add(actor="agent", type="tool_call", tool=name, args=args,
                           reasoning=think, elapsed=round(time.time() - t1, 2), result=result))
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})

        if submitted:
            break

    if final is None:
        final = {
            "language": "other", "category": "기타", "sentiment": "neutral", "severity": "low",
            "reply_text": "(에이전트가 최대 단계 내에 답글을 제출하지 못했습니다.)",
            "rationale": f"max_steps={max_steps} 초과", "degraded": True,
        }

    return {
        "review": review,
        "final": final,
        "steps": trace.steps,
        "tools_used": called,
        "elapsed": round(time.time() - started, 2),
        "usage": llm.usage_total(),
        "retries": list(llm.RETRY_LOG),
    }
