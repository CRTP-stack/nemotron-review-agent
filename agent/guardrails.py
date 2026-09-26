"""공개 답글 텍스트 가드레일 (순수 함수 — API 호출 없음).

G4 내부 데이터 노출 금지   — 주문번호/테이블번호/주문시각/"주문 내역 확인" 표현
G5 중량 분쟁 답글 규칙     — 고객 탓 금지, 테이블 계량 절차 안내 필수, 보상 금액 금지
G6 위생 클레임 답글 규칙   — 사실 인정·반박·보상 약속 금지, 개인 연락 채널 필수

정책 근거: data/policy.md §2, §4, §9, §11
"""
from __future__ import annotations

import re
from typing import List

CONTACT_EMAIL = "manager@haidang-danang.example"
CONTACT_PHONE_DIGITS = "2365550147"   # +84 236 555 0147 에서 숫자만

# ---------------------------------------------------------------- G4

ORDER_ID_RE = re.compile(r"HD-\d{8}-\d{2}", re.I)
# 한글은 \b 경계가 성립하지 않아(숫자+"번" 모두 word char) 숫자 뒤 lookahead 로 처리한다.
TABLE_RE = re.compile(
    r"(?:(?:테이블|table|bàn|стол\w*)\s*(?:번호|no\.?|số|№|#)?\s*\d{1,2}(?!\d)"
    r"|\d{1,2}\s*번\s*(?:테이블|table))",
    re.I,
)
ORDER_TIME_RE = re.compile(
    r"(주문|서빙|제공|ordered|served|order time|đặt món|phục vụ|заказ\w*|подач\w*)[^.。!?]{0,25}\d{1,2}:\d{2}",
    re.I,
)
LOOKUP_PHRASES = [
    "주문 내역", "주문내역", "주문 기록", "주문번호", "주문 번호", "주문 시각", "결제 내역",
    "조회해", "확인해 본 결과 주문", "전산",
    "order history", "order record", "order number", "your order id", "checked your order",
    "looked up your order", "in our system", "our records show", "according to our records",
    "mã đơn", "đơn hàng", "kiểm tra đơn", "hệ thống của chúng tôi",
    "номер заказа", "ваш заказ", "в нашей системе", "по нашим данным",
]


def check_internal_leak(text: str) -> List[str]:
    t = text or ""
    low = t.lower()
    v: List[str] = []
    if ORDER_ID_RE.search(t):
        v.append("G4 위반: 공개 답글에 주문번호가 노출되었다. 주문번호를 삭제하라.")
    if TABLE_RE.search(t):
        v.append("G4 위반: 공개 답글에 테이블 번호가 노출되었다. 테이블 번호를 삭제하라.")
    if ORDER_TIME_RE.search(t):
        v.append("G4 위반: 공개 답글에 주문·서빙 시각이 노출되었다. 시각 표기를 삭제하라.")
    hit = [p for p in LOOKUP_PHRASES if p in low]
    if hit:
        v.append(
            f"G4 위반: 내부 주문 데이터를 조회했다는 표현이 있다({', '.join(hit[:3])}). "
            "'주문 내역을 확인해 보니' 같은 표현 없이, 확인이 필요하면 개별 연락만 요청하라."
        )
    return v


# ---------------------------------------------------------------- 공통: 보상 금액 노출

MONEY_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})+\s*(?:₫|vnd|đồng|동|донг)", re.I)
PERCENT_RE = re.compile(r"\d{1,3}\s*%")
# 명사형만 넣으면 "вернём"(환불하겠다) 같은 동사 활용형을 놓친다. 어간까지 포함한다.
COMPENSATION_WORDS = [
    # ko
    "바우처", "쿠폰", "환불", "보상금", "배상", "무료로", "무상", "전액", "할인권",
    # en
    "voucher", "coupon", "refund", "reimburse", "compensat", "free meal", "complimentary",
    "at no charge", "on the house", "waive",
    # vi
    "phiếu", "hoàn tiền", "hoàn trả", "hoàn lại", "bồi thường", "miễn phí", "giảm giá",
    # ru (활용형 포함)
    "ваучер", "купон", "возврат", "вернём", "вернем", "вернуть", "вернёт", "вернет",
    "возмест", "компенсац", "бесплатн", "скидк",
]


def _compensation_hits(text: str) -> List[str]:
    low = (text or "").lower()
    hits = [w for w in COMPENSATION_WORDS if w in low]
    if MONEY_RE.search(text or ""):
        hits.append("금액 표기")
    if PERCENT_RE.search(text or ""):
        hits.append("비율 표기")
    return hits


# ---------------------------------------------------------------- G5 중량 분쟁

