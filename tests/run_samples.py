"""샘플 리뷰 통합 테스트 (실제 NVIDIA API 호출).

사용법:
  .venv/bin/python tests/run_samples.py                # 9건 전부
  .venv/bin/python tests/run_samples.py --stage 2      # 2단계 5건만
  .venv/bin/python tests/run_samples.py --only ko-praise
결과는 logs/test_report.json 에 저장된다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, guardrails, loop, samples  # noqa: E402
from agent import tools as agent_tools  # noqa: E402

BAR = "─" * 78
ICON = {"tool_call": "🔧", "final": "✅", "verify": "🛡️", "reject": "⛔",
        "nudge": "↩️", "forced_hold": "🚧"}


def brief(obj, maxlen=170):
    s = json.dumps(obj, ensure_ascii=False)
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


def run_one(sample):
    print(f"\n{BAR}\n▶ [{sample['stage']}단계] {sample['label']}  ({sample['id']})\n{BAR}")
    print(f"리뷰: {sample['text'][:110]}…\n")
    t0 = time.time()
    try:
        out = loop.run_agent(sample["text"])
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 실행 실패: {type(exc).__name__}: {exc}")
        return {"id": sample["id"], "ok": False, "error": str(exc),
                "error_kind": "5xx" if "500" in str(exc) or "503" in str(exc) else "other"}

    for st in out["steps"]:
        actor = "에이전트 결정" if st["actor"] == "agent" else "가드레일"
        print(f"  {ICON.get(st['type'], '•')} step{st['n']} [{actor}] {st['tool']} ({st['elapsed']}s)")
        if st.get("reasoning"):
            print(f"      why: {st['reasoning'][:140]}")
        if st.get("args") and st["tool"] not in ("submit_reply", "hold_for_approval"):
            print(f"      args: {brief(st['args'], 130)}")
        if st["type"] == "reject":
            for v in st["result"]["violations"]:
                print(f"      ⛔ {v[:120]}")
        elif st.get("result"):
            print(f"      → {brief(st['result'])}")

    f = out["final"]
    reply = f.get("reply_text", "") or ""
    print(f"\n  상태: {f.get('status')} | language={f.get('language')} / "
          f"category={f.get('category')} / severity={f.get('severity')} / "
          f"답글안전={f.get('reply_safety')}")
    if f.get("approval_id"):
        print(f"  승인대기 ID: {f['approval_id']}  (공개 게시 안 됨)")
    if f.get("compensation"):
        print(f"  보상 제안(매니저 알림 전용): {f['compensation'].get('display')}")
    print("  답글:\n      " + reply.replace("\n", "\n      "))
    print(f"\n  사용 툴: {out['tools_used']} | {out['elapsed']}s | "
          f"{out['usage']['calls']}콜 {out['usage']['total_tokens']}토큰")
    if out["retries"]:
        print(f"  5xx 재시도: {len(out['retries'])}회")

    # ---------------------------------------------------- 검증
    checks = []
    used = out["tools_used"]
    checks.append(("리뷰 안전검사 수행", used.get("check_safety", 0) >= 1))
    if sample.get("expect_policy_search", True):
        checks.append(("정책 검색 수행", used.get("search_policy", 0) >= 1))
    else:
        checks.append(("불필요한 정책 검색 없음", used.get("search_policy", 0) == 0))
    checks.append(("답글 언어 일치", f.get("language") == sample["lang"]))
    checks.append(("답글 비어있지 않음", bool(reply.strip())))
    checks.append((f"게시 상태 = {sample['expect_status']}", f.get("status") == sample["expect_status"]))

    alerted = used.get("notify_manager", 0) >= 1
    checks.append(("심각 건 매니저 알림" if sample["expect_alert"] else "불필요한 매니저 알림 없음",
                   alerted == sample["expect_alert"]))

    exp_order = sample.get("expect_order_lookup")
    if exp_order is True:
        checks.append(("주문 대조 툴 호출", used.get("find_order_candidates", 0) >= 1))
    elif exp_order is False:
        checks.append(("불필요한 주문 조회 없음", used.get("find_order_candidates", 0) == 0))

    leak = guardrails.check_internal_leak(reply)
    checks.append(("답글에 내부 데이터 없음", not leak))
    comp = f.get("compensation")
    if comp and comp.get("applicable"):
        amt = f"{comp['proposed_amount_vnd']:,}"
        checks.append(("보상 금액이 답글에 없음",
                       amt not in reply and str(comp["proposed_amount_vnd"]) not in reply))
    checks.append(("degraded 아님", not f.get("degraded")))

    print()
    ok_all = True
    for name, ok in checks:
        print(f"  {'✔' if ok else '✘'} {name}")
        ok_all = ok_all and ok
    if leak:
        print(f"      누출: {leak[0][:100]}")
    print(f"\n  결과: {'PASS' if ok_all else 'FAIL'}  ({round(time.time()-t0,1)}s)")

    return {
        "id": sample["id"], "stage": sample["stage"], "ok": ok_all,
        "checks": {n: o for n, o in checks}, "final": f, "tools_used": used,
        "tool_order": [s["tool"] for s in out["steps"]
                       if s["type"] in ("tool_call", "final", "forced_hold")],
        "steps": [{k: v for k, v in s.items() if k != "result"} for s in out["steps"]],
        "elapsed": out["elapsed"], "usage": out["usage"], "retries": len(out["retries"]),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    ap.add_argument("--stage", type=int, default=None)
    args = ap.parse_args()

    print("NVIDIA 엔드포인트:", config.BASE_URL)
    print("메인:", config.MODEL_MAIN, "| 안전:", config.MODEL_SAFETY, "| 임베딩:", config.MODEL_EMBED)
    print("API 키:", config.key_fingerprint())
    print("\n정책 인덱스 준비…", end=" ", flush=True)
    idx = agent_tools.build_index()
    print(f"섹션 {len(idx['chunks'])}개 / {len(idx['vectors'][0])}차원", end="")
    from agent import orders as orders_mod
    print(f" | 주문 데이터 {len(orders_mod.all_orders())}건")

    targets = [s for s in samples.SAMPLES
               if (not args.only or s["id"] == args.only)
               and (args.stage is None or s["stage"] == args.stage)]
    a0, p0 = len(agent_tools.read_alerts(999)), len(agent_tools.read_pending(999))
    results = [run_one(s) for s in targets]
    alerts, pending = agent_tools.read_alerts(999), agent_tools.read_pending(999)

    print(f"\n{BAR}\n요약\n{BAR}")
    for r in results:
        st = r.get("final", {}).get("status", "?")
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['id']:<18} {st:<6} "
              f"{r.get('final',{}).get('severity','?'):<8} {r.get('elapsed','?')}s"
              + (f"  ← {r.get('error_kind','')} {r.get('error','')[:60]}" if not r["ok"] and r.get("error") else ""))
    passed = sum(1 for r in results if r["ok"])
    print(f"\n  {passed}/{len(results)} PASS")
    print(f"  신규 매니저 알림 {len(alerts)-a0}건 / 신규 승인 대기 {len(pending)-p0}건")
    for a in alerts[: len(alerts) - a0]:
        c = a.get("compensation") or {}
        print(f"    🔔 [{a['severity']}] {a['alert_id']} {a['summary'][:52]}"
              + (f"\n        보상 제안: {c.get('display')}" if c.get("applicable") else ""))
    for q in pending[: len(pending) - p0]:
        print(f"    ⏸️  {q['approval_id']} [{q['severity']}] {q['category']} — {q['status']}")

    report = config.ROOT / "logs" / "test_report.json"
    report.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  리포트: {report.relative_to(config.ROOT)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
