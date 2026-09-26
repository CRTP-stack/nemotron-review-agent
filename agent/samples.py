"""데모용 샘플 리뷰.

[1단계 세트] 기본 4건 — 언어별 응대와 위생 에스컬레이션
[2단계 세트] 5건 — 주문 데이터 대조(find_order_candidates)와 승인 대기 흐름

모든 리뷰는 가상 매장 Hải Đăng Seafood 를 전제로 이 데모를 위해 작성한 허구다.
2단계 샘플은 data/orders.json 의 특정 주문을 겨냥하도록 방문일·메뉴를 맞춰 두었다.
"""
from __future__ import annotations

from typing import Any, Dict, List

# expect_status      : "자동 게시" | "승인 대기"
# expect_alert       : notify_manager 호출 기대 여부
# expect_order_lookup: True=반드시 호출 / False=호출하면 안 됨 / None=상관없음
# expect_policy_search: 정책 근거가 필요한 리뷰인지(순수 칭찬은 불필요)
SAMPLES: List[Dict[str, Any]] = [
    # ------------------------------------------------ 1단계 세트
    {
        "id": "ko-wait", "stage": 1, "lang": "ko",
        "label": "🇰🇷 한국어 · 칭찬 + 대기 불만",
        "expect_status": "자동 게시", "expect_alert": False, "expect_order_lookup": None,
        "text": (
            "가족들이랑 다녀왔는데 타이거새우 구이랑 모닝글로리 볶음 진짜 맛있었어요! "
            "사장님도 친절하시고요. 다만 저녁 7시쯤 갔더니 자리 날 때까지 50분 넘게 기다렸습니다. "
            "대기 순서 안내가 전혀 없어서 그냥 서서 기다렸네요. 대기 관리는 좀 아쉽습니다."
        ),
    },
    {
        "id": "en-weight", "stage": 1, "lang": "en",
        "label": "🇺🇸 English · 중량/가격 분쟁",
        # 정책 §10 개정으로 중량·금액 분쟁은 critical → 승인 대기가 맞다(1단계 기대값에서 변경).
        "expect_status": "승인 대기", "expect_alert": True, "expect_order_lookup": None,
        "text": (
            "We picked a lobster from the tank and the staff told us it was about 1.2kg, "
            "but the final bill charged us for 1.6kg. When I asked them to weigh it again, "
            "nobody would help and they just kept saying it's the market price. "
            "The food itself was decent but I left feeling ripped off."
        ),
    },
    {
        "id": "vi-hygiene", "stage": 1, "lang": "vi",
        "label": "🇻🇳 Tiếng Việt · ⚠️ 위생 심각 건",
        "expect_status": "승인 대기", "expect_alert": True, "expect_order_lookup": None,
        "text": (
            "Tối qua tôi ăn ở đây với bạn. Trong đĩa tôm rang me có một con gián chết. "
            "Về nhà tôi bị đau bụng và tiêu chảy cả đêm, sáng nay phải đi bệnh viện truyền nước. "
            "Nhân viên lúc đó chỉ nói xin lỗi rồi bỏ đi. Quá kinh khủng, "
            "tôi sẽ báo cơ quan y tế và kiện nhà hàng này."
        ),
    },
    {
        "id": "ru-refund", "stage": 1, "lang": "ru",
        "label": "🇷🇺 Русский · 환불 요청",
        "expect_status": "자동 게시", "expect_alert": False, "expect_order_lookup": None,
        "text": (
            "Заказали краба и морского окуня на гриле. Краб оказался настолько пересоленным, "
            "что есть было невозможно. Позвали официанта, но он только пожал плечами. "
            "Мы были там позавчера, чек у меня сохранился. "
            "Хотелось бы вернуть деньги хотя бы за краба."
        ),
    },
    # ------------------------------------------------ 2단계 세트 (주문 대조)
    {
        "id": "ru-weight-order", "stage": 2, "lang": "ru",
        "label": "🇷🇺 무게 분쟁 (주문 대조)",
        "expect_status": "승인 대기", "expect_alert": True, "expect_order_lookup": True,
        "target_order": "HD-20260922-05",
        "text": (
            "Были у вас 22 сентября 2026 года вечером. Выбрали лобстера, на весах у стола "
            "показали 1,2 кг, я это видел своими глазами. Но в счёте сумма оказалась заметно "
            "больше, чем 1,2 кг по цене за килограмм. Никто не смог объяснить разницу. "
            "Прошу разобраться и пересчитать счёт."
        ),
    },
    {
        "id": "en-wait-order", "stage": 2, "lang": "en",
        "label": "🇺🇸 대기 48분 (주문 대조)",
        "expect_status": "자동 게시", "expect_alert": False, "expect_order_lookup": True,
        "target_order": "HD-20260921-03",
        "text": (
            "We came on 21 September 2026 for dinner and ordered the tamarind tiger prawns. "
            "It took almost fifty minutes from ordering until the food actually reached our table. "
            "Nobody told us there was a delay, and we had to ask twice. "
            "The prawns were good but that wait was really hard with a hungry kid."
        ),
    },
    {
        "id": "vi-hygiene-order", "stage": 2, "lang": "vi",
        "label": "🇻🇳 위생 클레임 (주문 대조)",
        "expect_status": "승인 대기", "expect_alert": True, "expect_order_lookup": True,
        "target_order": "HD-20260923-02",
        "text": (
            "Ngày 23 tháng 9 năm 2026 tôi có ăn món tôm sú rang me ở đây. "
            "Khi ăn gần hết thì tôi thấy một mảnh nhựa cứng màu xanh trong đĩa. "
            "Tối đó tôi bị đau bụng và nôn hai lần. Tôi đã giữ lại mảnh nhựa đó và có chụp ảnh. "
            "Tôi muốn nhà hàng giải thích rõ chuyện này."
        ),
    },
    {
        "id": "en-unverified", "stage": 2, "lang": "en",
        "label": "🇺🇸 ⚠️ 방문 확인 불가 (악성)",
        "expect_status": "승인 대기", "expect_alert": True, "expect_order_lookup": True,
        "target_order": None,   # orders.json 에 해당 방문 기록 없음
        "text": (
            "I ate at your restaurant on 14 September 2026 and ordered the king crab. "
            "I got severe food poisoning and spent two days in bed. Your kitchen is filthy. "
            "I want 20 million dong in compensation or I will post this everywhere "
            "and report you to the health department."
        ),
    },
    {
        "id": "ko-praise", "stage": 2, "lang": "ko",
        "label": "🇰🇷 5점 칭찬 (주문 툴 불필요)",
        "expect_status": "자동 게시", "expect_alert": False, "expect_order_lookup": False,
        "expect_policy_search": False,   # 약속할 보상이 없어 정책 근거가 필요 없다
        "target_order": "HD-20260922-12",
        "text": (
            "9월 22일 저녁에 방문했어요. 타이거새우 타마린드 볶음이랑 모닝글로리, 해산물 볶음밥 시켰는데 "
            "전부 다 맛있었습니다! 음식도 금방 나왔고 직원분들도 웃으면서 응대해 주셔서 기분 좋았어요. "
            "다낭 오면 또 올게요. 별 다섯 개 드립니다 ⭐️⭐️⭐️⭐️⭐️"
        ),
    },
]


def by_id(sample_id: str) -> Dict[str, Any]:
    for s in SAMPLES:
        if s["id"] == sample_id:
            return s
    raise KeyError(sample_id)


def by_stage(stage: int) -> List[Dict[str, Any]]:
    return [s for s in SAMPLES if s["stage"] == stage]
