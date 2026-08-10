"""업로드 화면: 다건 PDF 드래그앤드롭 -> 파이프라인 실행."""

from __future__ import annotations

import streamlit as st

from . import config, pipeline
from .ui_common import status_badge


def render() -> None:
    st.subheader("문서 업로드")
    st.caption(
        "송장·영수증 파일을 아래 영역에 드래그앤드롭하세요. "
        "Docling으로 텍스트를 추출하고 Ollama로 검증한 뒤 MS-SQL에 저장합니다. "
        "PDF 외에 Word·PowerPoint·Excel·이미지도 읽습니다."
    )

    # file_uploader는 session_state로 비울 수 없다. key를 바꿔 새 위젯으로 갈아끼우는
    # 것이 유일하게 확실한 초기화 방법이라 회차 번호를 key에 붙인다.
    round_no = st.session_state.setdefault("upload_round", 0)

    uploaded = st.file_uploader(
        "문서 파일 (다중 선택 가능)",
        type=config.UPLOAD_EXTENSIONS,
        accept_multiple_files=True,
        key=f"uploader_{round_no}",
    )

    last_run = st.session_state.get("last_run")

    col1, col2, col3 = st.columns([1, 3, 1])
    with col1:
        skip_duplicates = st.checkbox("중복 파일 건너뛰기", value=True)
    with col2:
        run = st.button(
            f"검증 파이프라인 실행 ({len(uploaded) if uploaded else 0}건)",
            type="primary",
            disabled=not uploaded,
            width="stretch",
        )
    with col3:
        clear = st.button(
            "초기화",
            disabled=not uploaded and not last_run,
            width="stretch",
            help="업로드 목록과 아래 처리 결과를 비웁니다. 저장된 문서는 지우지 않습니다.",
        )

    if clear:
        st.session_state["upload_round"] = round_no + 1
        st.session_state.pop("last_run", None)
        st.rerun()

    if run and uploaded:
        # process_pdf 안에는 st.* 호출이 없어 Streamlit이 실행 중인 스크립트를
        # 중단시키지 못한다. 처리 도중 rerun이 걸리면 새 스크립트가 나란히 돌아
        # 같은 파일이 두 번 처리된다. 세션 단위로 재진입을 막는다.
        if st.session_state.get("pipeline_busy"):
            st.warning("이미 처리 중입니다. 완료될 때까지 기다려 주세요.")
        else:
            st.session_state["pipeline_busy"] = True
            try:
                _run_pipeline(uploaded, skip_duplicates)
            finally:
                st.session_state["pipeline_busy"] = False

    if st.session_state.get("last_run"):
        _render_summary(st.session_state["last_run"])


def _run_pipeline(files, skip_duplicates: bool) -> None:
    total = len(files)
    progress = st.progress(0.0, text="시작 중...")
    outcomes = []

    for idx, file in enumerate(files, start=1):
        progress.progress(
            (idx - 1) / total, text=f"[{idx}/{total}] {file.name} 처리 중..."
        )
        outcome = pipeline.process_pdf(
            file.name, file.getvalue(), skip_duplicates=skip_duplicates
        )
        outcomes.append(outcome)

    progress.progress(1.0, text=f"완료: {total}건 처리")
    st.session_state["last_run"] = outcomes


def _render_summary(outcomes) -> None:
    st.divider()
    st.markdown("#### 처리 결과")

    counts = {"error": 0, "pending": 0, "failed": 0, "skipped": 0}
    for o in outcomes:
        if o.skipped:
            counts["skipped"] += 1
        elif o.status == config.STATUS_ERROR:
            counts["error"] += 1
        elif o.status == config.STATUS_FAILED:
            counts["failed"] += 1
        else:
            counts["pending"] += 1

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("검증 통과", counts["pending"])
    m2.metric("검증 오류", counts["error"])
    m3.metric("처리 실패", counts["failed"])
    m4.metric("중복 건너뜀", counts["skipped"])

    for o in outcomes:
        extra = f"오류 {o.error_count}건" if o.error_count else ""
        cols = st.columns([4, 2, 4])
        cols[0].markdown(f"**{o.filename}**")
        cols[1].markdown(
            status_badge("SKIPPED" if o.skipped else o.status, extra),
            unsafe_allow_html=True,
        )
        cols[2].caption(o.message or (f"문서 #{o.document_id}" if o.document_id else ""))

    if counts["error"]:
        st.warning(f"{counts['error']}건이 검증에 실패했습니다. **검수** 탭에서 확인하세요.")
