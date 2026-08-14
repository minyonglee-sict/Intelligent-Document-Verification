"""업로드 -> Docling 추출 -> Ollama 필드 추출 -> 규칙 검증 -> MS-SQL 저장 오케스트레이션.

검수·승인은 여기 없다. 저장까지 끝난 행을 ui_review 가 읽어 사람이 마감한다.
"""

from __future__ import annotations

import hashlib
import re
import traceback
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from . import config, db, extractor, validator
from .schemas import InvoiceFields

_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class ProcessOutcome:
    filename: str
    document_id: Optional[int]
    status: str
    error_count: int = 0
    skipped: bool = False
    message: str = ""


def file_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def store_upload(filename: str, data: bytes) -> Path:
    """업로드된 원본 파일을 data/uploads 아래에 보관한다.

    확장자를 원본 그대로 지켜야 한다. Docling은 확장자로 형식을 판별하므로
    .pptx 를 .pdf 로 저장하면 읽지 못한다.
    """
    config.ensure_dirs()
    source = Path(filename)
    stem = _SAFE_NAME.sub("_", source.stem)[:80] or "document"
    suffix = source.suffix.lower() or ".pdf"
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    path = config.UPLOAD_DIR / f"{stamp}_{stem}{suffix}"
    path.write_bytes(data)
    return path


def process_pdf(
    filename: str, data: bytes, *, skip_duplicates: bool = True
) -> ProcessOutcome:
    """PDF 한 건을 처리해 DB에 저장하고 결과 요약을 돌려준다.

    파이프라인이 실패해도 예외를 올리지 않고 status=FAILED 로 기록한다.
    다건 업로드 중 한 건 때문에 전체가 멈추면 안 되기 때문이다.
    """
    digest = file_hash(data)
    stored_path = store_upload(filename, data)

    # 처리 '시작' 시점에 해시를 선점한다. 문서 1건에 수 분이 걸리므로, 끝나고
    # 저장할 때 중복을 확인하면 그 사이에 시작한 두 번째 실행을 막지 못한다.
    reserved, created = db.reserve_document(
        filename=filename,
        file_hash=digest,
        stored_path=str(stored_path),
        skip_duplicates=skip_duplicates,
    )
    if not created:
        stored_path.unlink(missing_ok=True)  # 처리하지 않을 파일은 남기지 않는다
        state = (
            "처리 중입니다"
            if reserved["status"] == config.STATUS_PROCESSING
            else "등록되어 있습니다"
        )
        return ProcessOutcome(
            filename=filename,
            document_id=reserved["id"],
            status=reserved["status"],
            skipped=True,
            message=f"동일한 파일이 이미 #{reserved['id']} 로 {state}.",
        )

    doc_id = reserved["id"]

    def fail(reason: str, message: str, extraction=None) -> ProcessOutcome:
        db.finalize_document(
            doc_id,
            status=config.STATUS_FAILED,
            is_valid=None,
            markdown=extraction.markdown if extraction else "",
            docling_json=extraction.docling_json if extraction else {},
            fields=None,
            errors=[],
            model=config.OLLAMA_MODEL,
            page_count=extraction.page_count if extraction else 0,
            failure_reason=reason,
        )
        return ProcessOutcome(filename, doc_id, config.STATUS_FAILED, message=message)

    try:
        extraction = extractor.extract(stored_path)
    except Exception as exc:
        return fail(
            f"Docling 추출 실패: {exc}\n{traceback.format_exc(limit=3)}",
            f"추출 실패: {exc}",
        )

    try:
        fields, extraction_issues = validator.extract_fields(extraction.markdown)
    except Exception as exc:
        return fail(f"LLM 필드 추출 실패: {exc}", f"LLM 추출 실패: {exc}", extraction)

    result = validator.validate(extraction.markdown, fields, extraction_issues)
    status = config.STATUS_PENDING if result.is_valid else config.STATUS_ERROR

    db.finalize_document(
        doc_id,
        status=status,
        is_valid=result.is_valid,
        markdown=extraction.markdown,
        docling_json=extraction.docling_json,
        fields=fields,
        errors=result.errors,
        model=config.OLLAMA_MODEL,
        page_count=extraction.page_count,
    )
    return ProcessOutcome(
        filename=filename,
        document_id=doc_id,
        status=status,
        error_count=len(result.errors),
    )


def delete_documents(doc_ids: list[int]) -> int:
    """문서를 DB에서 지우고 업로드 원본 파일까지 함께 치운다. 지운 건수를 돌려준다.

    파일을 남기면 같은 문서를 다시 올렸을 때 data/uploads 에 사본이 쌓인다.
    DB 행이 사라져 중복 검사(file_hash)에 걸리지 않으므로 얼마든지 반복된다.

    DB를 먼저 지운다. 파일부터 지우면 DB 삭제가 실패했을 때 원본 없는 행이 남는다.
    """
    paths = db.stored_paths(doc_ids)
    deleted = db.delete_documents(doc_ids)

    # 우리가 store_upload 로 만든 파일만 지운다. 마이그레이션 등으로 바깥 경로가
    # 들어와 있어도 엉뚱한 파일을 건드리지 않도록 한 번 확인한다.
    upload_dir = config.UPLOAD_DIR.resolve()
    for raw in paths:
        path = Path(raw)
        try:
            resolved = path.resolve()
            if resolved.parent != upload_dir:
                continue
            resolved.unlink(missing_ok=True)
        except OSError:
            pass  # 파일이 잠겨 있어도 DB 삭제까지 되돌릴 이유는 없다

    return deleted


def recheck(doc_id: int, fields: InvoiceFields, note: Optional[str] = None) -> list:
    """검수 화면에서 수정한 값으로 재검증하고 에러 로그를 갱신한다.

    상태는 건드리지 않는다. ERROR -> VALIDATED 전환은 담당자의 승인으로만 일어나야
    하고, 여기서 PENDING으로 되돌리면 검수 목록에서 사라져 승인할 수 없게 된다.
    """
    # 원문을 함께 넘긴다. 품목이 잘렸는지는 원문과 대조해야 알 수 있고, 빼놓으면
    # 처음 검증에서 잡힌 오류가 재검증에서 조용히 사라진다.
    document = db.get_document(doc_id)
    result = validator.revalidate_fields(fields, (document or {}).get("markdown"))
    db.update_fields(doc_id, fields, note)
    db.replace_errors(doc_id, result.errors)
    return result.errors
