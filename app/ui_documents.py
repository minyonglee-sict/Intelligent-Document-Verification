"""전체 문서 조회 화면."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from . import config, db
from .ui_common import render_errors, status_badge


def render() -> None:
    st.subheader("전체 문서")

    options = [
        "(전체)",
        config.STATUS_ERROR,
        config.STATUS_PENDING,
        config.STATUS_VALIDATED,
        config.STATUS_PROCESSING,
        config.STATUS_FAILED,
    ]
    choice = st.selectbox("상태 필터", options, format_func=_label)
    rows = db.list_documents(None if choice == "(전체)" else choice)

    if not rows:
        st.info("저장된 문서가 없습니다. **업로드** 탭에서 PDF를 올려보세요.")
        return

    counts = db.error_counts()
    table = pd.DataFrame(
        [
            {
                "ID": r["id"],
                "파일명": r["filename"],
                "상태": config.STATUS_LABELS.get(r["status"], r["status"]),
                "오류": counts.get(r["id"], 0),
                "페이지": r["page_count"],
                "모델": r["model"],
                "등록": r["created_at"],
                "승인": r["validated_at"] or "",
            }
            for r in rows
        ]
    )
    st.caption("행을 클릭하면 아래에 해당 문서의 상세가 열립니다.")
    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        key="documents_table",
        on_select="rerun",
        selection_mode="single-row",
    )

    # 선택 인덱스는 표의 행 위치다. 상태 필터를 바꾸면 행 수가 줄어 이전 선택이
    # 범위를 벗어날 수 있으므로 확인하고, 선택이 없으면 첫 행을 보여준다.
    selected = event.selection["rows"]
    position = selected[0] if selected and selected[0] < len(rows) else 0
    _render_detail(int(rows[position]["id"]))


def _label(value: str) -> str:
    return "(전체)" if value == "(전체)" else config.STATUS_LABELS.get(value, value)


def _render_detail(doc_id: int) -> None:
    doc = db.get_document(doc_id)
    if not doc:
        return

    st.divider()
    st.markdown(f"#### #{doc_id} · {doc['filename']}")
    st.markdown(status_badge(doc["status"]), unsafe_allow_html=True)
    if doc.get("failure_reason"):
        st.error(doc["failure_reason"])

    tabs = st.tabs(["추출 필드", "검증 오류", "Markdown", "Docling JSON", "DB 레코드"])

    with tabs[0]:
        fields = db.load_fields(doc)
        st.json(fields.model_dump(), expanded=True)

    with tabs[1]:
        render_errors(db.get_errors(doc_id, only_open=False))

    with tabs[2]:
        st.text(doc.get("markdown") or "(없음)")

    with tabs[3]:
        raw = doc.get("docling_json")
        st.json(json.loads(raw) if raw else {}, expanded=False)

    with tabs[4]:
        _render_db_record(doc, doc_id)

    if st.button("이 문서 삭제", key=f"del{doc_id}"):
        db.delete_document(doc_id)
        st.rerun()


_HEADER_LABELS = {
    "doc_type": "문서 유형",
    "invoice_number": "문서 번호",
    "issue_date": "발행일",
    "due_date": "지급 기한",
    "vendor_name": "공급자명",
    "buyer_name": "수신자명",
    "po_number": "발주 번호",
    "currency": "통화",
    "subtotal": "공급가액",
    "tax": "세액",
    "shipping": "배송비",
    "total_amount": "총 청구액",
}


def _render_db_record(doc: dict, doc_id: int) -> None:
    """승인된 송장 값이 DB에 어떻게 들어가 있는지 그대로 보여준다."""
    st.caption(
        "검수 화면에서 확정한 값이 `documents` 컬럼과 `line_items` 테이블에 "
        "저장된 모습입니다."
    )

    st.markdown("**`documents` — 머리말 컬럼**")
    st.dataframe(
        pd.DataFrame(
            [
                {
                    "컬럼": column,
                    "항목": label,
                    "값": "NULL" if doc.get(column) is None else str(doc.get(column)),
                }
                for column, label in _HEADER_LABELS.items()
            ]
        ),
        hide_index=True,
        width="stretch",
    )

    items = db.get_line_items(doc_id)
    st.markdown(f"**`line_items` — 품목 {len(items)}행**")
    if items:
        st.dataframe(
            pd.DataFrame(items).rename(
                columns={
                    "id": "id",
                    "position": "번호",
                    "description": "품목",
                    "quantity": "수량",
                    "unit_price": "단가",
                    "tax": "세액",
                    "amount": "금액",
                }
            ),
            hide_index=True,
            width="stretch",
        )
    else:
        st.caption("저장된 품목이 없습니다.")
