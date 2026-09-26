"""가상 주문 데이터 조회 · 후보 매칭 · 보상안 계산.

data/orders.json 은 이 데모를 위해 생성한 합성 데이터다(실제 거래 아님).
여기서 계산하는 보상 금액은 전부 '제안'이며, 실제 환불·결제 실행 기능은 없다.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from . import config

# 리뷰에 등장하는 메뉴 표현 → 주문 데이터의 품목을 잇기 위한 다국어 별칭.
# 리뷰어는 "랍스터"/"lobster"/"tôm hùm"/"лобстер" 중 아무거나 쓴다.
ITEM_ALIASES: Dict[str, List[str]] = {
    "tôm hùm": ["랍스터", "바닷가재", "lobster", "tôm hùm", "tom hum", "лобстер", "омар"],
    "cua hoàng đế": ["킹크랩", "킹 크랩", "대게", "king crab", "crab", "cua", "краб", "кинг"],
    "tôm sú": ["타이거새우", "새우", "tiger prawn", "prawn", "shrimp", "tôm sú", "tôm rang me",
                "tom rang me", "креветк", "тигров"],
    "cá mú": ["농어", "그루퍼", "생선", "grouper", "sea bass", "seabass", "fish", "cá mú", "ca mu",
               "окун", "рыб"],
    "nghêu": ["조개", "clam", "nghêu", "ngheu", "моллюск", "ракушк"],
    "mực": ["오징어", "squid", "calamari", "mực", "muc", "кальмар"],
    "ốc hương": ["골뱅이", "소라", "snail", "ốc hương", "oc huong", "улитк"],
    "rau muống": ["모닝글로리", "공심채", "morning glory", "rau muống", "rau muong", "шпинат"],
    "cơm chiên": ["볶음밥", "fried rice", "cơm chiên", "com chien", "рис"],
    "bia": ["맥주", "beer", "bia", "пиво"],
    "nước dừa": ["코코넛", "coconut", "nước dừa", "nuoc dua", "кокос"],
    "đậu hũ": ["두부", "tofu", "đậu hũ", "dau hu", "тофу"],
}

_cache: Optional[Dict[str, Any]] = None


def load() -> Dict[str, Any]:
    global _cache
    if _cache is None:
        _cache = json.loads(config.ORDERS_PATH.read_text(encoding="utf-8"))
    return _cache


def all_orders() -> List[Dict[str, Any]]:
    return load()["orders"]


def get(order_id: str) -> Optional[Dict[str, Any]]:
    for o in all_orders():
        if o["order_id"] == order_id:
            return o
    return None


def wait_minutes(order: Dict[str, Any]) -> int:
    h1, m1 = map(int, order["ordered_at"].split(":"))
    h2, m2 = map(int, order["served_at"].split(":"))
    return (h2 * 60 + m2) - (h1 * 60 + m1)


def _parse_date(s: str) -> Optional[date]:
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%m-%d", "%m/%d"):
        try:
            d = datetime.strptime(s.strip(), fmt).date()
            return d.replace(year=2026) if fmt in ("%m-%d", "%m/%d") else d
        except ValueError:
            continue
    return None


def _canonical_items(mentioned: List[str]) -> List[str]:
    """리뷰에서 언급된 메뉴 표현을 주문 데이터의 표준 품목명으로 환원."""
    blob = " ".join(mentioned).lower()
    return [canon for canon, aliases in ITEM_ALIASES.items()
            if any(a.lower() in blob for a in aliases)]


def _order_has(order: Dict[str, Any], canon: str) -> bool:
    return any(canon.lower() in it["name"].lower() for it in order["items"])


def find_candidates(
    review_date: str, mentioned_items: List[str], language: str = "", limit: int = 3
) -> Dict[str, Any]:
    """리뷰 정보로 주문 후보를 찾는다. **확정 매칭은 하지 않는다.**

    확신도 기준(고정):
      high     날짜 일치 + 언급 메뉴 1개 이상 일치 + 언어 일치
      medium   날짜 일치 + 언급 메뉴 1개 이상 일치 (언어 불일치 또는 미상)
      low      날짜 일치 + 언어 일치, 메뉴 언급이 없거나 불일치
      no_match 날짜만 일치, 또는 아무것도 일치하지 않음

    날짜만 맞는 건 근거로 치지 않는다. 매일 주문이 있으므로 방문 증거가 되지 못한다.
    low 후보만 3건 이상이면 특정 불가로 보고 no_match 로 떨어뜨린다.
    """
    rd = _parse_date(review_date or "")
    canon = _canonical_items(mentioned_items or [])
    lang = (language or "").lower().strip()
    date_provided = rd is not None

    scored: List[Tuple[int, Dict[str, Any]]] = []
    for o in all_orders():
        od = _parse_date(o["date"])
        # 날짜 일치는 후보의 전제 조건이다. 방문일이 없거나 다르면 후보가 아니다.
        if not (date_provided and od == rd):
            continue

        matched = [c for c in canon if _order_has(o, c)]
        lang_match = bool(lang) and lang == o["language"]

        if matched and lang_match:
            conf = "high"
        elif matched:
            conf = "medium"
        elif lang_match:
            conf = "low"
        else:
            continue  # 날짜만 일치 → 근거 없음

        reasons = [f"방문일 일치({o['date']})"]
        reasons.append("메뉴 일치: " + ", ".join(matched) if matched else "언급 메뉴 불일치/없음")
        reasons.append(f"주문 언어 일치({lang})" if lang_match else "주문 언어 불일치/미상")

        rank = {"high": 3, "medium": 2, "low": 1}[conf]
        scored.append((rank, {
            "order_id": o["order_id"],
            "date": o["date"],
            "match_confidence": conf,
            "reasons": reasons,
            "wait_minutes": wait_minutes(o),
            "weighed_at_table": o["weighed_at_table"],
            "total_vnd": o["total_vnd"],
            "items": [{"name": it["name"], "weight_kg": it["weight_kg"],
                       "price_per_kg": it["price_per_kg"], "line_total": it["line_total"]}
                      for it in o["items"]],
        }))

    scored.sort(key=lambda x: x[0], reverse=True)
    candidates = [c for _, c in scored[:limit]]

    # low 만 3건 이상이면 특정 불가
    low_only = bool(candidates) and all(c["match_confidence"] == "low" for c in candidates)
    too_many_low = low_only and len([c for _, c in scored if c["match_confidence"] == "low"]) >= 3

    if not candidates or too_many_low:
        verdict = "no_match"
        candidates = [] if not candidates else candidates
        if not date_provided:
            note = ("리뷰에 방문일이 없어 주문을 특정할 수 없다. "
                    "방문 사실 자체를 다투는 건이 아니라면 주문 대조 없이 일반 정책으로 처리하라. "
                    "주문 정보를 추측해서 답글에 쓰지 마라.")
        elif too_many_low:
            note = ("날짜만 맞는 후보가 여러 건이라 주문을 특정할 수 없다. "
                    "'방문 확인 불가' 경로로 처리하라: 짧고 중립적인 답글 + 매니저 에스컬레이션. "
                    "고객을 의심하거나 반박하지 말고 개별 연락으로 확인을 요청하라.")
        else:
            note = ("해당 방문일·메뉴에 맞는 주문이 없다. 방문 사실을 확인할 수 없는 리뷰다. "
                    "'방문 확인 불가' 경로로 처리하라: 짧고 중립적인 답글 + 매니저 에스컬레이션. "
                    "고객을 거짓말쟁이로 몰지 말고, 주문 정보를 언급하지도 말고, "
                    "확인을 위해 개인 연락 채널로만 정중히 안내하라.")
    else:
        verdict = candidates[0]["match_confidence"]
        if verdict in ("high", "medium"):
            note = ("이것은 후보일 뿐 확정된 매칭이 아니다. 판단 근거로 쓸 수 있다. "
                    "단, 주문번호·테이블번호·주문시각·금액을 공개 답글에 절대 쓰지 마라. "
                    "이 정보는 매니저 알림과 내부 판단에만 쓴다.")
        else:
            note = ("확신도가 low 다(날짜와 언어만 일치). 주문 데이터를 판단 근거로 쓰지 마라. "
                    "'방문 확인 불가' 경로로 처리하라: 짧고 중립적인 답글 + 매니저 에스컬레이션.")

    return {
        "query": {"review_date": review_date, "mentioned_items": mentioned_items,
                  "language": language},
        "date_provided": date_provided,
        "resolved_items": canon,
        "verdict": verdict,
        "usable_as_evidence": verdict in ("high", "medium"),
        "candidates": candidates,
        "note": note,
    }


# ------------------------------------------------------------------ 보상안 계산
# 아래 비율은 data/policy.md "9. 보상 규칙" 조항을 코드로 옮긴 것이다.
# 정책을 고치면 여기도 같이 고쳐야 한다.
COMPENSATION_RULES: Dict[str, Dict[str, Any]] = {
    "weight_dispute":  {"rate": 0.30, "form": "다음 방문 바우처", "policy": "§9-1 중량 이견"},
    "weight_shortage": {"rate": 2.00, "form": "차액의 2배 환불",   "policy": "§4 중량 오차 확인 시"},
    "wait_over_45":    {"rate": 0.00, "form": "웰컴 드링크 1잔",   "policy": "§3 대기 45분 초과"},
    "wait_over_60":    {"rate": 0.10, "form": "다음 방문 할인 쿠폰", "policy": "§3 대기 60분 초과"},
    "cooking_error":   {"rate": 1.00, "form": "해당 품목 전액 환불", "policy": "§1 조리 오류"},
    "hygiene":         {"rate": 1.00, "form": "테이블 전액 환불(매니저 개별 안내)",
                        "policy": "§2 위생 사고"},
    "service":         {"rate": 0.10, "form": "다음 방문 바우처",   "policy": "§9-3 서비스 미흡"},
}


def calc_compensation(issue_type: str, order_id: str = "", item_hint: str = "") -> Dict[str, Any]:
    """정책 §9 보상 규칙으로 '제안 금액'을 계산한다. 실제 환불 실행은 하지 않는다."""
    rule = COMPENSATION_RULES.get(issue_type)
    if not rule:
        return {"applicable": False,
                "reason": f"'{issue_type}' 에 해당하는 보상 규칙이 정책에 없음 → 보상 제안 없음"}

    order = get(order_id) if order_id else None
    if order is None:
        return {"applicable": False, "issue_type": issue_type, "policy_basis": rule["policy"],
                "form": rule["form"],
                "reason": "주문을 특정하지 못해 금액 산정 불가 → 매니저가 수기 확인 필요"}

    if issue_type == "hygiene":
        base, base_label = order["total_vnd"], "테이블 총액"
    else:
        target = None
        if item_hint:
            canon = _canonical_items([item_hint])
            for it in order["items"]:
                if any(c.lower() in it["name"].lower() for c in canon):
                    target = it
                    break
        if target is None:  # 힌트가 없으면 가장 비싼 품목 기준
            target = max(order["items"], key=lambda x: x["line_total"])
        base, base_label = target["line_total"], target["name"]

    amount = int(round(base * rule["rate"], -3))
    return {
        "applicable": True,
        "issue_type": issue_type,
        "policy_basis": rule["policy"],
        "form": rule["form"],
        "base_label": base_label,
        "base_vnd": base,
        "rate": rule["rate"],
        "proposed_amount_vnd": amount,
        "display": f"{amount:,}₫ ({rule['form']}, 기준 {base_label} {base:,}₫ × {rule['rate']:.0%})",
        "disclaimer": "제안 금액이며 실제 환불·결제는 실행되지 않았다. 매니저 승인 후 수기 처리.",
    }
