"""오류 신고 화면.

터미널에 캡처를 붙여넣고 고쳐 달라고 하던 일을 앱 안에서 끝내기 위한 화면이다.
신고는 파일로 남고(app.report), 소스 수정은 여전히 사람이 터미널에서 한다.

클립보드 붙여넣기는 Streamlit 기본 위젯에 없다. st.file_uploader 는 파일로
저장된 것만 받으므로, Win+Shift+S 로 뜬 캡처를 쓰려면 매번 파일로 저장하는
단계가 끼어든다. 그 단계를 없애려고 붙여넣기 영역만 커스텀 컴포넌트(CCv2)로
만들었다. 파일 업로더도 함께 두어 저장된 캡처는 그대로 올릴 수 있다.
"""

from __future__ import annotations

import contextlib
from typing import Any, Iterator, Optional

import streamlit as st

from . import config, report
from .ui_common import format_datetime

# --------------------------------------------------------------------------
# 붙여넣기 영역 (CCv2)
# --------------------------------------------------------------------------

_PASTE_HTML = """
<div class="idv-paste" id="zone" tabindex="0" role="button">
  <div class="idv-hint" id="hint"></div>
  <div class="idv-thumbs" id="thumbs"></div>
</div>
"""

_PASTE_CSS = """
.idv-paste {
  border: 1.5px dashed var(--st-color-border, rgba(128, 128, 128, 0.45));
  border-radius: 8px;
  padding: 14px 12px;
  text-align: center;
  cursor: pointer;
  color: var(--st-text-color, inherit);
  background: var(--st-secondary-background-color, transparent);
  font-family: var(--st-font, sans-serif);
  font-size: 0.86rem;
  transition: border-color 0.15s ease, background 0.15s ease;
}
.idv-paste:hover { border-color: var(--st-primary-color, #ff4b4b); }
.idv-paste:focus-visible,
.idv-paste.is-over {
  outline: none;
  border-color: var(--st-primary-color, #ff4b4b);
  background: color-mix(in srgb, var(--st-primary-color, #ff4b4b) 8%, transparent);
}
.idv-hint { opacity: 0.85; line-height: 1.5; }
.idv-thumbs {
  display: flex; flex-wrap: wrap; gap: 10px;
  justify-content: center; margin-top: 12px;
}
.idv-thumb { position: relative; }
.idv-thumb img {
  display: block; height: 92px; border-radius: 6px;
  border: 1px solid rgba(128, 128, 128, 0.35);
}
.idv-thumb button {
  position: absolute; top: -7px; right: -7px;
  width: 21px; height: 21px; padding: 0;
  border: none; border-radius: 50%; cursor: pointer;
  background: #d93025; color: #fff;
  font-size: 13px; line-height: 21px;
}
"""

# 붙여넣기는 포커스된 요소로 간다. 그래서 영역에 tabindex 를 주고, 클릭하면
# 포커스를 잡게 한다. 핸들러는 addEventListener 가 아니라 on* 프로퍼티로 단다 --
# 매 렌더마다 덮어써지므로 리스너가 쌓이지 않는다.
_PASTE_JS = """
export default function (component) {
  const { data, parentElement, setStateValue } = component

  const zone = parentElement.querySelector("#zone")
  const hint = parentElement.querySelector("#hint")
  const thumbs = parentElement.querySelector("#thumbs")
  if (!zone || !hint || !thumbs) return

  const images = Array.isArray(data && data.images) ? data.images : []
  const max = (data && data.max) || 4
  const limit = (data && data.maxBytes) || 10 * 1024 * 1024

  hint.textContent = images.length
    ? images.length + "장 붙여넣었습니다. 더 붙이려면 다시 클릭하고 Ctrl+V"
    : "여기를 클릭한 뒤 Ctrl+V 로 화면 캡처를 붙여넣으세요 (끌어다 놓아도 됩니다)"

  // 썸네일은 매번 data 기준으로 다시 그린다. 파이썬이 목록을 비우면 화면도 비어야 한다.
  thumbs.replaceChildren()
  images.forEach((source, index) => {
    const wrap = document.createElement("div")
    wrap.className = "idv-thumb"

    const image = document.createElement("img")
    image.src = source
    image.alt = "붙여넣은 캡처 " + (index + 1)

    const remove = document.createElement("button")
    remove.type = "button"
    remove.title = "이 캡처 빼기"
    remove.textContent = "\\u00d7"
    remove.onclick = (event) => {
      event.stopPropagation()
      setStateValue("images", images.filter((_, other) => other !== index))
    }

    wrap.append(image, remove)
    thumbs.append(wrap)
  })

  const add = (fileList) => {
    const picked = Array.from(fileList || [])
      .filter((file) => file.type && file.type.startsWith("image/"))
      .filter((file) => file.size <= limit)
      .slice(0, Math.max(0, max - images.length))
    if (!picked.length) return

    Promise.all(
      picked.map(
        (file) =>
          new Promise((resolve, reject) => {
            const reader = new FileReader()
            reader.onload = () => resolve(reader.result)
            reader.onerror = reject
            reader.readAsDataURL(file)
          })
      )
    ).then((encoded) => {
      setStateValue("images", images.concat(encoded).slice(0, max))
    })
  }

  zone.onclick = () => zone.focus()
  zone.onpaste = (event) => {
    const files = event.clipboardData && event.clipboardData.files
    if (files && files.length) {
      event.preventDefault()
      add(files)
    }
  }
  zone.ondragover = (event) => {
    event.preventDefault()
    zone.classList.add("is-over")
  }
  zone.ondragleave = () => zone.classList.remove("is-over")
  zone.ondrop = (event) => {
    event.preventDefault()
    zone.classList.remove("is-over")
    add(event.dataTransfer && event.dataTransfer.files)
  }
}
"""

