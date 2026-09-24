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
        self._alerts = config.ALERTS_PATH
        config.ALERTS_PATH = config.ROOT / "logs" / ".test_alerts.jsonl"

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
        config.ALERTS_PATH = self._alerts


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


def main():
    print("가드레일 단위 테스트 (API 호출 없음)\n" + "─" * 60)
    ok = True
    for fn in (test_g1, test_g2, test_g3):
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
