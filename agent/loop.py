"""에이전트 루프.

고정 파이프라인이 아니다. 메인 모델(nemotron-3-ultra-550b-a55b)이 매 턴
tool calling 으로 '다음에 무엇을 할지' 스스로 결정하고, 이 루프는 그 결정을 실행한 뒤
결과를 되돌려줄 뿐이다.

여기에 더해 코드 레벨 가드레일을 둔다. 모델이 잊어버려도 시스템이 강제한다.
  G1 리뷰 안전검사 없이 종료 금지
  G2 severity high/critical 인데 매니저 알림 없이 종료 금지
  G3 확정된 답글 초안을 content-safety 로 자동 재검증(unsafe 면 재작성 요구)
  G4 공개 답글에 내부 데이터(주문번호/테이블/주문시각/조회 사실) 노출 금지
  G5 중량 분쟁 답글: 고객 탓 금지 + 테이블 계량 절차 안내 필수 + 보상 금액 금지
  G6 위생 클레임 답글: 사실 인정·반박·보상 약속 금지 + 개인 연락 채널 필수
  G7 심각도별 게시 경로 강제(critical/high → 승인 대기, low → 자동 게시)
"""
from __future__ import annotations

import json
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import config, guardrails, llm, tools

TERMINAL_TOOLS = ("submit_reply", "hold_for_approval")
MAX_TERMINAL_REJECTS = 3   # 반려 왕복이 이 횟수를 넘으면 사람에게 넘긴다(fail-safe)