# 컴포넌트 등록은 import 시점에 한 번만 한다. 함수 안에서 매번 등록하면 같은
# 이름이 거듭 등록되어 동작이 꼬인다.
_PASTE = st.components.v2.component(
    "idv_screenshot_paste",
    html=_PASTE_HTML,
    css=_PASTE_CSS,
    js=_PASTE_JS,
)


def _screenshot_paste(key: str) -> list[str]:
    """붙여넣은 캡처를 data URL 목록으로 돌려준다."""
    state = st.session_state.get(key) or {}
    images = state.get("images") if isinstance(state, dict) else getattr(state, "images", None)
    images = list(images) if isinstance(images, list) else []

    result = _PASTE(
        key=key,
        data={
            "images": images,
            "max": config.MAX_REPORT_IMAGES,
            "maxBytes": 10 * 1024 * 1024,
        },
        on_images_change=lambda: None,
    )
    current = getattr(result, "images", None)
    return list(current) if isinstance(current, list) else images


# --------------------------------------------------------------------------
# 신고 폼
# --------------------------------------------------------------------------

def report_form(
    *,
    section: str,
    key_prefix: str,
    doc_id: Optional[int] = None,
    exception: Optional[str] = None,
) -> None:
    """캡처와 증상을 받아 신고 한 건을 남긴다."""
    paste_key = f"{key_prefix}_paste"
    upload_key = f"{key_prefix}_files"
    message_key = f"{key_prefix}_message"
    attach_key = f"{key_prefix}_attach"

    # 위젯 상태 비우기는 위젯이 만들어지기 전에 해야 한다. 그래서 보내기 직후가
    # 아니라 다음 실행의 첫머리에서 치운다.
    if st.session_state.pop(f"{key_prefix}_reset", False):
        for key in (paste_key, upload_key, message_key):
            st.session_state.pop(key, None)

    # 보낸 결과는 보낸 자리에 띄운다. 검수 화면에서 신고하고 신고함 탭에 안내가
    # 뜨면 정작 보낸 사람은 그것을 보지 못한다.
    saved = st.session_state.pop(f"{key_prefix}_flash", None)
    if saved:
        st.success(f"수정 요청을 남겼습니다 → `data/reports/{saved}/report.md`")

    pasted = _screenshot_paste(paste_key)

    uploaded = st.file_uploader(
        "캡처 파일로 올리기",
        type=config.REPORT_IMAGE_EXTENSIONS,
        accept_multiple_files=True,
        key=upload_key,
        help="이미 파일로 저장해 둔 캡처가 있으면 여기로 올리세요.",
    )
    uploaded = uploaded or []

    message = st.text_area(
        "무엇이 잘못됐나요?",
        key=message_key,
        placeholder="예) 품목 3행 단가가 원문(12,000)과 다르게 1,200 으로 들어옵니다.",
        height=110,
    )

    attach = True
    if doc_id is not None:
        attach = st.checkbox(
            "이 문서의 맥락을 함께 보내기",
            value=True,
            key=attach_key,
            help=(
                "문서 행·검증 오류·저장된 품목·아직 저장하지 않은 편집값·Docling 추출 "
                "원문을 함께 담습니다. 캡처만으로는 값이 왜 그렇게 나왔는지 알기 어렵습니다."
            ),
        )

    total = len(pasted) + len(uploaded)
    if total:
        st.caption(f"첨부 {total}장 (붙여넣기 {len(pasted)}장 · 파일 {len(uploaded)}장)")

    ready = bool(message.strip())
    with st.container(horizontal=True, horizontal_alignment="left"):
        send = st.button(
            "수정 요청 보내기",
            key=f"{key_prefix}_send",
            type="primary",
            icon=":material/bug_report:",
            disabled=not ready,
        )
    if not ready:
        st.caption("증상을 한 줄이라도 적어야 보낼 수 있습니다.")

    if not send:
        return

    context = report.collect_context(
        doc_id if attach else None,
        session=st.session_state if attach else None,
        exception=exception,
    )
    try:
        folder = report.create(
            message=message.strip(),
            section=section,
            doc_id=doc_id,
            pasted_images=pasted,
            uploaded_files=uploaded,
            context=context,
        )
    except Exception as exc:
        st.error(f"신고를 저장하지 못했습니다: {type(exc).__name__}: {exc}")
        return

    st.session_state[f"{key_prefix}_reset"] = True
    st.session_state[f"{key_prefix}_flash"] = folder.name
    st.toast("수정 요청을 남겼습니다.", icon="🐞")
    st.rerun()


