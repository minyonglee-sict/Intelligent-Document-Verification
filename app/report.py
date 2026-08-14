"""오류 신고 저장 계층.

신고는 DB가 아니라 파일로 남긴다. 신고하려는 고장 중에는 DB 연결 실패도 있는데,
저장에 DB가 필요하면 정작 그 고장을 신고할 수 없다. 파일이면 DB가 죽어 있어도
남는다. data/ 는 이미 .gitignore 에 있어 캡처에 찍힌 송장 내용이 저장소로 새어
나가지도 않는다.

한 건이 폴더 하나다.

    data/reports/0007_20260813-101532_doc12/
        report.md        사람과 터미널의 Claude 가 읽는 본문
        context.json     기계가 읽는 원본 맥락 + 처리 상태
        screenshot_1.png

터미널에서 소스를 고칠 때 필요한 것은 캡처 이미지가 아니라 그 순간의 값이다.
그래서 문서 행·검증 오류·저장된 품목에 더해, 화면에서 고치는 중이라 아직 DB에
없는 편집값(위젯 상태)과 직전 예외 스택까지 함께 담는다.
"""

from __future__ import annotations

import base64
import json
import platform
import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import config

STATUS_OPEN = "OPEN"
STATUS_RESOLVED = "RESOLVED"

# 캡처가 붙어 있어도 원문이 없으면 값이 왜 그렇게 뽑혔는지 알 수 없다. 다만 통째로
# 넣으면 report.md 가 수만 자가 되므로 앞부분만 남긴다. 전문은 DB에 그대로 있다.
#
# 실제 송장이 5천 자 안팎이라 4천 자로 끊으면 표 뒤에 밀려난 품목 -- 파싱이 실패해
# 표 밖으로 떨어진 바로 그 행들 -- 이 잘려 나간다. 정작 원인이 있는 자리다.
_MARKDOWN_PREVIEW_CHARS = 12000

_DATA_URL = re.compile(r"^data:(image/[a-z.+-]+);base64,(.+)$", re.IGNORECASE | re.DOTALL)
_EXTENSION_BY_MIME = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/bmp": "bmp",
}


# --------------------------------------------------------------------------
# 맥락 수집
# --------------------------------------------------------------------------

def _jsonable(value: Any, *, depth: int = 0) -> Any:
    """JSON으로 떨어지지 않는 값도 버리지 않고 문자열로 남긴다.

    맥락 수집이 직렬화 하나 때문에 실패하면 신고 자체가 날아간다. 못 옮기는 값은
    repr 로라도 남기는 편이 낫다.
    """
    if depth > 6:
        return "..."
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v, depth=depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth=depth + 1) for v in value]
    # numpy/pandas 스칼라는 item() 으로 파이썬 값이 된다.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            return _jsonable(item(), depth=depth + 1)
        except Exception:
            pass
    return repr(value)


