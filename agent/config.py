"""환경 설정 및 모델 상수.

API 키는 .env 에서만 읽고, 어떤 경로로도 화면/로그에 출력하지 않는다.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

BASE_URL = os.getenv("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1")

# 메인 판단/답글 모델. tool calling 지원 확인 완료(reasoning_content 분리 출력).
MODEL_MAIN = "nvidia/nemotron-3-ultra-550b-a55b"
# 안전 필터. 출력 포맷 고정: "User Safety: safe|unsafe" (+ assistant 동봉 시 "Response Safety: ...")
MODEL_SAFETY = "nvidia/nemotron-3.5-content-safety"
# 정책 검색용 임베딩. 2048차원, input_type=query|passage
MODEL_EMBED = "nvidia/nemotron-3-embed-1b"

POLICY_PATH = ROOT / "data" / "policy.md"
ORDERS_PATH = ROOT / "data" / "orders.json"        # 가상 주문 데이터(합성)
INDEX_PATH = ROOT / "data" / "policy_index.json"
ALERTS_PATH = ROOT / "logs" / "alerts.jsonl"
PENDING_PATH = ROOT / "logs" / "pending_approvals.jsonl"   # 승인 대기 큐

MAX_AGENT_STEPS = 12         # 가드레일 반려→재작업 왕복이 늘어 여유를 둔다
REQUEST_TIMEOUT = 120.0
MAX_RETRIES = 6              # 500/503 이 연속 발생하는 구간이 있어 넉넉히 잡는다


def get_api_key() -> str:
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY 가 설정되지 않았습니다. .env.example 을 .env 로 복사한 뒤 키를 넣어주세요."
        )
    return key


def key_fingerprint() -> str:
    """키 노출 없이 '설정됨' 을 표시하기 위한 지문(앞 6자 + 길이)."""
    key = os.getenv("NVIDIA_API_KEY", "").strip()
    if not key:
        return "미설정"
    return f"{key[:6]}…({len(key)}자)"
