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
    "forced_hold": ("🚧", "#ff453a"),
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
        "- **G1** 리뷰 안전검사 없이 종료 금지\n"
        "- **G2** high/critical 인데 매니저 알림 없으면 반려\n"
        "- **G3** 답글 초안 content-safety 자동 재검증\n"
        "- **G4** 주문번호·테이블·주문시각·조회사실 노출 금지\n"
        "- **G5** 중량 분쟁: 고객 탓 금지 + 계량 절차 안내 필수\n"
        "- **G6** 위생: 인정·반박·보상 금지, 개인 연락만\n"
        "- **G7** critical/high → 자동 게시 금지(승인 대기)"
    )

    st.markdown("### ⏸️ 승인 대기 큐")
    pend = agent_tools.read_pending(limit=5)
    if not pend:
        st.caption("대기 중인 건 없음")
    for q in pend:
        st.markdown(
            f"<span style='color:{SEV_COLOR.get(q['severity'], '#888')}'>●</span> "
            f"`{q['approval_id']}` **{q['category']}**<br/>"
            f"<small>{q['status']} · {q['draft_reply'][:60]}…</small>",
            unsafe_allow_html=True,
        )

    st.markdown("### 🔔 최근 매니저 알림")
    alerts = agent_tools.read_alerts(limit=5)
    if not alerts:
        st.caption("아직 없음")
    for a in alerts:
        comp = (a.get("compensation") or {})
        extra = (f"<br/><small style='color:#76b900'>💰 {comp['display'][:70]}</small>"
                 if comp.get("applicable") else "")
        st.markdown(
            f"<span style='color:{SEV_COLOR.get(a['severity'], '#888')}'>●</span> "
            f"`{a['alert_id']}` **{a['severity']}**<br/>"
            f"<small>{a['summary'][:90]}</small>{extra}",
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
st.caption("1단계 — 다국어 응대 · 안전 필터 · 정책 검색")
for col, smp in zip(st.columns(4), samples.by_stage(1)):
    with col:
        if st.button(smp["label"], use_container_width=True, key=f"btn-{smp['id']}"):
            st.session_state.review_text = smp["text"]
            st.session_state.result = None
            st.rerun()
st.caption("2단계 — 주문 데이터 대조 · 보상안 산정 · 승인 대기")
for col, smp in zip(st.columns(5), samples.by_stage(2)):
    with col:
        if st.button(smp["label"], use_container_width=True, key=f"btn-{smp['id']}"):
            st.session_state.review_text = smp["text"]
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
                elif step["tool"] == "find_order_candidates" and step.get("result"):
                    r = step["result"]
                    if r.get("verdict") == "no_match":
                        st.markdown(
                            "<div style='font-size:0.85em;padding-left:14px;color:#ff9f0a'>"
                            "🔍 <b>일치하는 주문 없음</b> — 방문 사실 확인 불가. "
                            "고객을 반박하지 않고 개별 연락으로 유도</div>",
                            unsafe_allow_html=True,
                        )
                    for c in r.get("candidates", []):
                        badge = {"high": "#76b900", "medium": "#ffd60a", "low": "#888"}.get(
                            c["match_confidence"], "#888")
                        st.markdown(
                            f"<div style='font-size:0.85em;padding-left:14px'>"
                            f"🧾 <code>{c['order_id']}</code> "
                            f"<span style='color:{badge}'>●{c['match_confidence']}</span> "
                            f"<span style='color:#888'>{' / '.join(c['reasons'])}</span></div>",
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
                    comp = (r.get("compensation") or {})
                    if comp.get("applicable"):
                        st.markdown(
                            f"<div style='font-size:0.85em;padding-left:14px;color:#76b900'>"
                            f"💰 보상 제안 <b>{comp['display']}</b><br/>"
                            f"<span style='color:#888'>근거 {comp['policy_basis']} · "
                            f"매니저 알림에만 기록, 공개 답글 금지</span></div>",
                            unsafe_allow_html=True,
                        )
                elif step["type"] == "forced_hold":
                    st.markdown(
                        "<div style='font-size:0.85em;padding-left:14px;color:#ff453a'>"
                        "🚧 가드레일 반려 한도 초과 → 자동 게시 차단, 사람 검토로 전환</div>",
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

        held = f.get("status") == "승인 대기"
        if held:
            st.markdown(
                f"<div style='background:#3a2a00;border:1px solid #ff9f0a;border-radius:8px;"
                f"padding:10px 14px;margin:8px 0'>"
                f"<span style='font-size:1.1em'>⏸️ <b>승인 대기</b></span> "
                f"<span style='color:#ffd60a'>— 자동 게시되지 않았습니다. "
                f"매니저 승인 후 게시됩니다.</span><br/>"
                f"<small style='color:#aaa'>승인 ID {f.get('approval_id','-')}"
                + (f" · 연계 알림 {f.get('alert_id')}" if f.get("alert_id") else "")
                + "</small></div>",
                unsafe_allow_html=True,
            )
            st.markdown("##### 매니저 승인 대기 중인 답글 초안")
            st.warning(f.get("reply_text", ""))
            if f.get("manager_note"):
                st.caption(f"매니저 확인 사항: {f['manager_note']}")
        else:
            st.markdown(
                "<div style='background:#1e3a00;border:1px solid #76b900;border-radius:8px;"
                "padding:8px 14px;margin:8px 0'>✅ <b>자동 게시</b> "
                "<span style='color:#9c6'>— 심각도 낮음, 승인 없이 게시 가능</span></div>",
                unsafe_allow_html=True,
            )
            st.markdown("##### 고객에게 게시할 답글")
            st.success(f.get("reply_text", ""))

        comp = f.get("compensation")
        if comp and comp.get("applicable"):
            st.markdown("##### 💰 보상 제안 (매니저 전용 · 답글에는 미노출)")
            st.info(f"{comp['display']}\n\n근거: {comp['policy_basis']} · {comp['disclaimer']}")

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
