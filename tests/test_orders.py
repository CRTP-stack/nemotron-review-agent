"""주문 후보 매칭 확신도 규칙 단위 테스트 (API 호출 없음).

확신도 기준(고정):
  high     날짜 일치 + 언급 메뉴 1개 이상 일치 + 언어 일치
  medium   날짜 일치 + 언급 메뉴 1개 이상 일치 (언어 불일치/미상)
  low      날짜 일치 + 언어 일치, 메뉴 언급 없음/불일치
  no_match 날짜만 일치, 또는 아무것도 일치하지 않음, 또는 low 후보 3건 이상
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent import orders  # noqa: E402

CASES = [
    # (설명, review_date, mentioned_items, language, 기대 verdict, 기대 근거사용)
    ("날짜+메뉴+언어 일치",            "2026-09-22", ["лобстер"],     "ru", "high",     True),
    ("날짜+메뉴 일치, 언어 불일치",      "2026-09-22", ["лобстер"],     "en", "medium",   True),
    ("날짜+메뉴 일치, 언어 미상",        "2026-09-22", ["лобстер"],     "",   "medium",   True),
    ("날짜+언어 일치, 메뉴 미언급",      "2026-09-21", [],              "en", "low",      False),
    ("날짜만 일치 (메뉴·언어 불일치)",   "2026-09-22", ["pizza"],       "de", "no_match", False),
    ("아무것도 일치 안 함",             "2026-09-14", ["king crab"],   "en", "no_match", False),
    ("방문일 미기재",                  "",           ["лобстер"],     "ru", "no_match", False),
    ("low 후보 3건 이상 → 특정 불가",   "2026-09-22", [],              "ru", "no_match", False),
]


def test_confidence_rules():
    bad = []
    for desc, d, items, lang, exp_v, exp_u in CASES:
        r = orders.find_candidates(d, items, lang)
        got_v, got_u = r["verdict"], r["usable_as_evidence"]
        ok = (got_v == exp_v and got_u == exp_u)
        print(f"  {'OK' if ok else 'NG'} {desc:<30} → {got_v:<8} 근거사용={got_u}"
              + ("" if ok else f"   (기대 {exp_v}/{exp_u})"))
        if not ok:
            bad.append(desc)
    assert not bad, f"확신도 규칙 불일치: {bad}"
    return "확신도 8케이스 전부 규칙대로"


def test_scenario_c_no_match():
    """시나리오 c: 리뷰가 주장한 방문일·메뉴에 해당하는 주문이 없다."""
    r = orders.find_candidates("2026-09-14", ["king crab", "crab"], "en")
    assert r["verdict"] == "no_match", f"no_match 여야 하는데 {r['verdict']}"
    assert r["candidates"] == [], "후보가 남아 있으면 안 됨"
    assert r["usable_as_evidence"] is False
    assert r["date_provided"] is True, "방문일은 리뷰에 적혀 있었다"
    assert "방문 확인 불가" in r["note"], "방문 확인 불가 경로 안내가 없음"
    assert "거짓말쟁이로 몰지" in r["note"], "고객 반박 금지 안내가 없음"
    # orders.json 에 실제로 해당 날짜 주문이 없는지 직접 확인
    assert not [o for o in orders.all_orders() if o["date"] == "2026-09-14"], \
        "테스트 전제 붕괴: 2026-09-14 주문이 생겼다"
    return "시나리오 c: 9/14 킹크랩 → no_match + '방문 확인 불가' 경로 안내"


def test_low_only_three():
    """low 후보가 3건 이상이면 특정 불가로 떨어진다."""
    r = orders.find_candidates("2026-09-22", [], "ru")
    assert r["verdict"] == "no_match", r["verdict"]
    assert len(r["candidates"]) >= 3 and all(
        c["match_confidence"] == "low" for c in r["candidates"]), "low 후보 3건이 아님"
    assert "특정할 수 없다" in r["note"]
    return "low 후보 3건 이상 → no_match(특정 불가), 후보는 참고용으로만 표시"


def test_date_only_is_not_evidence():
    """날짜만 맞는 주문은 후보에 들어가면 안 된다."""
    r = orders.find_candidates("2026-09-23", ["pizza"], "de")
    assert r["verdict"] == "no_match"
    same_day = [o for o in orders.all_orders() if o["date"] == "2026-09-23"]
    assert len(same_day) >= 2, "테스트 전제: 해당 날짜에 주문이 여러 건 있어야 의미가 있다"
    assert r["candidates"] == [], f"날짜만 맞는 {len(same_day)}건이 후보로 새어 들어옴"
    return f"날짜만 일치하는 주문 {len(same_day)}건 전부 후보에서 제외"


def main():
    print("주문 매칭 확신도 테스트 (API 호출 없음)\n" + "─" * 66)
    ok = True
    for fn in (test_confidence_rules, test_scenario_c_no_match,
               test_low_only_three, test_date_only_is_not_evidence):
        try:
            print(f"  ▸ {fn()}")
        except AssertionError as exc:
            ok = False
            print(f"  NG {fn.__name__}: {exc}")
    print("─" * 66)
    print("  PASS" if ok else "  FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