SYSTEM_PROMPT = """너는 베트남 다낭 미케비치의 해산물 레스토랑 "Hải Đăng Seafood"의 리뷰 응대 담당 AI 에이전트다.
오늘 날짜는 2026-09-25 다.

[임무]
고객 리뷰 1건을 받아, 필요한 툴을 스스로 골라 호출하면서 처리한 뒤 최종 결과를 제출한다.

[반드시 지킬 절차]
1. 가장 먼저 check_safety(kind="review") 로 리뷰 원문을 판정한다.
   - 판정이 unsafe 여도 고객을 차단하지 않는다. 사람이 개입해야 할 심각 건이라는 신호로 해석한다.
2. 사실 확인이 필요한 리뷰(중량·금액 분쟁, 대기 시간 불만, 위생 클레임, 방문 사실이 의심되는 건)는
   find_order_candidates 로 주문 데이터를 대조한다.
   - 단순 칭찬 리뷰처럼 확인할 사실이 없으면 부르지 마라. 불필요한 조회는 하지 않는다.
   - 결과는 '후보'일 뿐 확정이 아니다. 반환된 verdict 에 따라 갈라진다.
   - **verdict 가 high 또는 medium 일 때만** 주문 데이터를 판단 근거로 쓴다.
     (notify_manager 에 order_id 를 넘겨 보상안을 산정시킬 수 있다.)
   - **verdict 가 low 또는 no_match 이면 주문 데이터를 근거로 쓰지 마라.**
     방문일이 리뷰에 적혀 있는데 no_match/low 라면 '방문 확인 불가' 경로로 간다:
       · category 를 "방문확인불가" 로 두거나 실제 클레임 유형을 쓰되,
       · 답글은 **짧고 중립적으로** 쓴다(3문장 이내). 사실관계를 인정하지도 반박하지도 않는다.
       · 고객을 의심하거나 거짓이라고 암시하지 마라. "기록이 없다"는 말도 쓰지 마라.
       · 개인 연락 채널로만 확인을 요청한다.
       · notify_manager 로 매니저에게 넘기고 hold_for_approval 로 종료한다.
     방문일 자체가 리뷰에 없어서 no_match 인 경우는, 방문 사실을 다투는 건이 아니라면
     주문 대조 없이 일반 정책(search_policy)으로 처리하면 된다.
3. 답글에서 환불·보상·대기 보상·중량 분쟁·영업정보 등 매장 규정에 해당하는 내용을 언급하려면,
   반드시 먼저 search_policy 로 근거 조항을 찾는다. 정책에 없는 보상은 절대 약속하지 않는다.
4. 심각 건은 notify_manager 로 에스컬레이션한다. 이때 order_id 와 issue_type 을 함께 넘기면
   정책 §9 보상 규칙으로 제안 보상 금액이 자동 계산되어 **매니저 알림에만** 기록된다.
   그 금액을 공개 답글에 절대 쓰지 마라.
5. 답글은 리뷰 작성자가 사용한 언어와 똑같은 언어로 쓴다.
   (한국어 리뷰→한국어, English→English, tiếng Việt→tiếng Việt, русский→русский)
   번역투를 쓰지 말고 그 언어권에서 자연스러운 존대 표현을 쓴다. 3~6문장.
   **오직 그 언어 하나로만 쓴다. 다른 언어 단어를 절대 섞지 마라.**
   escalation / critical / refund / manager 같은 영어 단어를 베트남어·러시아어·한국어 답글에
   그대로 쓰지 말고, 반드시 해당 언어 표현으로 번역해서 써라
   (예: escalation → báo cáo khẩn cấp, critical → nghiêm trọng).
   단, 매장명 "Hải Đăng Seafood" 와 이메일·전화번호는 원형 그대로 쓴다.
6. 마지막에 심각도에 맞는 종료 툴을 호출한다.
   - severity 가 critical 또는 high → hold_for_approval (공개 게시하지 않고 매니저 승인 대기)
     · critical: 위생·이물질·식중독, 청구 금액/중량/단가 분쟁, 법적 대응·언론 제보 예고
     · high: 반복 클레임, 직원의 부적절한 언행
     · hold_for_approval 전에 notify_manager 를 반드시 먼저 부른다.
   - severity 가 medium 또는 low → submit_reply (자동 게시)
     · 대기 시간 불만, 맛·간 등 기호 차이, 조리 오류 환불 요청, 일반 칭찬
     · 단순 환불 요청은 '금액 분쟁'이 아니다. 청구 금액·중량·단가가 틀렸다는 주장만 금액 분쟁이다.

[공개 답글에 절대 쓰면 안 되는 것]
- 주문번호, 테이블 번호, 주문·서빙 시각, 결제 금액.
- "주문 내역을 확인해 보니", "전산에서 조회해 보니" 같은 내부 조회 사실.
  확인이 필요하면 주문 정보를 말하지 말고 개별 연락만 정중히 요청하라.
- 보상 금액·비율·바우처. 보상은 매니저 알림에만 기재한다.

[중량·금액 분쟁 답글 규칙]
- 고객의 착오라고 지적하지 마라. "착각하셨다", "동의하셨다" 같은 표현 금지.
- 테이블에서 저울로 중량을 함께 확인하는 절차가 있다는 사실만 정중히 안내하라.
- 정확한 확인은 개별 연락으로 유도하라.

[위생 클레임 답글 규칙]
- 사실 인정 금지, 반박 금지, 보상 약속 금지. 공개된 자리에서 판단하지 않는다.
- 사과와 유감 표명 + 개인 연락 채널(manager@haidang-danang.example / +84 236 555 0147) 안내만 한다.

[답글 작성 규칙]
- 감사 → 지적 사항의 구체적 인정 → 정책 근거에 따른 조치 안내 → 재방문 유도 순서.
- 매장명 "Hải Đăng Seafood" 를 언급한다.
- search_policy 로 실제로 읽은 조항에 있는 조치만 약속한다. 정책에 없는 조치
  (식재료 폐기, 직원 징계·해고, 위생 점검 일정, 설비 교체 등)는 그럴듯해 보여도 쓰지 마라.
- 정책 문서에 있더라도 `[내부 조치]` 로 표시된 항목은 답글에 쓰지 않는다.
- 고객의 외모·국적·성별을 언급하지 않는다. 경쟁 매장을 언급하지 않는다.

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
    if name == "find_order_candidates":
        return tools.find_order_candidates(
            review_date=args.get("review_date", ""),
            mentioned_items=args.get("mentioned_items", []) or [],
            language=args.get("language", ""),
        )
    if name == "notify_manager":
        return tools.notify_manager(
            summary=args.get("summary", ""),
            severity=args.get("severity", "high"),
            review_excerpt=args.get("review_excerpt", "") or review,
            order_id=args.get("order_id", "") or "",
            issue_type=args.get("issue_type", "") or "",
            item_hint=args.get("item_hint", "") or "",
        )
    return {"error": f"알 수 없는 툴: {name}"}


def _draft_of(name: str, args: Dict[str, Any]) -> str:
    key = "reply_text" if name == "submit_reply" else "draft_reply"
    return (args.get(key, "") or "").strip()


def _routing_violations(name: str, severity: str) -> List[str]:
    """G7 심각도별 게시 경로 강제."""
    sev = (severity or "low").lower()
    if name == "submit_reply" and sev in ("high", "critical"):
        return [
            f"G7 위반: severity='{sev}' 는 자동 게시 대상이 아니다(정책 §10). "
            "submit_reply 대신 hold_for_approval 을 호출해 매니저 승인 대기로 종료하라."
        ]
    if name == "hold_for_approval" and sev == "low":
        return [
            "G7 위반: severity='low' 는 자동 게시 대상이다(정책 §10). "
            "hold_for_approval 대신 submit_reply 를 호출하라."
        ]
    return []


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
    draft_checks: Dict[str, Dict[str, Any]] = {}   # 에이전트가 스스로 검사한 초안 재사용
    alert_ids: List[str] = []
    compensations: List[Dict[str, Any]] = []
    rewrite_used = 0
    reject_count = 0
    last_args: Dict[str, Any] = {}
    last_tool = ""
    final: Optional[Dict[str, Any]] = None
    nudged = False

    for _ in range(max_steps):
        t0 = time.time()
        msg = llm.chat(config.MODEL_MAIN, messages, tools=tools.TOOL_SCHEMAS,
                       temperature=0.3, max_tokens=1400)
        think = (getattr(msg, "reasoning_content", None) or "").strip()

        tool_calls = getattr(msg, "tool_calls", None) or []
        if not tool_calls:
            content = (msg.content or "").strip()
            if not nudged:
                nudged = True
                emit(trace.add(actor="guardrail", type="nudge", tool="(종료 툴)",
                               reasoning=think, elapsed=round(time.time() - t0, 2),
                               result={"note": "툴 호출 없이 평문 응답 → 종료 툴 재요청"}))
                messages.append({"role": "assistant", "content": content})
                messages.append({
                    "role": "user",
                    "content": "평문 대신 submit_reply 또는 hold_for_approval 툴을 호출해서 결과를 제출해줘.",
                })
                continue
            final = {
                "language": "other", "category": "기타", "sentiment": "neutral",
                "severity": "low", "reply_text": content, "status": "자동 게시",
                "rationale": "모델이 종료 툴을 호출하지 않아 평문 응답을 그대로 사용함.",
                "degraded": True,
            }
            break

        messages.append({
            "role": "assistant",
            "content": msg.content,
            "tool_calls": [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in tool_calls
            ],
        })

        finished = False
        for tc in tool_calls:
            name = tc.function.name
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {}
            called[name] = called.get(name, 0) + 1

            # ------------------------------------------------ 종료 툴
            if name in TERMINAL_TOOLS:
                last_tool, last_args = name, args
                sev = str(args.get("severity", "low")).lower()
                draft = _draft_of(name, args)
                category = args.get("category", "기타")

                violations: List[str] = []
                if not called.get("check_safety"):
                    violations.append(
                        "G1 위반: 리뷰 원문에 대한 check_safety(kind='review') 를 아직 호출하지 않았다. "
                        "먼저 안전 판정을 수행하라."
                    )
                if sev in ("high", "critical") and not called.get("notify_manager"):
                    violations.append(
                        f"G2 위반: severity='{sev}' 로 판단했으면 notify_manager 를 먼저 호출해야 한다."
                    )
                violations += _routing_violations(name, sev)
                violations += guardrails.inspect_reply(draft, category)

                if violations:
                    reject_count += 1
                    payload = {"accepted": False, "violations": violations,
                               "남은_재시도": max(0, MAX_TERMINAL_REJECTS - reject_count)}
                    emit(trace.add(actor="guardrail", type="reject", tool=name,
                                   args=args, reasoning=think,
                                   elapsed=round(time.time() - t0, 2), result=payload))
                    if reject_count >= MAX_TERMINAL_REJECTS:
                        # fail-safe: 가드레일을 못 넘기면 자동 게시하지 않고 사람에게 넘긴다.
                        hold = tools.hold_for_approval(
                            language=args.get("language", "other"), category=category,
                            sentiment=args.get("sentiment", "neutral"),
                            severity=sev if sev != "low" else "medium", draft_reply=draft,
                            rationale=args.get("rationale", ""),
                            manager_note="가드레일 반려 3회 초과 → 자동 게시 차단, 사람 검토 필요. "
                                         + " / ".join(violations)[:300],
                            alert_id=alert_ids[-1] if alert_ids else "",
                        )
                        final = dict(args, reply_text=draft, status="승인 대기",
                                     approval_id=hold["approval_id"], degraded=True,
                                     guardrail_forced=True, violations=violations)
                        emit(trace.add(actor="guardrail", type="forced_hold", tool="hold_for_approval",
                                       elapsed=0.0, result=hold))
                        finished = True
                        break
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": json.dumps(payload, ensure_ascii=False)})
                    continue

                # ---- G3: 확정 직전 답글 초안 안전성 재검증 ----
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

                # ---- 수락 ----
                if name == "hold_for_approval":
                    held = tools.hold_for_approval(
                        language=args.get("language", "other"), category=category,
                        sentiment=args.get("sentiment", "neutral"), severity=sev,
                        draft_reply=draft, rationale=args.get("rationale", ""),
                        manager_note=args.get("manager_note", ""),
                        alert_id=args.get("alert_id", "") or (alert_ids[-1] if alert_ids else ""),
                    )
                    final = dict(args, reply_text=draft, status="승인 대기",
                                 approval_id=held["approval_id"], reply_safety=verify["verdict"])
                    emit(trace.add(actor="agent", type="final", tool=name, args=args,
                                   reasoning=think, elapsed=round(time.time() - t0, 2),
                                   result={"accepted": True, **held}))
                else:
                    final = dict(args, status="자동 게시", reply_safety=verify["verdict"])
                    emit(trace.add(actor="agent", type="final", tool=name, args=args,
                                   reasoning=think, elapsed=round(time.time() - t0, 2),
                                   result={"accepted": True, "status": "자동 게시"}))
                if compensations:
                    final["compensation"] = compensations[-1]
                if alert_ids:
                    final.setdefault("alert_id", alert_ids[-1])
                finished = True
                break

            # ------------------------------------------------ 일반 툴
            t1 = time.time()
            try:
                result = _execute_tool(name, args, review)
            except Exception as exc:  # noqa: BLE001 - 툴 실패도 모델에게 돌려준다
                result = {"error": f"{type(exc).__name__}: {exc}"}
            if name == "check_safety" and args.get("kind") == "draft_reply" and "verdict" in result:
                draft_checks[(args.get("text", "") or "").strip()] = result
            if name == "notify_manager" and result.get("alert_id"):
                alert_ids.append(result["alert_id"])
                if result.get("compensation", {}).get("applicable"):
                    compensations.append(result["compensation"])
            emit(trace.add(actor="agent", type="tool_call", tool=name, args=args,
                           reasoning=think, elapsed=round(time.time() - t1, 2), result=result))
            messages.append({"role": "tool", "tool_call_id": tc.id,
                             "content": json.dumps(result, ensure_ascii=False)})

        if finished:
            break

    if final is None:
        final = {
            "language": "other", "category": "기타", "sentiment": "neutral", "severity": "low",
            "reply_text": _draft_of(last_tool, last_args) or "(에이전트가 최대 단계 내에 답글을 제출하지 못했습니다.)",
            "status": "승인 대기", "rationale": f"max_steps={max_steps} 초과", "degraded": True,
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
