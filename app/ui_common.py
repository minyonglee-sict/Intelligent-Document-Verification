"""검수/조회 화면이 공유하는 위젯."""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from . import config
from .schemas import InvoiceFields, LineItem

BADGE_CSS = """
<style>
.idv-badge {
    display: inline-block;
    padding: 2px 10px;
    border-radius: 999px;
    font-size: 0.78rem;
    font-weight: 700;
    letter-spacing: .02em;
    border: 1px solid transparent;
}
.idv-badge.error    { background:#fdecea; color:#b3261e; border-color:#f2b8b5; }
.idv-badge.warning  { background:#fff4e5; color:#8a5300; border-color:#ffd8a8; }
.idv-badge.pending  { background:#fff8e1; color:#8a6d00; border-color:#ffe082; }
.idv-badge.ok       { background:#e6f4ea; color:#137333; border-color:#a8dab5; }
.idv-badge.failed   { background:#eceff1; color:#37474f; border-color:#cfd8dc; }
.idv-error-row {
    border-left: 4px solid #d93025;
    background: rgba(217,48,37,.06);
    padding: .55rem .8rem;
    border-radius: 0 6px 6px 0;
    margin-bottom: .4rem;
}
.idv-error-row.warning { border-left-color:#f29900; background: rgba(242,153,0,.07); }
.idv-error-field { font-family: ui-monospace, monospace; font-size:.78rem; opacity:.75; }
</style>
"""

_BADGE_CLASS = {
    config.STATUS_ERROR: "error",
    config.STATUS_PENDING: "pending",
    config.STATUS_VALIDATED: "ok",
    config.STATUS_FAILED: "failed",
}


def inject_css() -> None:
    st.markdown(BADGE_CSS, unsafe_allow_html=True)


def status_badge(status: str, extra: str = "") -> str:
    label = config.STATUS_LABELS.get(status, status)
    if extra:
        label = f"{label} · {extra}"
    return (
        f'<span class="idv-badge {_BADGE_CLASS.get(status, "failed")}">'
        f"{html.escape(label)}</span>"
    )


def render_errors(errors: list[dict]) -> None:
    """검증 에러 목록을 빨간 경고 뱃지와 함께 출력한다."""
    if not errors:
        st.success("미해결 검증 오류가 없습니다.")
        return
    for err in errors:
        cls = "warning" if err.get("severity") == "warning" else ""
        icon = "⚠️" if err.get("severity") == "warning" else "🚨"
        source = "규칙" if err.get("source") == "rule" else "LLM"
        message = html.escape(str(err.get("message", "")))
        field = html.escape(str(err.get("field") or "document"))
        st.markdown(
            f'<div class="idv-error-row {cls}">'
            f"{icon} {message}<br>"
            f'<span class="idv-error-field">{field} · {source}</span>'
            f"</div>",
            unsafe_allow_html=True,
        )


def _num_input(label: str, value, key: str):
    return st.number_input(
        label,
        value=float(value) if value is not None else None,
        step=0.01,
        format="%.2f",
        key=key,
        placeholder="비어 있음",
    )


def field_editor(fields: InvoiceFields, key_prefix: str) -> InvoiceFields:
    """추출 필드 편집 폼. 사용자가 수정한 값을 담은 새 InvoiceFields를 돌려준다."""
    c1, c2, c3 = st.columns(3)
    with c1:
        invoice_number = st.text_input(
            "송장 번호", fields.invoice_number or "", key=f"{key_prefix}_inv"
        )
        vendor_name = st.text_input(
            "공급자명", fields.vendor_name or "", key=f"{key_prefix}_vendor"
        )
    with c2:
        issue_date = st.text_input(
            "발행일 (YYYY-MM-DD)", fields.issue_date or "", key=f"{key_prefix}_issue"
        )
        buyer_name = st.text_input(
            "수신자명", fields.buyer_name or "", key=f"{key_prefix}_buyer"
        )
    with c3:
        due_date = st.text_input(
            "지급 기한 (YYYY-MM-DD)", fields.due_date or "", key=f"{key_prefix}_due"
        )
        po_number = st.text_input(
            "발주 번호", fields.po_number or "", key=f"{key_prefix}_po"
        )

    st.markdown("**품목**")
    df = pd.DataFrame(
        [i.model_dump() for i in fields.line_items],
        columns=["description", "quantity", "unit_price", "amount"],
    )
    # 빈 목록이면 object 컬럼이 되어 NumberColumn 설정과 충돌한다. dtype을 고정한다.
    df["description"] = df["description"].fillna("").astype("string")
    for column in ("quantity", "unit_price", "amount"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")

    # 오류 메시지는 'line_items[20]'처럼 1부터 센다. 표의 기본 인덱스는 0부터라
    # 그대로 두면 검수자가 한 칸 어긋난 행을 고치게 된다. 번호를 맞춰 보여준다.
    df.insert(0, "번호", range(1, len(df) + 1))

    edited = st.data_editor(
        df,
        num_rows="dynamic",
        width="stretch",
        hide_index=True,
        disabled=["번호"],
        key=f"{key_prefix}_items",
        column_config={
            "번호": st.column_config.NumberColumn("번호", format="%d", width="small"),
            "description": st.column_config.TextColumn("품목", width="large"),
            "quantity": st.column_config.NumberColumn("수량", format="%.2f"),
            "unit_price": st.column_config.NumberColumn("단가", format="%.2f"),
            "amount": st.column_config.NumberColumn("금액", format="%.2f"),
        },
    )

    a1, a2, a3, a4, a5 = st.columns(5)
    with a1:
        currency = st.text_input("통화", fields.currency or "", key=f"{key_prefix}_cur")
    with a2:
        subtotal = _num_input("공급가액", fields.subtotal, f"{key_prefix}_sub")
    with a3:
        tax = _num_input("세액", fields.tax, f"{key_prefix}_tax")
    with a4:
        shipping = _num_input("배송비", fields.shipping, f"{key_prefix}_ship")
    with a5:
        total_amount = _num_input("총 청구액", fields.total_amount, f"{key_prefix}_total")

    line_items = []
    for row in edited.to_dict("records"):
        description = (row.get("description") or "").strip()
        values = (row.get("quantity"), row.get("unit_price"), row.get("amount"))
        if not description and all(v is None or pd.isna(v) for v in values):
            continue  # 빈 행은 버린다
        line_items.append(
            LineItem(
                description=description,
                quantity=_clean_number(row.get("quantity")),
                unit_price=_clean_number(row.get("unit_price")),
                amount=_clean_number(row.get("amount")),
            )
        )

    return InvoiceFields(
        invoice_number=invoice_number or None,
        issue_date=issue_date or None,
        due_date=due_date or None,
        vendor_name=vendor_name or None,
        buyer_name=buyer_name or None,
        po_number=po_number or None,
        currency=currency or None,
        line_items=line_items,
        subtotal=subtotal,
        tax=tax,
        shipping=shipping,
        total_amount=total_amount,
    )


def _clean_number(value):
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
