"""검수/조회 화면이 공유하는 위젯."""

from __future__ import annotations

import html
from datetime import datetime

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

/* 오류를 누르면 고쳐야 할 입력칸으로 내려간다. */
.idv-error-row a.idv-error-link { color: inherit; text-decoration: none; display: block; }
.idv-error-row:has(a.idv-error-link) { cursor: pointer; }
.idv-error-row:has(a.idv-error-link):hover { filter: brightness(.97); }
.idv-error-jump { font-size:.78rem; opacity:.75; white-space: nowrap; }

/* 이동 표적으로만 쓰는 제목(anchor()가 만든다). id 에 '_at_' 가 들어간 것만
   골라 화면에서 접는다. scroll-margin-top 은 스크롤 뒤 상단에 가리지 않게 한다.
   감싸는 컨테이너의 세로 gap 까지 음수 마진으로 상쇄해 폼이 벌어지지 않게 한다. */
[id*="_at_"] {
    height: 0 !important; margin: 0 !important; padding: 0 !important;
    font-size: 0 !important; line-height: 0 !important;
    overflow: hidden !important; scroll-margin-top: 6rem;
}
[id*="_at_"] a, [id*="_at_"] svg { display: none !important; }

/* 표적을 감싸는 컨테이너까지 접는다. Streamlit 버전마다 감싸는 요소가 달라
   자식 선택자(>) 대신 후손으로 넓게 잡는다. */
div[data-testid="stElementContainer"]:has([id*="_at_"]),
div.element-container:has([id*="_at_"]),
div[data-testid="stHeadingWithActionElements"]:has([id*="_at_"]) {
    height: 0 !important; min-height: 0 !important;
    padding: 0 !important; margin: 0 0 -1rem 0 !important;
    overflow: hidden !important; scroll-margin-top: 6rem;
}

/* 이동해 온 칸을 표시한다. 표적은 높이 0이라 눈에 띄지 않으므로, 바로 뒤에 오는
   입력칸(다음 요소 컨테이너)에 테두리를 준다. 어느 칸을 고쳐야 하는지 분명해진다. */
div[data-testid="stElementContainer"]:has([id*="_at_"]:target)
    + div[data-testid="stElementContainer"],
