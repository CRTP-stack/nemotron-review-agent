"""Streamlit 데모 — NVIDIA Nemotron 다국어 리뷰 응대 에이전트.

실행:  .venv/bin/streamlit run app.py
"""
from __future__ import annotations

import json

import streamlit as st

from agent import config, loop, samples
from agent import tools as agent_tools

st.set_page_config(page_title="Nemotron 리뷰 응대 에이전트", page_icon="🦐", layout="wide")

STEP_STYLE = {
    "tool_call": ("🔧", "#76b900"),
    "final": ("✅", "#76b900"),
    "verify": ("🛡️", "#0a84ff"),
    "reject": ("⛔", "#ff453a"),
    "nudge": ("↩️", "#ff9f0a"),
}
SEV_COLOR = {"low": "#76b900", "medium": "#ffd60a", "high": "#ff9f0a", "critical": "#ff453a"}


# ------------------------------------------------------------------ 사이드바
with st.sidebar:
    st.markdown("### ⚙️ 구성")
    st.caption("NVIDIA NIM · OpenAI 호환 엔드포인트")
    for role, model, note in [
        ("판단·답글", config.MODEL_MAIN, "tool calling"),
        ("안전 필터", config.MODEL_SAFETY, "리뷰 + 답글 검증"),
        ("정책 검색", config.MODEL_EMBED, "2048d embeddings"),
    ]:
        st.markdown(
            f"<div style='font-size:0.82em;line-height:1.45;margin-bottom:6px'>"
            f"<b>{role}</b><br/>"
            f"<code style='font-size:0.95em'>{model.replace('nvidia/', '')}</code><br/>"
            f"<span style='color:#888'>{note}</span></div>",
            unsafe_allow_html=True,
        )
    st.caption(f"API 키 {config.key_fingerprint()} · {config.BASE_URL.replace('https://', '')}")

    st.markdown("### 🧰 에이전트가 쓸 수 있는 툴")
    for t in agent_tools.TOOL_SCHEMAS:
        fn = t["function"]
        st.markdown(f"**`{fn['name']}`** — {fn['description'][:70]}…")

    st.markdown("### 🛡️ 코드 가드레일")
    st.markdown(
        "- **G1** 리뷰 안전검사 없이 답글 제출 금지\n"
        "- **G2** severity high/critical 인데 매니저 알림 없으면 제출 반려\n"
        "- **G3** 제출된 답글 초안을 content-safety 로 자동 재검증"
    )

    st.markdown("### 🔔 최근 매니저 알림")
    alerts = agent_tools.read_alerts(limit=5)
    if not alerts:
        st.caption("아직 없음")
    for a in alerts:
        st.markdown(
            f"<span style='color:{SEV_COLOR.get(a['severity'], '#888')}'>●</span> "
            f"`{a['alert_id']}` **{a['severity']}**<br/>"
            f"<small>{a['summary'][:90]}</small>",
            unsafe_allow_html=True,
        )


# ------------------------------------------------------------------ 헤더
st.markdown("## 🦐 다국어 리뷰 응대 에이전트")
st.caption(
    "다낭 해산물 레스토랑 Hải Đăng Seafood(가상 매장) · 리뷰 1건을 받아 "
    "**메인 모델이 tool calling 으로 "
    "스스로 판단**해서 안전검사 → 정책검색 → 매니저 알림 → 리뷰어 언어 답글까지 처리합니다."
)

if "review_text" not in st.session_state:
    st.session_state.review_text = samples.SAMPLES[0]["text"]
if "result" not in st.session_state:
    st.session_state.result = None

st.markdown("##### 샘플 리뷰")
cols = st.columns(4)
for col, s in zip(cols, samples.SAMPLES):
    with col:
        if st.button(s["label"], use_container_width=True, key=f"btn-{s['id']}"):
            st.session_state.review_text = s["text"]
            st.session_state.result = None
            st.rerun()

review = st.text_area("리뷰 원문", key="review_text", height=150)
run = st.button("▶︎ 에이전트 실행", type="primary")

left, right = st.columns([1.15, 1])