BLAME_PHRASES = [
    "착각", "잘못 기억", "오해하신", "확인하지 않으신", "동의하셨", "고객님의 실수", "고객님 실수",
    "you must have", "you misremember", "you are mistaken", "on your part", "you agreed",
    "you did not check", "you failed to", "you should have",
    "quý khách đã nhầm", "quý khách nhầm", "do quý khách", "quý khách đã đồng ý",
    "quý khách không kiểm tra",
    "вы ошиблись", "вы неправильно", "вы согласились", "вы не проверили", "ваша ошибка",
]
WEIGH_TOKENS = ["저울", "계량", "중량을 확인", "무게를 확인", "weigh", "scale", "cân ", "cân,",
                "đo lại", "весы", "взвеш", "взвешива"]


def check_weight_dispute(text: str) -> List[str]:
    t = text or ""
    low = t.lower()
    v: List[str] = []
    blame = [p for p in BLAME_PHRASES if p in low]
    if blame:
        v.append(
            f"G5 위반: 중량 분쟁 답글에서 고객을 탓하는 표현이 있다({', '.join(blame[:3])}). "
            "고객의 착오를 지적하지 말고, 테이블 계량 절차가 있다는 사실만 정중히 안내하라."
        )
    if not any(tok in low for tok in WEIGH_TOKENS):
        v.append(
            "G5 위반: 테이블에서 저울로 중량을 함께 확인하는 절차(정책 §4)를 답글에 안내하지 않았다. "
            "이 절차를 정중하게 언급하라."
        )
    comp = _compensation_hits(t)
    if comp:
        v.append(
            f"G5 위반: 공개 답글에 보상/금액을 언급했다({', '.join(comp[:3])}). "
            "보상안은 매니저 알림에만 넣고, 답글에서는 개별 연락으로만 안내하라."
        )
    return v


# ---------------------------------------------------------------- G6 위생 클레임

DENIAL_PHRASES = [
    "사실이 아닙니다", "있을 수 없", "그럴 리 없", "확인되지 않았습니다",
    "not true", "never happened", "cannot happen", "we did not", "no evidence",
    "không đúng sự thật", "không thể xảy ra", "không có chuyện",
    "неправда", "не могло", "не соответствует действительности",
]
ADMISSION_PHRASES = [
    "저희 잘못", "저희의 잘못", "저희 과실", "저희의 과실", "저희 실수", "우리 잘못",
    "our fault", "our mistake", "we are responsible", "we caused",
    "lỗi của chúng tôi", "do lỗi của nhà hàng", "chúng tôi đã sai",
    "наша вина", "наша ошибка", "мы виноваты",
]


def check_hygiene_reply(text: str) -> List[str]:
    t = text or ""
    low = t.lower()
    digits = re.sub(r"\D", "", t)
    v: List[str] = []

    if CONTACT_EMAIL.lower() not in low and CONTACT_PHONE_DIGITS not in digits:
        v.append(
            "G6 위반: 위생 클레임 답글에 개인 연락 채널이 없다. "
            f"{CONTACT_EMAIL} 또는 +84 236 555 0147 로 개별 연락을 요청하는 문장을 넣어라."
        )
    comp = _compensation_hits(t)
    if comp:
        v.append(
            f"G6 위반: 위생 클레임 답글에서 보상을 약속했다({', '.join(comp[:3])}). "
            "공개 답글에서는 환불·치료비·바우처를 일절 언급하지 말고 개별 연락으로만 유도하라(정책 §2)."
        )
    denial = [p for p in DENIAL_PHRASES if p in low]
    if denial:
        v.append(f"G6 위반: 사실관계를 반박했다({', '.join(denial[:2])}). 공개 답글에서 반박하지 마라.")
    admit = [p for p in ADMISSION_PHRASES if p in low]
    if admit:
        v.append(
            f"G6 위반: 사실관계 확정 전에 책임을 인정했다({', '.join(admit[:2])}). "
            "사과와 유감 표명은 하되 책임 인정 표현은 쓰지 마라."
        )
    return v


# ---------------------------------------------------------------- 디스패처

WEIGHT_CATEGORIES = {"가격/중량"}
HYGIENE_CATEGORIES = {"위생/식품안전"}


def inspect_reply(text: str, category: str) -> List[str]:
    """카테고리에 맞는 가드레일을 전부 적용해 위반 목록을 돌려준다."""
    v = check_internal_leak(text)
    if category in WEIGHT_CATEGORIES:
        v += check_weight_dispute(text)
    if category in HYGIENE_CATEGORIES:
        v += check_hygiene_reply(text)
    return v