# --------------------------------------------------------------------------
# 화면이 깨졌을 때
# --------------------------------------------------------------------------

@contextlib.contextmanager
def guard(section: str, *, key: str) -> Iterator[None]:
    """화면 렌더 중 터진 예외를 잡아 그 자리에서 신고할 수 있게 한다.

    그냥 두면 Streamlit 이 스택만 뱉고 끝나서, 그것을 캡처해 터미널로 옮기는
    수고가 그대로 남는다. 스택은 자동으로 신고에 붙인다.
    """
    try:
        yield
    except Exception as exc:  # noqa: BLE001 - 화면 전체가 죽지 않게 막는 자리다
        trace = report.format_exception(exc)
        st.error(f"'{section}' 화면을 그리는 중 오류가 났습니다: {type(exc).__name__}: {exc}")
        with st.expander("오류 내용 보기"):
            st.code(trace, language="text")
        st.caption("아래로 바로 신고할 수 있습니다. 위 오류 내용은 자동으로 첨부됩니다.")
        report_form(section=f"{section} (렌더 오류)", key_prefix=f"crash_{key}", exception=trace)


# --------------------------------------------------------------------------
# 신고함
# --------------------------------------------------------------------------

def _summary(record: dict[str, Any]) -> str:
    message = (record.get("message") or "").strip().splitlines()
    head = message[0] if message else "(내용 없음)"
    return head[:70] + ("…" if len(head) > 70 else "")


def render_inbox() -> None:
    st.caption(
        "검수 화면에서 오류를 만나면 그 자리에서 캡처와 함께 신고하세요. "
        "신고는 `data/reports/` 아래에 파일로 쌓입니다 — DB가 죽어 있어도 남습니다. "
        "터미널에서 **\"새 신고 봐줘\"** 라고 하면 내용을 읽고 소스를 고칩니다."
    )

    with st.expander("문서와 무관한 오류 신고하기", icon=":material/bug_report:"):
        report_form(section="일반", key_prefix="inbox_general")

    st.divider()

    records = report.load_all()
    if not records:
        st.info("아직 신고가 없습니다.")
        return

    scope = st.segmented_control(
        "보기",
        ["미처리", "전체"],
        default="미처리",
        key="inbox_scope",
        label_visibility="collapsed",
    )
    shown = [
        r for r in records
        if scope == "전체" or r.get("status") == report.STATUS_OPEN
    ]
    st.caption(f"미처리 {sum(1 for r in records if r.get('status') == report.STATUS_OPEN)}건 / 전체 {len(records)}건")

    if not shown:
        st.success("미처리 신고가 없습니다. 🎉")
        return

    for record in shown:
        resolved = record.get("status") == report.STATUS_RESOLVED
        mark = "✅" if resolved else "🐞"
        created = format_datetime(record.get("created_at"))
        label = f"{mark} #{record.get('number', 0):04d} · {created} · {_summary(record)}"
        with st.expander(label):
            _render_record(record)


def _render_record(record: dict[str, Any]) -> None:
    folder = record["path"]
    slug = record.get("slug") or folder.name

    st.markdown(f"**화면** {record.get('section') or '-'}")
    if record.get("document_id"):
        st.markdown(f"**문서** #{record['document_id']}")
    st.markdown(record.get("message") or "_(내용 없음)_")

    images = [folder / name for name in record.get("images") or []]
    images = [p for p in images if p.exists()]
    if images:
        st.image([str(p) for p in images], width=320)

    context = record.get("context") or {}
    if context.get("exception"):
        with st.expander("첨부된 예외 스택"):
            st.code(context["exception"], language="text")

    st.caption(f"파일 위치 `data/reports/{slug}/report.md`")

    pending_key = f"inbox_delete_{slug}"
    if st.session_state.get(pending_key):
        st.warning("이 신고를 지웁니다. 캡처 파일까지 함께 지워지며 되돌릴 수 없습니다.")
        with st.container(horizontal=True, horizontal_alignment="left"):
            if st.button("예, 지웁니다", key=f"{pending_key}_yes", type="primary"):
                report.delete([slug])
                st.session_state.pop(pending_key, None)
                st.toast("신고를 지웠습니다.", icon="🗑️")
                st.rerun()
            if st.button("취소", key=f"{pending_key}_no"):
                st.session_state.pop(pending_key, None)
                st.rerun()
        return

    resolved = record.get("status") == report.STATUS_RESOLVED
    with st.container(horizontal=True, horizontal_alignment="left"):
        if resolved:
            if st.button("다시 열기", key=f"inbox_reopen_{slug}", icon=":material/undo:"):
                report.set_status(slug, report.STATUS_OPEN)
                st.rerun()
        else:
            if st.button(
                "처리 완료로 표시",
                key=f"inbox_resolve_{slug}",
                icon=":material/check:",
            ):
                report.set_status(slug, report.STATUS_RESOLVED)
                st.rerun()
        if st.button("삭제", key=f"inbox_delete_btn_{slug}", icon=":material/delete:"):
            st.session_state[pending_key] = True
            st.rerun()