div.element-container:has([id*="_at_"]:target) + div.element-container {
    outline: 2px solid #d93025;
    outline-offset: 4px;
    border-radius: 6px;
    animation: idv-flash 1.2s ease-out 2;
}
@keyframes idv-flash {
    0%, 100% { background: transparent; }
    35%      { background: rgba(217,48,37,.10); }
}
@media (prefers-reduced-motion: reduce) {
    div[data-testid="stElementContainer"]:has(> div > [id*="_at_"]:target)
        + div[data-testid="stElementContainer"] { animation: none; }
}
</style>
"""

# 검증 오류의 field 값 중 편집 폼에 대응하는 입력칸이 있는 것들.
# 'line_items[3]' 처럼 첨자가 붙은 것은 대괄호 앞부분으로 맞춘다.
ANCHORED_FIELDS = {
    "doc_type", "invoice_number", "issue_date", "due_date", "vendor_name",
    "buyer_name", "po_number", "line_items", "currency", "subtotal", "tax",
    "shipping", "total_amount",
}

_BADGE_CLASS = {
    config.STATUS_ERROR: "error",
    config.STATUS_PENDING: "pending",
    config.STATUS_VALIDATED: "ok",
    config.STATUS_FAILED: "failed",
}


def inject_css() -> None:
    st.markdown(BADGE_CSS, unsafe_allow_html=True)


def format_datetime(iso: str | None) -> str:
    """DB에 저장된 UTC ISO 시각을 이 컴퓨터의 로컬 시간대로 바꿔 보여준다.

    예전엔 문자열을 그냥 잘라서(`[:16]`) UTC 시각을 로컬 시각인 것처럼 보여줬다 --
    KST 등 UTC가 아닌 시간대에서는 실제 시각과 몇 시간씩 어긋나 보였다.
    """
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso)
    except ValueError:
        return iso[:16].replace("T", " ")
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d %H:%M")


def status_badge(status: str, extra: str = "") -> str:
    label = config.STATUS_LABELS.get(status, status)
    if extra:
        label = f"{label} · {extra}"
    return (
        f'<span class="idv-badge {_BADGE_CLASS.get(status, "failed")}">'
        f"{html.escape(label)}</span>"
    )


def anchor(key_prefix: str, field: str) -> None:
    """입력칸 바로 앞에 이동 표적을 심는다. 화면에는 보이지 않는다.

    직접 만든 <div id="..."> 는 마크다운 정화 과정에서 id 가 살아남는다는 보장이
    없어 표적 노릇을 못 했다. Streamlit이 제목에 직접 붙여 주는 anchor 를 쓴다 --
    화면의 제목 옆 링크 아이콘이 쓰는 것과 같은 장치라 확실히 동작한다.
    제목 자체는 CSS로 접어 보이지 않게 한다.
    """
    st.subheader("​", anchor=f"{key_prefix}_at_{field}")


def render_errors(errors: list[dict], key_prefix: str | None = None) -> None:
    """검증 에러 목록을 빨간 경고 뱃지와 함께 출력한다.

    key_prefix 를 주면(= 같은 화면에 편집 폼이 있으면) 오류가 그 필드의 입력칸으로
    가는 링크가 된다. 메시지를 읽고 자리를 찾는 수고를 없애기 위한 것이다.
    """
    if not errors:
        st.success("미해결 검증 오류가 없습니다.")
        return
    for err in errors:
        cls = "warning" if err.get("severity") == "warning" else ""
        icon = "⚠️" if err.get("severity") == "warning" else "🚨"
        source = "규칙" if err.get("source") == "rule" else "LLM"
        message = html.escape(str(err.get("message", "")))
        field = str(err.get("field") or "document")
        target = field.split("[", 1)[0]  # 'line_items[3]' -> 'line_items'
        label = html.escape(field)

        body = (
            f"{icon} {message}<br>"
            f'<span class="idv-error-field">{label} · {source}</span>'
        )
        if key_prefix and target in ANCHORED_FIELDS:
            body = (
                f'<a class="idv-error-link" href="#{key_prefix}_at_{target}">'
                f"{icon} {message}<br>"
                f'<span class="idv-error-field">{label} · {source}</span>'
                f'<span class="idv-error-jump"> · 고치러 가기 ↓</span>'
                f"</a>"
            )
        st.markdown(
            f'<div class="idv-error-row {cls}">{body}</div>', unsafe_allow_html=True
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
    from .validator import DOC_TYPE_LABELS

    types = list(DOC_TYPE_LABELS)
    anchor(key_prefix, "doc_type")
    doc_type = st.radio(
        "문서 유형",
        types,
        index=types.index(fields.doc_type) if fields.doc_type in types else 2,
        format_func=lambda t: DOC_TYPE_LABELS[t]["name"],
        horizontal=True,
        key=f"{key_prefix}_type",
        help="자동 분류입니다. 틀렸으면 바꾸세요 — 유형에 따라 항목 이름이 달라집니다.",
    )
    number_label = DOC_TYPE_LABELS[doc_type]["invoice_number"]

    c1, c2, c3 = st.columns(3)
    with c1:
        anchor(key_prefix, "invoice_number")
        invoice_number = st.text_input(
            number_label, fields.invoice_number or "", key=f"{key_prefix}_inv"
        )
        anchor(key_prefix, "vendor_name")
        vendor_name = st.text_input(
            "공급자명", fields.vendor_name or "", key=f"{key_prefix}_vendor"
        )
    with c2:
        anchor(key_prefix, "issue_date")
        issue_date = st.text_input(
            "발행일 (YYYY-MM-DD)", fields.issue_date or "", key=f"{key_prefix}_issue"
        )
        anchor(key_prefix, "buyer_name")
        buyer_name = st.text_input(
            "수신자명", fields.buyer_name or "", key=f"{key_prefix}_buyer"
        )
    with c3:
        anchor(key_prefix, "due_date")
        due_date = st.text_input(
            "지급 기한 (YYYY-MM-DD)", fields.due_date or "", key=f"{key_prefix}_due"
        )
        anchor(key_prefix, "po_number")
        po_number = st.text_input(
            "발주 번호", fields.po_number or "", key=f"{key_prefix}_po"
        )

    anchor(key_prefix, "line_items")
    st.markdown("**품목**")
    df = pd.DataFrame(
        [i.model_dump() for i in fields.line_items],
        columns=["description", "quantity", "unit_price", "tax", "amount"],
    )
    # 빈 목록이면 object 컬럼이 되어 NumberColumn 설정과 충돌한다. dtype을 고정한다.
    df["description"] = df["description"].fillna("").astype("string")
    for column in ("quantity", "unit_price", "tax", "amount"):
        df[column] = pd.to_numeric(df[column], errors="coerce").astype("float64")

    # 표의 기본 인덱스는 0부터라 그대로 두면 검수자가 한 칸 어긋난 행을 고치게 된다.
    # 문서가 품목 번호를 달고 있으면 그 번호를 쓴다. 원문·오류 메시지·DB가 모두
    # 같은 번호를 가리켜야 검수자가 행을 찾을 수 있다.
    numbers = [i.position for i in fields.line_items]
    has_document_numbers = bool(numbers) and all(n is not None for n in numbers)
    df.insert(0, "번호", numbers if has_document_numbers else range(1, len(df) + 1))

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
            "tax": st.column_config.NumberColumn("세액", format="%.2f"),
            "amount": st.column_config.NumberColumn("금액", format="%.2f"),
        },
    )

    a1, a2, a3, a4, a5 = st.columns(5)
    with a1:
        anchor(key_prefix, "currency")
        currency = st.text_input("통화", fields.currency or "", key=f"{key_prefix}_cur")
    with a2:
        anchor(key_prefix, "subtotal")
        subtotal = _num_input("공급가액", fields.subtotal, f"{key_prefix}_sub")
    with a3:
        anchor(key_prefix, "tax")
        tax = _num_input("세액", fields.tax, f"{key_prefix}_tax")
    with a4:
        anchor(key_prefix, "shipping")
        shipping = _num_input("배송비", fields.shipping, f"{key_prefix}_ship")
    with a5:
        anchor(key_prefix, "total_amount")
        total_amount = _num_input("총 청구액", fields.total_amount, f"{key_prefix}_total")

    line_items = []
    for row in edited.to_dict("records"):
        description = (row.get("description") or "").strip()
        values = (row.get("quantity"), row.get("unit_price"), row.get("tax"), row.get("amount"))
        if not description and all(v is None or pd.isna(v) for v in values):
            continue  # 빈 행은 버린다
        # 새로 추가한 행은 번호가 비어 있다. 그때는 문서 번호를 쓰지 않고
        # 전체를 순번으로 돌린다 (db._write_fields).
        number = row.get("번호") if has_document_numbers else None
        line_items.append(
            LineItem(
                position=None if number is None or pd.isna(number) else int(number),
                description=description,
                quantity=_clean_number(row.get("quantity")),
                unit_price=_clean_number(row.get("unit_price")),
                tax=_clean_number(row.get("tax")),
                amount=_clean_number(row.get("amount")),
            )
        )

    return InvoiceFields(
        doc_type=doc_type,
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
