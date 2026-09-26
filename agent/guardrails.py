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
    # "기록을 찾을 수 없다" 류 — 내부 조회 사실을 드러내고 고객 주장을 간접 부인한다
    "기록이 없", "기록을 찾을 수 없", "기록이 확인되지", "방문 기록",
    "no record of your visit", "could not find your", "couldn't find your",
    "no matching order", "unable to locate your", "we have no record",
    "không tìm thấy", "không có ghi nhận", "không có đơn nào",
    "не смогли найти", "не нашли запись", "нет записи", "не удалось найти",
    "отсутствует запись",
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


def check_compensation_promise(text: str) -> List[str]:
    """G8 (전 카테고리 공통): 공개 답글의 보상 약속 금지.

    정책 §9 — 환불·금액·무료 제공·바우처·쿠폰·할인은 카테고리를 가리지 않고
    공개 답글에 쓰지 않는다. 보상은 매니저 개별 연락으로만 안내한다.
    """
    hits = _compensation_hits(text)
    if not hits:
        return []
    return [
        f"G8 위반: 공개 답글에서 보상을 약속했다({', '.join(hits[:3])}). "
        "환불·금액·무료 제공·바우처·할인은 어떤 카테고리에서도 공개 답글에 쓸 수 없다. "
        "보상안은 매니저 알림에만 남기고, 답글에서는 매니저가 직접 연락드린다고만 안내하라(정책 §9)."
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
    return v   # 보상 금액 언급은 G8(전 카테고리 공통)에서 잡는다


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
    # 보상 약속은 G8(전 카테고리 공통)에서 잡는다.
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


# ---------------------------------------------------------------- G9 답글 언어

_HANGUL = re.compile(r"[가-힣]")
_CYRILLIC = re.compile(r"[а-яёА-ЯЁ]")
_LATIN = re.compile(r"[A-Za-zÀ-ỹ]")
# 베트남어 고유 자모(성조 포함) — en 과 vi 를 구분하는 데 쓴다
_VI_DIACRITIC = re.compile(
    r"[ăâđêôơưĂÂĐÊÔƠƯáàảãạấầẩẫậắằẳẵặéèẻẽẹếềểễệíìỉĩịóòỏõọốồổỗộớờởỡợúùủũụứừửữựýỳỷỹỵ]"
)
_STRIP = [
    (re.compile(r"\S+@\S+"), " "),                 # 이메일
    (re.compile(r"https?://\S+"), " "),             # URL
    (re.compile(r"[+\d][\d\s\-().]{6,}"), " "),     # 전화번호
    (re.compile(r"H[aả]i\s*Đ[ăa]ng\s*Seafood", re.I), " "),  # 매장명(고유명사)
]


def _script_counts(text: str) -> dict:
    t = text or ""
    for rx, rep in _STRIP:
        t = rx.sub(rep, t)
    return {
        "hangul": len(_HANGUL.findall(t)),
        "cyrillic": len(_CYRILLIC.findall(t)),
        "latin": len(_LATIN.findall(t)),
        "vi_diacritic": len(_VI_DIACRITIC.findall(t)),
    }


def check_reply_language(text: str, language: str) -> List[str]:
    """G9: 답글이 실제로 리뷰어의 언어로 쓰였는지 문자 체계로 검증한다.

    모델이 language 필드에는 'ru' 라고 써놓고 본문을 한국어로 쓰는 경우가 실측으로 나왔다.
    선언값을 믿지 않고 본문 글자를 직접 센다.
    """
    lang = (language or "").lower()
    c = _script_counts(text)
    total = c["hangul"] + c["cyrillic"] + c["latin"]
    if total < 20:
        return []   # 너무 짧으면 판정하지 않는다

    def fail(expected: str) -> List[str]:
        got = max(("한글", c["hangul"]), ("키릴", c["cyrillic"]), ("로마자", c["latin"]),
                  key=lambda x: x[1])[0]
        return [
            f"G9 위반: language='{lang}' 로 제출했는데 답글 본문이 {expected} 가 아니다"
            f"(실제 우세 문자: {got}; 한글 {c['hangul']} / 키릴 {c['cyrillic']} / 로마자 {c['latin']}). "
            f"리뷰어가 쓴 언어 그대로 답글을 다시 작성하라."
        ]

    if lang == "ko":
        return [] if c["hangul"] >= 10 and c["hangul"] > c["cyrillic"] else fail("한국어")
    if lang == "ru":
        return [] if c["cyrillic"] >= 10 and c["cyrillic"] > c["hangul"] else fail("러시아어")
    if lang == "vi":
        if c["hangul"] or c["cyrillic"]:
            return fail("베트남어")
        return [] if c["vi_diacritic"] >= 3 else [
            "G9 위반: language='vi' 인데 베트남어 성조 부호가 거의 없다. "
            "베트남어로 다시 작성하라."
        ]
    if lang == "en":
        return fail("영어") if (c["hangul"] >= 5 or c["cyrillic"] >= 5) else []
    return []


# ---------------------------------------------------------------- 디스패처

WEIGHT_CATEGORIES = {"가격/중량"}
HYGIENE_CATEGORIES = {"위생/식품안전"}


def inspect_reply(text: str, category: str, language: str = "") -> List[str]:
    """카테고리에 맞는 가드레일을 전부 적용해 위반 목록을 돌려준다."""
    v = check_internal_leak(text)          # G4: 전 카테고리
    v += check_compensation_promise(text)  # G8: 전 카테고리
    if language:
        v += check_reply_language(text, language)   # G9: 전 카테고리
    if category in WEIGHT_CATEGORIES:
        v += check_weight_dispute(text)
    if category in HYGIENE_CATEGORIES:
        v += check_hygiene_reply(text)
    return v
