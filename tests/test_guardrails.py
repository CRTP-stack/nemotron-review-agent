"""가드레일 G1/G2/G3 단위 테스트 (API 호출 없음, 모델 응답을 스텁으로 대체).

실행: .venv/bin/python tests/test_guardrails.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, llm, loop, tools  # noqa: E402


def _tool_call(cid, name, args):
    return SimpleNamespace(
        id=cid, type="function",
        function=SimpleNamespace(name=name, arguments=json.dumps(args, ensure_ascii=False)),
    )


def _msg(tool_calls=None, content=None, reasoning="stub"):
    return SimpleNamespace(
        content=content, tool_calls=tool_calls or None, reasoning_content=reasoning
    )


class Harness:
    """llm.chat / tools.check_safety 를 가로채 원하는 시나리오를 재생한다."""

    def __init__(self, scripted_messages, safety_verdicts):
        self.scripted = list(scripted_messages)
        self.safety = list(safety_verdicts)
        self.rejections = []

    def __enter__(self):
        self._chat, self._safety = llm.chat, tools.check_safety
        # 테스트용 알림은 실제 데모 로그를 오염시키지 않도록 별도 파일로 보낸다.
        self._alerts, self._pending = config.ALERTS_PATH, config.PENDING_PATH
        config.ALERTS_PATH = config.ROOT / "logs" / ".test_alerts.jsonl"
        config.PENDING_PATH = config.ROOT / "logs" / ".test_pending.jsonl"

        def fake_chat(*_a, **_k):
            return self.scripted.pop(0) if self.scripted else _msg(content="끝")

        def fake_safety(text, kind="review", review_context=""):
            verdict = self.safety.pop(0) if self.safety else "safe"
            return {"kind": kind, "verdict": verdict, "raw": f"stub:{verdict}",
                    "model": "stub", "note": "stub"}

        llm.chat = fake_chat
        tools.check_safety = fake_safety
        loop.llm.chat = fake_chat
        loop.tools.check_safety = fake_safety
        return self

    def __exit__(self, *exc):
        llm.chat, tools.check_safety = self._chat, self._safety
        loop.llm.chat, loop.tools.check_safety = self._chat, self._safety
        config.ALERTS_PATH.unlink(missing_ok=True)
        config.PENDING_PATH.unlink(missing_ok=True)
        config.ALERTS_PATH, config.PENDING_PATH = self._alerts, self._pending


def collect(out):
    return [s for s in out["steps"] if s["type"] == "reject"]


def test_g1():
    """안전검사 없이 곧장 submit_reply → G1 반려 후 재제출."""
    scripted = [
        _msg([_tool_call("c1", "submit_reply", {
            "language": "ko", "category": "칭찬", "sentiment": "positive", "severity": "low",
            "reply_text": "감사합니다.", "rationale": "칭찬 리뷰"})]),
        _msg([_tool_call("c2", "check_safety", {"text": "맛있어요", "kind": "review"})]),
        _msg([_tool_call("c3", "submit_reply", {
            "language": "ko", "category": "칭찬", "sentiment": "positive", "severity": "low",
            "reply_text": "감사합니다.", "rationale": "칭찬 리뷰"})]),
    ]
    with Harness(scripted, ["safe", "safe"]) as h:
        out = loop.run_agent("맛있어요")
    rejects = collect(out)
    assert rejects and "G1" in rejects[0]["result"]["violations"][0], "G1 이 반려하지 않음"
    assert out["final"]["reply_text"] == "감사합니다.", "재제출이 수락되지 않음"
    return "G1: 안전검사 누락 → 반려 → 모델이 보완 후 재제출 성공"


def test_g2():
    """severity=critical 인데 notify_manager 미호출 → G2 반려."""
    sub = {"language": "ko", "category": "위생/식품안전", "sentiment": "negative",
           "severity": "critical", "reply_text": "죄송합니다.", "rationale": "위생 건"}
    scripted = [
        _msg([_tool_call("c1", "check_safety", {"text": "벌레 나왔어요", "kind": "review"})]),
        _msg([_tool_call("c2", "submit_reply", sub)]),
        _msg([_tool_call("c3", "notify_manager", {"summary": "이물질 발견", "severity": "critical"})]),
        _msg([_tool_call("c4", "submit_reply", sub)]),
    ]
    with Harness(scripted, ["unsafe", "safe"]) as h:
        out = loop.run_agent("벌레 나왔어요")
    rejects = collect(out)
    assert rejects and "G2" in rejects[0]["result"]["violations"][0], "G2 가 반려하지 않음"
    assert out["tools_used"].get("notify_manager") == 1, "매니저 알림이 기록되지 않음"
    return "G2: 심각도 critical + 알림 누락 → 반려 → 알림 후 재제출 성공"


def test_g3():
    """제출된 답글 초안이 unsafe → 1회 재작성 요구."""
    bad = {"language": "ko", "category": "서비스/응대", "sentiment": "negative", "severity": "low",
           "reply_text": "당신이 잘못했습니다.", "rationale": "초안"}
    good = dict(bad, reply_text="불편을 드려 죄송합니다.")
    scripted = [
        _msg([_tool_call("c1", "check_safety", {"text": "별로였어요", "kind": "review"})]),
        _msg([_tool_call("c2", "submit_reply", bad)]),
        _msg([_tool_call("c3", "submit_reply", good)]),
    ]
    # 1) 리뷰 판정 safe, 2) 초안1 unsafe, 3) 초안2 safe
    with Harness(scripted, ["safe", "unsafe", "safe"]) as h:
        out = loop.run_agent("별로였어요")
    verifies = [s for s in out["steps"] if s["type"] == "verify"]
    assert len(verifies) == 2, f"초안 검증이 2회 일어나야 함 (실제 {len(verifies)})"
    assert verifies[0]["result"]["verdict"] == "unsafe"
    assert out["final"]["reply_text"] == "불편을 드려 죄송합니다.", "재작성본이 채택되지 않음"
    return "G3: 답글 초안 unsafe → 재작성 요구 → 순화된 답글 채택"


def test_g4():
    """공개 답글에 주문번호·테이블번호가 섞이면 반려 → 재작업."""
    leaky = {"language": "ko", "category": "대기시간", "sentiment": "negative", "severity": "medium",
             "reply_text": "주문 내역을 확인해 보니 HD-20260921-03 주문, 테이블 2번에서 18:32에 "
                           "주문하셨네요. 늦어져 죄송합니다.",
             "rationale": "대기 지연"}
    clean = dict(leaky, reply_text="오래 기다리게 해 죄송합니다. 대기 안내를 개선하겠습니다. Hải Đăng Seafood 드림")
    scripted = [
        _msg([_tool_call("c1", "check_safety", {"text": "늦었어요", "kind": "review"})]),
        _msg([_tool_call("c2", "submit_reply", leaky)]),
        _msg([_tool_call("c3", "submit_reply", clean)]),
    ]
    with Harness(scripted, ["safe", "safe"]):
        out = loop.run_agent("늦었어요")
    rejects = collect(out)
    assert rejects, "G4 가 반려하지 않음"
    joined = " ".join(rejects[0]["result"]["violations"])
    for tag in ("주문번호", "테이블 번호", "주문·서빙 시각", "내부 주문 데이터"):
        assert tag in joined, f"G4 가 '{tag}' 를 잡지 못함: {joined}"
    assert out["final"]["reply_text"] == clean["reply_text"], "재작업본이 채택되지 않음"
    return "G4: 주문번호·테이블·시각·조회사실 4종 모두 반려 → 정제본 채택"


def test_g5():
    """중량 분쟁 답글에서 고객 탓 + 계량 절차 누락 + 금액 언급 → 반려."""
    bad = {"language": "en", "category": "가격/중량", "sentiment": "negative", "severity": "critical",
           "draft_reply": "You must have misremembered the weight. We will give you a 666,000₫ voucher.",
           "rationale": "중량 분쟁", "manager_note": ""}
    good = dict(bad, draft_reply="We are sorry about the confusion. Our staff brings a scale to your "
                                 "table so you can see the weight together before cooking. Please "
                                 "contact us at manager@haidang-danang.example so we can review it.")
    scripted = [
        _msg([_tool_call("c1", "check_safety", {"text": "weight wrong", "kind": "review"})]),
        _msg([_tool_call("c2", "notify_manager", {"summary": "중량 분쟁", "severity": "critical"})]),
        _msg([_tool_call("c3", "hold_for_approval", bad)]),
        _msg([_tool_call("c4", "hold_for_approval", good)]),
    ]
    with Harness(scripted, ["safe", "safe"]):
        out = loop.run_agent("weight wrong")
    rejects = collect(out)
    assert rejects, "G5 가 반려하지 않음"
    joined = " ".join(rejects[0]["result"]["violations"])
    assert "고객을 탓하는" in joined and "계량 절차" in joined and "보상/금액" in joined, joined
    assert out["final"]["status"] == "승인 대기", "승인 대기로 끝나지 않음"
    return "G5: 고객 탓 + 계량 절차 누락 + 금액 노출 3종 반려 → 정제본 승인 대기"


def test_g6():
    """위생 클레임 답글에서 보상 약속·책임 인정 → 반려, 연락 채널 필수."""
    bad = {"language": "vi", "category": "위생/식품안전", "sentiment": "negative",
           "severity": "critical",
           "draft_reply": "Đây là lỗi của chúng tôi. Chúng tôi sẽ hoàn tiền toàn bộ hóa đơn.",
           "rationale": "위생", "manager_note": ""}
    good = dict(bad, draft_reply="Chúng tôi rất tiếc về trải nghiệm này. Xin quý khách liên hệ "
                                 "manager@haidang-danang.example để chúng tôi kiểm tra kỹ hơn.")
    scripted = [
        _msg([_tool_call("c1", "check_safety", {"text": "gián", "kind": "review"})]),
        _msg([_tool_call("c2", "notify_manager", {"summary": "위생", "severity": "critical"})]),
        _msg([_tool_call("c3", "hold_for_approval", bad)]),
        _msg([_tool_call("c4", "hold_for_approval", good)]),
    ]
    with Harness(scripted, ["unsafe", "safe"]):
        out = loop.run_agent("gián")
    joined = " ".join(collect(out)[0]["result"]["violations"])
    assert "개인 연락 채널이 없다" in joined, joined
    assert "보상을 약속" in joined, joined
    assert "책임을 인정" in joined, joined
    assert out["final"]["status"] == "승인 대기"
    return "G6: 연락처 누락 + 보상 약속 + 책임 인정 3종 반려 → 연락 유도형 초안 채택"


def test_g7():
    """critical 인데 submit_reply 로 자동 게시하려 하면 반려 → hold_for_approval."""
    sub = {"language": "vi", "category": "위생/식품안전", "sentiment": "negative",
           "severity": "critical", "reply_text": "Xin lỗi quý khách. Vui lòng liên hệ "
                                                 "manager@haidang-danang.example.",
           "rationale": "위생"}
    hold = {"language": "vi", "category": "위생/식품안전", "sentiment": "negative",
            "severity": "critical", "draft_reply": sub["reply_text"], "rationale": "위생",
            "manager_note": "사실 확인 필요"}
    scripted = [
        _msg([_tool_call("c1", "check_safety", {"text": "gián", "kind": "review"})]),
        _msg([_tool_call("c2", "notify_manager", {"summary": "위생", "severity": "critical"})]),
        _msg([_tool_call("c3", "submit_reply", sub)]),
        _msg([_tool_call("c4", "hold_for_approval", hold)]),
    ]
    with Harness(scripted, ["unsafe", "safe"]):
        out = loop.run_agent("gián")
    joined = " ".join(collect(out)[0]["result"]["violations"])
    assert "G7" in joined and "hold_for_approval" in joined, joined
    assert out["final"]["status"] == "승인 대기"
    assert out["final"].get("approval_id"), "승인 대기 ID 가 없음"
    return "G7: critical 자동 게시 시도 반려 → 승인 대기로 강제 전환"


def test_forced_hold():
    """가드레일을 3회 넘게 못 넘기면 자동 게시하지 않고 사람에게 넘긴다."""
    leaky = {"language": "ko", "category": "대기시간", "sentiment": "negative", "severity": "medium",
             "reply_text": "테이블 2번 주문 HD-20260921-03 확인했습니다.", "rationale": "x"}
    scripted = [_msg([_tool_call("c1", "check_safety", {"text": "x", "kind": "review"})])]
    scripted += [_msg([_tool_call("c%d" % i, "submit_reply", leaky)]) for i in range(2, 6)]
    with Harness(scripted, ["safe", "safe"]):
        out = loop.run_agent("x")
    assert out["final"]["status"] == "승인 대기", "자동 게시가 차단되지 않음"
    assert out["final"].get("guardrail_forced"), "강제 전환 표시가 없음"
    forced = [s for s in out["steps"] if s["type"] == "forced_hold"]
    assert forced, "forced_hold 단계가 기록되지 않음"
    return "fail-safe: 반려 3회 초과 → 자동 게시 차단하고 승인 대기로 전환"


def test_refund_promise_by_category():
    """환불 약속은 카테고리에 따라 허용/반려가 갈린다.

    정책 §1은 조리 오류 환불을 고객에게 직접 안내하도록 허용한다(ru-refund 케이스).
    반면 §2(위생)·§9(중량 분쟁)는 공개 답글에서 보상 언급을 금지한다.
    """
    from agent import guardrails as g
    ru = ("Уважаемый гость, приносим извинения за пересоленного краба. "
          "Согласно политике Hải Đăng Seafood, мы вернём полную стоимость блюда "
          "при предъявлении чека в течение 7 дней.")
    assert not g.inspect_reply(ru, "음식품질"), "조리 오류 환불 안내는 허용돼야 한다(정책 §1)"
    assert not g.inspect_reply(ru, "예약/환불"), "환불 카테고리도 허용돼야 한다"
    for cat in ("가격/중량", "위생/식품안전"):
        v = " ".join(g.inspect_reply(ru, cat))
        assert "보상" in v, f"{cat} 에서 환불 약속이 반려되지 않음: {v}"

    # 동사 활용형까지 잡는지(명사형만 넣으면 'вернём' 을 놓친다)
    for text in ("мы вернём деньги", "we will refund you", "chúng tôi sẽ hoàn tiền",
                 "전액 환불해 드리겠습니다", "the meal is complimentary", "miễn phí"):
        assert g._compensation_hits(text), f"보상 표현 미탐지: {text}"
    return "환불 약속: 음식품질/예약환불은 허용, 중량·위생은 반려 (동사 활용형 포함)"


def main():
    print("가드레일 단위 테스트 (API 호출 없음)\n" + "─" * 60)
    ok = True
    for fn in (test_g1, test_g2, test_g3, test_g4, test_g5, test_g6, test_g7,
               test_forced_hold, test_refund_promise_by_category):
        try:
            print(f"  ✔ {fn()}")
        except AssertionError as exc:
            ok = False
            print(f"  ✘ {fn.__name__}: {exc}")
    print("─" * 60)
    print("  PASS" if ok else "  FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
