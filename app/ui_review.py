"""검수 화면.

두 갈래를 한 화면에서 마감한다.
  - status='ERROR'   : 검증 오류. 값을 고쳐야 승인할 수 있다.
  - status='PENDING' : 검증 통과. 고칠 것이 없으니 확인 후 승인만 하면 된다.
둘 다 최종 상태는 'VALIDATED'이고, 전환은 사람의 승인으로만 일어난다.
"""

from __future__ import annotations

import streamlit as st

from . import config, db, pipeline, ui_report
from .ui_common import field_editor, render_errors, status_badge


def render() -> None:
    _render_error_section()
    st.divider()
    _render_pending_section()


# --------------------------------------------------------------------------
# 1) 검증 오류 - 수정이 필요한 건
# --------------------------------------------------------------------------

def _render_error_section() -> None:
    st.subheader("검수 대기 (검증 오류)")

    rows = db.list_documents(status=config.STATUS_ERROR)
    if not rows:
        st.success("검증 오류 상태의 문서가 없습니다. 🎉")
        return

    counts = db.error_counts()
    st.caption(f"{len(rows)}건의 문서에 검증 오류가 있습니다. 값을 확인·수정한 뒤 승인하세요.")

    for row in rows:
        doc_id = row["id"]
        n_errors = counts.get(doc_id, 0)
        state = f"오류 {n_errors}건" if n_errors else "수정 완료 · 승인 대기"
        with st.expander(f"#{doc_id} · {row['filename']} — {state}", expanded=len(rows) == 1):
            _render_document(doc_id, show_recheck=True)


# --------------------------------------------------------------------------
# 2) 검증 통과 - 승인만 남은 건
# --------------------------------------------------------------------------

def _render_pending_section() -> None:
    st.subheader("승인 대기 (검증 통과)")

    rows = db.list_documents(status=config.STATUS_PENDING)
    if not rows:
        st.info("승인을 기다리는 문서가 없습니다.")
        return

    st.caption(
        f"{len(rows)}건이 검증을 통과했습니다. 고칠 것이 없다면 그대로 승인하면 됩니다."
    )

    if st.button(
        f"전체 승인 → VALIDATED ({len(rows)}건)",
        type="primary",
        key="bulk_approve",
    ):
        approved = db.bulk_approve([r["id"] for r in rows])
        st.toast(f"{approved}건을 승인했습니다.", icon="✅")
        st.rerun()

    for row in rows:
        doc_id = row["id"]
        with st.expander(f"#{doc_id} · {row['filename']} — 검증 통과", expanded=False):
            _render_document(doc_id, show_recheck=False)


# --------------------------------------------------------------------------
# 공통 상세 화면
# --------------------------------------------------------------------------

def _render_document(doc_id: int, *, show_recheck: bool) -> None:
    doc = db.get_document(doc_id)
    if not doc:
        st.info("문서를 찾을 수 없습니다.")
        return

    flash = st.session_state.pop(f"doc{doc_id}_flash", None)
    if flash:
        level, text = flash
        getattr(st, level)(text)

    errors = db.get_errors(doc_id)

    st.markdown(
        status_badge(doc["status"], f"오류 {len(errors)}건" if errors else ""),
        unsafe_allow_html=True,
    )
    st.markdown("##### 검증 오류")
    # 아래 편집 폼과 같은 key_prefix 를 넘겨, 오류를 누르면 그 입력칸으로 내려간다.
    render_errors(errors, key_prefix=f"doc{doc_id}")

    st.divider()
    st.markdown("##### 추출 결과 수정")
    edited = field_editor(db.load_fields(doc), key_prefix=f"doc{doc_id}")

    note = st.text_area(
        "검수 메모", value=doc.get("reviewer_note") or "", key=f"doc{doc_id}_note"
    )

    # 승인 직전에 규칙을 한 번 더 돌려 오류가 남아 있으면 되묻는다. 이 플래그가
    # 서 있으면 사용자가 그것을 보고도 승인하겠다고 다시 누른 것이다.
    force_key = f"doc{doc_id}_force"
    forcing = st.session_state.get(force_key, False)

    if show_recheck:
        c1, c2, _ = st.columns([1, 1, 2])
        recheck = c1.button("재검증", key=f"doc{doc_id}_recheck", width="stretch")
        approve_col = c2
    else:
        recheck = False
        approve_col, _ = st.columns([1, 3])

    if forcing:
        label = "오류가 남아 있지만 승인"
    elif show_recheck:
        label = "수정 후 승인 → VALIDATED"
    else:
        label = "승인 → VALIDATED"

    approve = approve_col.button(
        label,
        key=f"doc{doc_id}_approve",
        type="primary",
        width="stretch",
    )

    if recheck:
        remaining = pipeline.recheck(doc_id, edited, note)
        # data_editor는 원본 DataFrame 대비 편집 델타를 위젯 상태에 들고 있다.
        # 수정본을 DB에 저장한 뒤에도 그 델타가 남아 있으면 다음 렌더에서 한 번 더
        # 얹혀 추가한 행이 중복된다. 저장했으니 위젯 상태는 버린다.
        st.session_state.pop(f"doc{doc_id}_items", None)
        st.session_state[f"doc{doc_id}_flash"] = (
            ("error", f"아직 {len(remaining)}건의 오류가 남아 있습니다.")
            if remaining
            else ("success", "규칙 검증을 모두 통과했습니다. 승인할 수 있습니다.")
        )
        st.rerun()

    if approve:
        # 표 편집기는 셀에 입력한 뒤 Enter나 표 바깥 클릭으로 확정해야 반영된다.
        # 확정 전에 승인을 누르면 고치기 전 값이 그대로 승인될 수 있으므로,
        # 저장할 값으로 규칙을 다시 돌려 남은 오류를 보여주고 한 번 되묻는다.
        remaining = pipeline.recheck(doc_id, edited, note)
        critical = [e for e in remaining if e.severity == "critical"]

        if critical and not forcing:
            st.session_state[force_key] = True
            st.session_state.pop(f"doc{doc_id}_items", None)
            st.session_state[f"doc{doc_id}_flash"] = (
                "warning",
                f"승인하려는 값에 아직 {len(critical)}건의 오류가 있습니다. "
                f"표의 셀을 고쳤다면 Enter를 누르거나 표 바깥을 클릭해 확정한 뒤 "
                f"다시 시도하세요. 그대로 승인하려면 버튼을 한 번 더 누르세요.",
            )
            st.rerun()

        db.approve(doc_id, edited, note)
        st.session_state.pop(force_key, None)
        st.toast(f"문서 #{doc_id} 를 VALIDATED 로 승인했습니다.", icon="✅")
        st.rerun()

    with st.expander("Docling 추출 원문 (Markdown)"):
        st.text(doc.get("markdown") or "(없음)")

    # 오류를 만난 그 자리에서 신고한다. 펼쳤을 때만 폼을 만든다 -- 문서마다 하나씩
    # 붙는 자리라, 열지도 않은 붙여넣기 컴포넌트를 문서 수만큼 띄우게 된다.
    reporter = st.expander(
        "이 화면 오류 신고",
        icon=":material/bug_report:",
        on_change="rerun",
        key=f"report_exp_{doc_id}",
    )
    if reporter.open:
        with reporter:
            ui_report.report_form(
                section="검수", key_prefix=f"report_doc{doc_id}", doc_id=doc_id
            )
