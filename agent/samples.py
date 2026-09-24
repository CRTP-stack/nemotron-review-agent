"""데모용 샘플 리뷰 4건 (한국어 / 영어 / 베트남어 / 러시아어).

3번(vi)이 위생 심각 건이며, 에이전트가 스스로 notify_manager 를 호출해야 하는 케이스다.
각 샘플은 policy.md 의 서로 다른 섹션을 건드리도록 설계했다.
"""
from __future__ import annotations

from typing import Any, Dict, List

SAMPLES: List[Dict[str, Any]] = [
    {
        "id": "ko-wait",
        "label": "🇰🇷 한국어 · 칭찬 + 대기 불만",
        "lang": "ko",
        "expect_policy": "대기 시간",
        "expect_alert": False,
        "text": (
            "가족들이랑 다녀왔는데 타이거새우 구이랑 모닝글로리 볶음 진짜 맛있었어요! "
            "사장님도 친절하시고요. 다만 저녁 7시쯤 갔더니 자리 날 때까지 50분 넘게 기다렸습니다. "
            "대기 순서 안내가 전혀 없어서 그냥 서서 기다렸네요. 대기 관리는 좀 아쉽습니다."
        ),
    },
    {
        "id": "en-weight",
        "label": "🇺🇸 English · 중량/가격 분쟁",
        "lang": "en",
        "expect_policy": "중량",
        "expect_alert": False,
        "text": (
            "We picked a lobster from the tank and the staff told us it was about 1.2kg, "
            "but the final bill charged us for 1.6kg. When I asked them to weigh it again, "
            "nobody would help and they just kept saying it's the market price. "
            "The food itself was decent but I left feeling ripped off."
        ),
    },
    {
        "id": "vi-hygiene",
        "label": "🇻🇳 Tiếng Việt · ⚠️ 위생 심각 건",
        "lang": "vi",
        "expect_policy": "위생",
        "expect_alert": True,
        "text": (
            "Tối qua tôi ăn ở đây với bạn. Trong đĩa tôm rang me có một con gián chết. "
            "Về nhà tôi bị đau bụng và tiêu chảy cả đêm, sáng nay phải đi bệnh viện truyền nước. "
            "Nhân viên lúc đó chỉ nói xin lỗi rồi bỏ đi. Quá kinh khủng, "
            "tôi sẽ báo cơ quan y tế và kiện nhà hàng này."
        ),
    },
    {
        "id": "ru-refund",
        "label": "🇷🇺 Русский · 환불 요청",
        "lang": "ru",
        "expect_policy": "환불",
        "expect_alert": False,
        "text": (
            "Заказали краба и морского окуня на гриле. Краб оказался настолько пересоленным, "
            "что есть было невозможно. Позвали официанта, но он только пожал плечами. "
            "Мы были там позавчера, чек у меня сохранился. "
            "Хотелось бы вернуть деньги хотя бы за краба."
        ),
    },
]


def by_id(sample_id: str) -> Dict[str, Any]:
    for s in SAMPLES:
        if s["id"] == sample_id:
            return s
    raise KeyError(sample_id)
