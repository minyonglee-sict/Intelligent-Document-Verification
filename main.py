"""Intelligent Document Verification — Streamlit 대시보드.

실행:  streamlit run main.py
"""

from __future__ import annotations

import streamlit as st

from app import config, db, ui_common, ui_documents, ui_review, ui_upload

st.set_page_config(
    page_title="Intelligent Document Verification",
    page_icon="📄",
    layout="wide",
)

db.init_db()
# 처리 도중 앱이 죽으면 PROCESSING 행이 남아 같은 파일의 재업로드를 영원히 막는다.
db.cleanup_stale_processing()
ui_common.inject_css()


def sidebar() -> None:
    with st.sidebar:
        st.markdown("### ⚙️ 환경")
        st.caption(f"모델: `{config.OLLAMA_MODEL}`")
        st.caption(f"호스트: `{config.OLLAMA_HOST}`")
        st.caption(f"DB: `{config.MSSQL_DATABASE}` @ `{config.MSSQL_SERVER}`")

        if st.button("Ollama 연결 확인", width="stretch"):
            from app import validator

            ok, message = validator.health_check()
            (st.success if ok else st.error)(message)

        if st.button("DB 연결 확인", width="stretch"):
            ok, message = db.health_check()
            (st.success if ok else st.error)(message)

        st.divider()
        st.markdown("### 📊 현황")
        counts = db.status_counts()
        for status in (
            config.STATUS_ERROR,
            config.STATUS_PENDING,
            config.STATUS_VALIDATED,
            config.STATUS_FAILED,
        ):
            st.metric(config.STATUS_LABELS[status], counts.get(status, 0))


def main() -> None:
    st.title("📄 Intelligent Document Verification")
    st.caption("Docling 추출 → Ollama 검증 → MS-SQL 저장 → 사람이 검수")

    sidebar()

    counts = db.status_counts()
    # 검수 탭은 오류 건과 승인 대기 건을 모두 다룬다. 둘 다 사람 손이 필요한 건이다.
    open_count = counts.get(config.STATUS_ERROR, 0) + counts.get(config.STATUS_PENDING, 0)
    tab_upload, tab_review, tab_docs = st.tabs(
        [
            "📥 업로드",
            f"🔍 검수{f' ({open_count})' if open_count else ''}",
            "🗂️ 전체 문서",
        ]
    )

    with tab_upload:
        ui_upload.render()
    with tab_review:
        ui_review.render()
    with tab_docs:
        ui_documents.render()


if __name__ == "__main__":
    main()