def _app_info() -> dict[str, Any]:
    """환경. 비밀번호는 절대 담지 않는다 -- 신고 파일은 캡처와 함께 오간다."""
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "ollama_model": config.OLLAMA_MODEL,
        "ollama_host": config.OLLAMA_HOST,
        "mssql_server": config.MSSQL_SERVER,
        "mssql_database": config.MSSQL_DATABASE,
        "mssql_auth": "SQL 인증" if config.MSSQL_USER else "Windows 인증",
    }
    try:
        import streamlit

        info["streamlit"] = streamlit.__version__
    except Exception:
        pass
    try:
        info["git_commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=config.BASE_DIR,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip() or "(알 수 없음)"
    except Exception:
        info["git_commit"] = "(알 수 없음)"
    return info


def collect_context(
    doc_id: Optional[int],
    *,
    session: Optional[Mapping[str, Any]] = None,
    exception: Optional[str] = None,
) -> dict[str, Any]:
    """신고에 붙일 맥락을 모은다.

    DB가 죽어 있어도 신고는 남아야 하므로 조회 실패는 예외로 올리지 않고
    'db_error' 로 적어 둔다 -- 그 실패 자체가 신고 내용일 수 있다.
    """
    context: dict[str, Any] = {
        "document": None,
        "errors": [],
        "line_items": [],
        "unsaved_edits": {},
        "markdown_preview": None,
        "exception": exception,
        "app": _app_info(),
    }

    if doc_id is not None:
        try:
            from . import db

            document = db.get_document(doc_id)
            if document:
                markdown = document.get("markdown") or ""
                context["markdown_preview"] = markdown[:_MARKDOWN_PREVIEW_CHARS]
                context["markdown_truncated"] = len(markdown) > _MARKDOWN_PREVIEW_CHARS
                # 원문과 Docling 원시 JSON 은 따로 담았거나 너무 커서 표에서 뺀다.
                context["document"] = {
                    k: _jsonable(v)
                    for k, v in document.items()
                    if k not in ("markdown", "docling_json")
                }
            context["errors"] = [
                _jsonable(e) for e in db.get_errors(doc_id, only_open=False)
            ]
            context["line_items"] = [_jsonable(i) for i in db.get_line_items(doc_id)]
        except Exception as exc:
            context["db_error"] = f"{type(exc).__name__}: {exc}"

    # 화면에서 고치는 중이라 아직 저장되지 않은 값. 표 편집기는 원본 대비 델타를
    # 위젯 상태에 들고 있어서, '고쳤는데 반영이 안 된다' 류의 신고에서 결정적이다.
    if session is not None and doc_id is not None:
        prefix = f"doc{doc_id}_"
        context["unsaved_edits"] = {
            key: _jsonable(value)
            for key, value in session.items()
            if isinstance(key, str) and key.startswith(prefix)
        }

    return context


def format_exception(exc: BaseException) -> str:
    return "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))


# --------------------------------------------------------------------------
# 쓰기
# --------------------------------------------------------------------------

def _next_number() -> int:
    """폴더 이름 앞자리에서 다음 번호를 얻는다."""
    highest = 0
    if config.REPORTS_DIR.exists():
        for path in config.REPORTS_DIR.iterdir():
            if path.is_dir():
                head = path.name.split("_", 1)[0]
                if head.isdigit():
                    highest = max(highest, int(head))
    return highest + 1


def decode_pasted_image(data_url: str) -> Optional[tuple[bytes, str]]:
    """브라우저에서 붙여넣은 data URL 을 (바이트, 확장자) 로 푼다."""
    match = _DATA_URL.match(data_url.strip())
    if not match:
        return None
    mime, payload = match.group(1).lower(), match.group(2)
    try:
        return base64.b64decode(payload), _EXTENSION_BY_MIME.get(mime, "png")
    except Exception:
        return None


def create(
    *,
    message: str,
    section: str,
    doc_id: Optional[int] = None,
    pasted_images: Iterable[str] = (),
    uploaded_files: Iterable[Any] = (),
    context: Optional[dict[str, Any]] = None,
) -> Path:
    """신고 한 건을 폴더로 남기고 그 경로를 돌려준다."""
    config.REPORTS_DIR.mkdir(parents=True, exist_ok=True)

    now = datetime.now().astimezone()
    number = _next_number()
    slug = f"{number:04d}_{now.strftime('%Y%m%d-%H%M%S')}"
    if doc_id is not None:
        slug += f"_doc{doc_id}"

    folder = config.REPORTS_DIR / slug
    folder.mkdir(parents=True, exist_ok=False)

    image_names: list[str] = []
    for data_url in pasted_images:
        decoded = decode_pasted_image(data_url)
        if not decoded:
            continue
        payload, extension = decoded
        name = f"screenshot_{len(image_names) + 1}.{extension}"
        (folder / name).write_bytes(payload)
        image_names.append(name)

    for uploaded in uploaded_files:
        extension = Path(uploaded.name).suffix.lstrip(".").lower() or "png"
        name = f"screenshot_{len(image_names) + 1}.{extension}"
        (folder / name).write_bytes(uploaded.getvalue())
        image_names.append(name)

    record = {
        "number": number,
        "slug": slug,
        "status": STATUS_OPEN,
        "created_at": now.isoformat(timespec="seconds"),
        "section": section,
        "document_id": doc_id,
        "message": message,
        "images": image_names,
        "context": context or {},
    }
    (folder / "context.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "report.md").write_text(_render_markdown(record), encoding="utf-8")
    return folder


# --------------------------------------------------------------------------
# report.md 본문
# --------------------------------------------------------------------------

def _cell(value: Any) -> str:
    """표 칸 하나. 파이프는 표를 깨뜨리므로 escape 하고 줄바꿈은 눕힌다."""
    if value is None:
        return ""
    return str(value).replace("|", "\\|").replace("\n", " ")


def _fenced(body: str, language: str = "") -> list[str]:
    """코드 블록. 본문에 울타리가 들어 있어도 깨지지 않게 더 긴 울타리를 쓴다.

    Docling 추출 원문은 남의 문서에서 나온 임의의 텍스트라 백틱이 얼마든지 있을
    수 있다. 세 개로 고정하면 그 자리에서 블록이 끊겨 뒷부분이 본문으로 샌다.
    """
    longest = max((len(run) for run in re.findall(r"`+", body)), default=0)
    fence = "`" * max(3, longest + 1)
    return [fence + language, body.rstrip("\n"), fence]


def _table(rows: Iterable[tuple[str, Any]]) -> str:
    lines = ["| 항목 | 값 |", "| --- | --- |"]
    for name, value in rows:
        lines.append(f"| {_cell(name)} | {_cell(value)} |")
    return "\n".join(lines)


def _render_markdown(record: dict[str, Any]) -> str:
    context = record.get("context") or {}
    created = datetime.fromisoformat(record["created_at"])
    parts: list[str] = [
        f"# 오류 신고 #{record['number']:04d}",
        "",
        _table(
            [
                ("신고 시각", created.strftime("%Y-%m-%d %H:%M:%S")),
                ("화면", record.get("section") or "-"),
                ("문서 ID", record.get("document_id") if record.get("document_id") else "-"),
                ("상태", record.get("status")),
            ]
        ),
        "",
        "## 증상 (신고자 작성)",
        "",
        record.get("message") or "(작성되지 않음)",
        "",
    ]

    if record.get("images"):
        parts += ["## 첨부 화면", ""]
        parts += [f"- `{name}`" for name in record["images"]]
        parts.append("")

    if context.get("exception"):
        parts += ["## 직전 예외", ""] + _fenced(context["exception"], "text") + [""]

    if context.get("db_error"):
        parts += [
            "## 맥락 수집 중 DB 오류",
            "",
            f"`{context['db_error']}`",
            "",
            "> 맥락을 못 읽은 것이지 신고가 잘못된 것은 아니다. 이 오류 자체가 원인일 수 있다.",
            "",
        ]

    document = context.get("document")
    if document:
        parts += ["## 문서", "", _table(sorted(document.items())), ""]

    errors = context.get("errors") or []
    if errors:
        parts += [f"## 검증 오류 ({len(errors)}건)", ""]
        parts += ["| 필드 | 심각도 | 출처 | 해결됨 | 메시지 |", "| --- | --- | --- | --- | --- |"]
        for error in errors:
            cells = [
                error.get("field") or "-",
                error.get("severity") or "-",
                error.get("source") or "-",
                "예" if error.get("resolved") else "아니오",
                error.get("message") or "",
            ]
            parts.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        parts.append("")

    line_items = context.get("line_items") or []
    if line_items:
        parts += [f"## 저장된 품목 ({len(line_items)}행)", ""]
        parts += ["| 번호 | 품목 | 수량 | 단가 | 세액 | 금액 |", "| --- | --- | --- | --- | --- | --- |"]
        for item in line_items:
            cells = [
                item.get("position"),
                item.get("description") or "",
                item.get("quantity"),
                item.get("unit_price"),
                item.get("tax"),
                item.get("amount"),
            ]
            parts.append("| " + " | ".join(_cell(c) for c in cells) + " |")
        parts.append("")

    unsaved = context.get("unsaved_edits") or {}
    if unsaved:
        parts += [
            "## 화면에서 고치는 중이던 값 (아직 저장 전)",
            "",
            "> 표 편집기(`*_items`)는 원본 대비 편집 델타만 들고 있다.",
            "",
        ] + _fenced(json.dumps(unsaved, ensure_ascii=False, indent=2), "json") + [""]

    if context.get("markdown_preview"):
        suffix = " (앞부분만)" if context.get("markdown_truncated") else ""
        parts += [f"## Docling 추출 원문{suffix}", ""]
        parts += _fenced(context["markdown_preview"], "text") + [""]

    if context.get("app"):
        parts += ["## 환경", "", _table(sorted(context["app"].items())), ""]

    return "\n".join(parts)


# --------------------------------------------------------------------------
# 읽기 / 상태 변경
# --------------------------------------------------------------------------

def load_all() -> list[dict[str, Any]]:
    """신고를 최신순으로 읽는다. 깨진 폴더 하나가 목록 전체를 막지 않게 한다."""
    if not config.REPORTS_DIR.exists():
        return []

    records: list[dict[str, Any]] = []
    for folder in config.REPORTS_DIR.iterdir():
        if not folder.is_dir():
            continue
        source = folder / "context.json"
        try:
            record = json.loads(source.read_text(encoding="utf-8"))
        except Exception as exc:
            record = {
                "number": 0,
                "slug": folder.name,
                "status": STATUS_OPEN,
                "created_at": datetime.fromtimestamp(
                    folder.stat().st_mtime
                ).astimezone().isoformat(timespec="seconds"),
                "section": "(읽을 수 없음)",
                "message": f"context.json 을 읽지 못했습니다: {exc}",
                "images": [],
                "context": {},
            }
        record["path"] = folder
        records.append(record)

    records.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return records


def open_count() -> int:
    return sum(1 for r in load_all() if r.get("status") == STATUS_OPEN)


def set_status(slug: str, status: str) -> bool:
    """처리 상태를 바꾸고 report.md 도 같이 다시 쓴다."""
    folder = config.REPORTS_DIR / slug
    source = folder / "context.json"
    if not source.exists():
        return False
    record = json.loads(source.read_text(encoding="utf-8"))
    record["status"] = status
    record["status_changed_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    source.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "report.md").write_text(_render_markdown(record), encoding="utf-8")
    return True


def delete(slugs: Iterable[str]) -> int:
    """신고 폴더를 지운다. data/reports 밖의 경로는 건드리지 않는다."""
    root = config.REPORTS_DIR.resolve()
    removed = 0
    for slug in slugs:
        folder = (config.REPORTS_DIR / slug).resolve()
        if folder.parent != root or not folder.is_dir():
            continue
        shutil.rmtree(folder)
        removed += 1
    return removed
