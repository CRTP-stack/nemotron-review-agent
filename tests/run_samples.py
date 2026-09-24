"""샘플 4건 자동 테스트.

사용법:  .venv/bin/python tests/run_samples.py [--only ko-wait]
결과는 logs/test_report.json 에 저장된다.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import config, loop, samples  # noqa: E402
from agent import tools as agent_tools  # noqa: E402

BAR = "─" * 78


def brief(result, maxlen=160):
    s = json.dumps(result, ensure_ascii=False)
    return s if len(s) <= maxlen else s[: maxlen - 1] + "…"


def run_one(sample):
    print(f"\n{BAR}\n▶ {sample['label']}  [{sample['id']}]\n{BAR}")
    print(f"리뷰: {sample['text'][:110]}…\n")
    t0 = time.time()
    try:
        out = loop.run_agent(sample["text"])
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 실행 실패: {type(exc).__name__}: {exc}")
        return {"id": sample["id"], "ok": False, "error": str(exc)}

    for st in out["steps"]:
        icon = {"tool_call": "🔧", "final": "✅", "verify": "🛡️", "reject": "⛔", "nudge": "↩️"}.get(
            st["type"], "•"
        )
        actor = "에이전트 결정" if st["actor"] == "agent" else "가드레일"
        print(f"  {icon} step{st['n']} [{actor}] {st['tool']}  ({st['elapsed']}s)")
        if st.get("reasoning"):
            print(f"      why: {st['reasoning'][:150]}")
        if st.get("args") and st["tool"] != "submit_reply":
            print(f"      args: {brief(st['args'], 120)}")
        if st.get("result"):
            print(f"      → {brief(st['result'])}")

    f = out["final"]
    print(f"\n  분류: language={f.get('language')} / category={f.get('category')} / "
          f"sentiment={f.get('sentiment')} / severity={f.get('severity')} "
          f"/ 답글안전={f.get('reply_safety')}")
    print(f"  답글:\n      " + (f.get("reply_text", "") or "").replace("\n", "\n      "))
    print(f"\n  사용 툴: {out['tools_used']} | {out['elapsed']}s | "
          f"{out['usage']['calls']}콜 {out['usage']['total_tokens']}토큰")
    if out["retries"]:
        print(f"  재시도: {out['retries']}")

    # --- 검증 ---
    checks = []
    checks.append(("리뷰 안전검사 수행", out["tools_used"].get("check_safety", 0) >= 1))
    checks.append(("정책 검색 수행", out["tools_used"].get("search_policy", 0) >= 1))
    checks.append(("답글 언어 일치", f.get("language") == sample["lang"]))
    checks.append(("답글 비어있지 않음", bool((f.get("reply_text") or "").strip())))
    alerted = out["tools_used"].get("notify_manager", 0) >= 1
    if sample["expect_alert"]:
        checks.append(("심각 건 매니저 알림", alerted))
        checks.append(("severity high/critical", f.get("severity") in ("high", "critical")))
    else:
        checks.append(("불필요한 매니저 알림 없음", not alerted))
    checks.append(("degraded 아님", not f.get("degraded")))

    print()
    ok_all = True
    for name, ok in checks:
        print(f"  {'✔' if ok else '✘'} {name}")
        ok_all = ok_all and ok
    print(f"\n  결과: {'PASS' if ok_all else 'FAIL'}  ({round(time.time()-t0,1)}s)")

    return {
        "id": sample["id"], "ok": ok_all,
        "checks": {n: o for n, o in checks},
        "final": f, "tools_used": out["tools_used"],
        "steps": [{k: v for k, v in s.items() if k != "result"} for s in out["steps"]],
        "elapsed": out["elapsed"], "usage": out["usage"],
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", default=None)
    args = ap.parse_args()

    print("NVIDIA 엔드포인트:", config.BASE_URL)
    print("메인:", config.MODEL_MAIN, "| 안전:", config.MODEL_SAFETY, "| 임베딩:", config.MODEL_EMBED)
    print("API 키:", config.key_fingerprint())
    print("\n정책 인덱스 생성 중…", end=" ", flush=True)
    idx = agent_tools.build_index()
    print(f"섹션 {len(idx['chunks'])}개 / {len(idx['vectors'][0])}차원")

    targets = [s for s in samples.SAMPLES if (not args.only or s["id"] == args.only)]
    before = len(agent_tools.read_alerts(limit=999))
    results = [run_one(s) for s in targets]
    after = agent_tools.read_alerts(limit=999)

    print(f"\n{BAR}\n요약\n{BAR}")
    for r in results:
        print(f"  {'PASS' if r['ok'] else 'FAIL'}  {r['id']:<12} "
              f"{r.get('final',{}).get('category','?')} / {r.get('final',{}).get('severity','?')} "
              f"/ {r.get('elapsed','?')}s")
    passed = sum(1 for r in results if r["ok"])
    print(f"\n  {passed}/{len(results)} PASS")
    print(f"  신규 매니저 알림: {len(after) - before}건 → logs/alerts.jsonl")
    for a in after[: len(after) - before]:
        print(f"    [{a['severity']}] {a['alert_id']} {a['summary'][:70]}")

    report = config.ROOT / "logs" / "test_report.json"
    report.write_text(json.dumps({"results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n  리포트: {report.relative_to(config.ROOT)}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