# ------------------------------------------------------------------ 실행
if run:
    if not review.strip():
        st.warning("리뷰를 입력해주세요.")
        st.stop()

    with left:
        st.markdown("#### 🧠 에이전트 실행 타임라인")
        timeline = st.container()
        status = st.status("정책 인덱스 준비 중…", expanded=True)

    try:
        agent_tools.build_index()
        status.update(label="에이전트 루프 실행 중…")

        def on_step(step):
            icon, color = STEP_STYLE.get(step["type"], ("•", "#888"))
            actor = "에이전트 스스로 결정" if step["actor"] == "agent" else "코드 가드레일"
            with timeline:
                st.markdown(
                    f"<div style='border-left:3px solid {color};padding:2px 0 2px 10px;"
                    f"margin:6px 0;'>{icon} <b>step {step['n']}</b> · "
                    f"<code>{step['tool']}</code> "
                    f"<span style='color:#888;font-size:0.85em'>({actor} · {step['elapsed']}s)"
                    f"</span></div>",
                    unsafe_allow_html=True,
                )
                if step.get("reasoning"):
                    st.markdown(
                        f"<div style='color:#8a8a8a;font-size:0.85em;padding-left:14px'>"
                        f"💭 <i>왜 불렀나</i>: {step['reasoning'][:400]}</div>",
                        unsafe_allow_html=True,
                    )
                if step["tool"] == "search_policy" and step.get("result", {}).get("hits"):
                    for h in step["result"]["hits"]:
                        st.markdown(
                            f"<div style='font-size:0.85em;padding-left:14px'>"
                            f"📄 <b>{h['title']}</b> (유사도 {h['score']})</div>",
                            unsafe_allow_html=True,
                        )
                        with st.expander(f"본문 — {h['title']}", expanded=False):
                            st.text(h["text"])
                elif step["tool"] == "check_safety" and step.get("result"):
                    r = step["result"]
                    tag = "review 원문" if r.get("kind") == "review" else "답글 초안(게시 전)"
                    ok = r.get("verdict") == "safe"
                    st.markdown(
                        f"<div style='font-size:0.85em;padding-left:14px'>"
                        f"{'🟢' if ok else '🔴'} {tag} → <b>{r.get('verdict')}</b> "
                        f"<span style='color:#888'>· {r.get('note','')}</span></div>",
                        unsafe_allow_html=True,
                    )
                elif step["tool"] == "notify_manager" and step.get("result"):
                    r = step["result"]
                    st.markdown(
                        f"<div style='font-size:0.85em;padding-left:14px'>"
                        f"🔔 <b>{r.get('alert_id')}</b> ({r.get('severity')}) "
                        f"→ <code>{r.get('path')}</code></div>",
                        unsafe_allow_html=True,
                    )
                elif step["type"] == "reject":
                    for v in step.get("result", {}).get("violations", []):
                        st.markdown(
                            f"<div style='font-size:0.85em;padding-left:14px;color:#ff453a'>"
                            f"⛔ {v}</div>",
                            unsafe_allow_html=True,
                        )

        result = loop.run_agent(review, on_step=on_step)
        st.session_state.result = result
        status.update(label=f"완료 · {result['elapsed']}s", state="complete", expanded=False)
    except Exception as exc:  # noqa: BLE001
        status.update(label="실패", state="error")
        st.error(f"{type(exc).__name__}: {exc}")
        st.stop()

# ------------------------------------------------------------------ 결과
result = st.session_state.result
if result:
    f = result["final"]
    with right:
        st.markdown("#### 📬 최종 결과")
        c1, c2, c3 = st.columns(3)
        c1.metric("언어", f.get("language", "-"))
        c2.metric("분류", f.get("category", "-"))
        sev = f.get("severity", "low")
        c3.markdown(
            f"<div style='font-size:0.8em;color:#888'>심각도</div>"
            f"<div style='font-size:1.6em;color:{SEV_COLOR.get(sev,'#888')};font-weight:600'>"
            f"{sev}</div>",
            unsafe_allow_html=True,
        )

        st.markdown(f"**감성** `{f.get('sentiment','-')}` · "
                    f"**답글 안전성 재검증** `{f.get('reply_safety','-')}`")

        st.markdown("##### 고객에게 게시할 답글")
        st.success(f.get("reply_text", ""))

        st.markdown("##### 판단 근거 (운영자용)")
        st.info(f.get("rationale", "-"))

        used = result["tools_used"]
        st.caption(
            f"호출 툴: {', '.join(f'{k}×{v}' for k, v in used.items())}  ·  "
            f"{result['usage']['calls']} API 콜 / {result['usage']['total_tokens']} 토큰  ·  "
            f"{result['elapsed']}s"
        )
        if result["retries"]:
            st.caption(f"⚠️ 엔드포인트 5xx 자동 재시도 {len(result['retries'])}회")

        with st.expander("원시 트레이스 (JSON)"):
            st.code(json.dumps(result, ensure_ascii=False, indent=2), language="json")
