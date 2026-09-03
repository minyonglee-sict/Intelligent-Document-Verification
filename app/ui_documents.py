"""전체 문서 조회 화면."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from . import config, db, pipeline, ui_report
from .ui_common import format_datetime, render_errors, status_badge

# 표의 선택 상태는 행 '위치'로 남는다. 문서를 지운 뒤 그대로 두면 같은 위치의
# 다른 문서가 선택된 것처럼 보이므로, key 를 바꿔 위젯을 새로 만든다.
_TABLE_VERSION = "documents_table_version"


def _reset_table_selection() -> None:
    st.session_state[_TABLE_VERSION] = st.session_state.get(_TABLE_VERSION, 0) + 1


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
    # 순번은 pandas 인덱스가 아니라 실제 열로 넣는다. 인덱스는 0부터 시작하는 데다
    # 열 목록에서 '(index)' 로 보여 몇 건인지 세는 데 쓰기 어렵다.
    table = pd.DataFrame(
        [
            {
                "순번": position,
                "ID": r["id"],
                "파일명": r["filename"],
                "상태": config.STATUS_LABELS.get(r["status"], r["status"]),
                "오류": counts.get(r["id"], 0),
                "페이지": r["page_count"],
                "모델": r["model"],
                "등록": format_datetime(r["created_at"]),
                "승인": format_datetime(r["validated_at"]),
            }
            for position, r in enumerate(rows, start=1)
        ]
    )
    st.caption(
        f"총 **{len(rows)}건**. 행을 클릭하면 아래에 해당 문서의 상세가 열립니다. "
        "왼쪽 체크박스로 여러 건을 골라 한 번에 삭제할 수 있습니다."
    )
    event = st.dataframe(
        table,
        width="stretch",
        hide_index=True,
        key=f"documents_table_{st.session_state.get(_TABLE_VERSION, 0)}",
        on_select="rerun",
        selection_mode="multi-row",
        column_config={
            "순번": st.column_config.NumberColumn("순번", format="%d", width="small"),
        },
    )

    # 선택 인덱스는 표의 행 위치다. 상태 필터를 바꾸면 행 수가 줄어 이전 선택이
    # 범위를 벗어날 수 있으므로 걸러낸다.
    selected = [i for i in event.selection["rows"] if i < len(rows)]

    _render_delete(rows, selected)

    # 선택이 없으면 첫 행, 여러 건을 골랐으면 첫 번째 것의 상세를 보여준다.
    position = selected[0] if selected else 0
    _render_detail(int(rows[position]["id"]))


def _render_delete(rows: list[dict], selected: list[int]) -> None:
    """체크한 문서를 삭제한다. 되돌릴 수 없으므로 한 번 더 확인받는다."""
    if not selected:
        st.caption("삭제하려면 왼쪽 체크박스로 문서를 고르세요.")
        return

    targets = [rows[i] for i in selected]
    column, _ = st.columns([1, 3])
    with column.popover(f"🗑️ 선택한 {len(targets)}건 삭제"):
        st.markdown("다음 문서를 삭제합니다. **되돌릴 수 없습니다.**")
        for row in targets:
            st.markdown(f"- `#{row['id']}` {row['filename']}")
        st.caption(
            "문서·품목·검증 오류 기록과 `data/uploads` 의 원본 파일이 함께 지워집니다. "
            "같은 파일을 다시 올리면 새로 처리됩니다."
        )
        if st.button("삭제", type="primary", key="confirm_bulk_delete"):
            deleted = pipeline.delete_documents([int(r["id"]) for r in targets])
            _reset_table_selection()
            st.toast(f"{deleted}건을 삭제했습니다.", icon="🗑️")
            st.rerun()


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

    # 잘못 뽑힌 값은 승인한 뒤에야 눈에 띄기도 한다. 검수 화면에만 신고를 두면
    # 이미 승인한 문서는 신고할 길이 없어, 결국 화면을 캡처해 터미널로 옮기게 된다.
    reporter = st.expander(
        "이 문서 오류 신고",
        icon=":material/bug_report:",
        on_change="rerun",
        key=f"docs_report_exp_{doc_id}",
    )
    if reporter.open:
        with reporter:
            ui_report.report_form(
                section="전체 문서", key_prefix=f"docs_report_{doc_id}", doc_id=doc_id
            )

    # 되돌릴 수 없는 동작이라 한 번 확인받는다. 확인 없이 한 번에 지우고 있었는데,
    # 상세를 훑다가 잘못 누르면 문서와 원본 파일이 그대로 사라졌다.
    # 체크박스 다중 삭제(_render_delete)와 같은 방식으로 맞춘다.
    with st.popover("🗑️ 이 문서 삭제"):
        st.markdown(
            f"`#{doc_id}` **{doc['filename']}** 을(를) 삭제합니다. **되돌릴 수 없습니다.**"
        )
        st.caption(
            "문서·품목·검증 오류 기록과 `data/uploads` 의 원본 파일이 함께 지워집니다. "
            "같은 파일을 다시 올리면 새로 처리됩니다."
        )
        if st.button("삭제", type="primary", key=f"confirm_del{doc_id}"):
            deleted = pipeline.delete_documents([doc_id])
            _reset_table_selection()
            st.toast(f"{deleted}건을 삭제했습니다.", icon="🗑️")
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
