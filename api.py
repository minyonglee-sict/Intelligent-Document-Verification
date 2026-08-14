"""Intelligent Document Verification — HTTP API.

실행:  uvicorn api:app --port 8000

Streamlit 화면(main.py)과 같은 엔진을 부르는 또 하나의 진입점이다. 화면을 React 로
옮기면 그쪽이 이 API 를 쓰고, MCP 서버를 붙이면 같은 함수를 도구로 감싼다. 그래서
여기에는 업무 규칙을 새로 두지 않는다 -- app/ 아래 로직을 그대로 호출하기만 한다.
규칙을 두 벌로 만들면 화면과 API 의 판정이 갈라진다.

문서 처리는 Docling 변환과 LLM 호출을 합쳐 수 분이 걸린다. HTTP 요청 하나가 그
시간을 붙들고 있으면 프록시·브라우저 타임아웃에 걸리므로, 업로드는 접수만 하고
작업 번호를 돌려준 뒤 진행 상황을 따로 묻게 한다.
"""

from __future__ import annotations

import threading
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field

from app import config, db, pipeline, validator
from app.schemas import InvoiceFields


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    # 처리 도중 프로세스가 죽으면 PROCESSING 행이 남아 같은 파일의 재업로드를
    # 영원히 막는다. 화면 진입 때와 같은 정리를 여기서도 한 번 돌린다.
    db.cleanup_stale_processing()
    yield


app = FastAPI(
    title="Intelligent Document Verification API",
    description="Streamlit 화면과 같은 엔진을 쓰는 HTTP 진입점",
    version="1.0.0",
    lifespan=lifespan,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 응답 모델
# --------------------------------------------------------------------------

class HealthResponse(BaseModel):
    ok: bool
    database: str
    ollama: str


class DocumentSummary(BaseModel):
    id: int
    filename: str
    status: str
    status_label: str
    page_count: Optional[int] = None
    model: Optional[str] = None
    error_count: int = 0
    created_at: Optional[str] = None
    validated_at: Optional[str] = None


class ValidationError(BaseModel):
    id: Optional[int] = None
    field: Optional[str] = None
    message: str
    severity: str
    source: str
    resolved: bool = False


class DocumentDetail(DocumentSummary):
    fields: InvoiceFields
    errors: list[ValidationError] = Field(default_factory=list)
    reviewer_note: Optional[str] = None
    failure_reason: Optional[str] = None


class JobStatus(BaseModel):
    job_id: str
    state: Literal["QUEUED", "RUNNING", "DONE", "FAILED"]
    filename: str
    document_id: Optional[int] = None
    document_status: Optional[str] = None
    error_count: int = 0
    skipped: bool = False
    message: str = ""
    started_at: str
    finished_at: Optional[str] = None


class RecheckRequest(BaseModel):
    fields: InvoiceFields
    note: Optional[str] = None


class ApproveRequest(BaseModel):
    fields: InvoiceFields
    note: Optional[str] = None
    # 승인은 되돌릴 수 없는 마감이다. 남은 오류를 보고도 넘어가겠다는 뜻을 명시하게
    # 해서, 값을 확인하지 않은 호출이 그대로 VALIDATED 로 가지 않게 한다.
    force: bool = False


class RecheckResponse(BaseModel):
    document_id: int
    errors: list[ValidationError]
    critical_count: int


# --------------------------------------------------------------------------
# 변환
# --------------------------------------------------------------------------

def _error_model(row: dict[str, Any]) -> ValidationError:
    return ValidationError(
        id=row.get("id"),
        field=row.get("field"),
        message=str(row.get("message") or ""),
        severity=str(row.get("severity") or "critical"),
        source=str(row.get("source") or "rule"),
        resolved=bool(row.get("resolved")),
    )


def _summary(row: dict[str, Any], error_count: int) -> DocumentSummary:
    return DocumentSummary(
        id=row["id"],
        filename=row["filename"],
        status=row["status"],
        status_label=config.STATUS_LABELS.get(row["status"], row["status"]),
        page_count=row.get("page_count"),
        model=row.get("model"),
        error_count=error_count,
        created_at=row.get("created_at"),
        validated_at=row.get("validated_at") or None,
    )


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------

@app.get("/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok, db_message = db.health_check()
    try:
        llm_ok, llm_message = validator.health_check()
    except Exception as exc:  # 연결 확인 자체가 실패해도 API 는 살아 있어야 한다
        llm_ok, llm_message = False, f"{type(exc).__name__}: {exc}"
    return HealthResponse(ok=db_ok and llm_ok, database=db_message, ollama=llm_message)


@app.get("/documents", response_model=list[DocumentSummary])
def list_documents(
    status: Optional[str] = Query(default=None, description="ERROR/PENDING/VALIDATED/FAILED"),
) -> list[DocumentSummary]:
    if status and status not in config.STATUS_LABELS:
        raise HTTPException(400, f"알 수 없는 상태입니다: {status}")
    rows = db.list_documents(status)
    counts = db.error_counts()
    return [_summary(r, counts.get(r["id"], 0)) for r in rows]


@app.get("/documents/counts")
def status_counts() -> dict[str, int]:
    return db.status_counts()


def _load(doc_id: int) -> dict[str, Any]:
    row = db.get_document(doc_id)
    if not row:
        raise HTTPException(404, f"문서 #{doc_id} 을(를) 찾을 수 없습니다.")
    return row


@app.get("/documents/{doc_id}", response_model=DocumentDetail)
def get_document(doc_id: int) -> DocumentDetail:
    row = _load(doc_id)
    errors = [_error_model(e) for e in db.get_errors(doc_id, only_open=False)]
    open_errors = sum(1 for e in errors if not e.resolved)
    return DocumentDetail(
        **_summary(row, open_errors).model_dump(),
        fields=db.load_fields(row),
        errors=errors,
        reviewer_note=row.get("reviewer_note") or None,
        failure_reason=row.get("failure_reason") or None,
    )


@app.get("/documents/{doc_id}/markdown", response_model=dict)
def get_markdown(doc_id: int) -> dict[str, Any]:
    """Docling 추출 원문. 검수 화면에서 원문과 대조할 때 쓴다."""
    row = _load(doc_id)
    return {"document_id": doc_id, "markdown": row.get("markdown") or ""}


# --------------------------------------------------------------------------
# 처리 (비동기)
# --------------------------------------------------------------------------

# 작업 상태는 프로세스 메모리에만 둔다. 재시작하면 사라지지만, 문서의 실제 결과는
# DB 에 남으므로 잃는 것은 진행 표시뿐이다. 여러 대로 늘릴 때는 이 자리를 공용
# 저장소로 바꿔야 한다.
_jobs: dict[str, JobStatus] = {}
_jobs_lock = threading.Lock()


def _set_job(job: JobStatus) -> None:
    with _jobs_lock:
        _jobs[job.job_id] = job


def _run_job(job_id: str, filename: str, data: bytes, skip_duplicates: bool) -> None:
    with _jobs_lock:
        job = _jobs[job_id].model_copy(update={"state": "RUNNING"})
        _jobs[job_id] = job

    try:
        outcome = pipeline.process_pdf(filename, data, skip_duplicates=skip_duplicates)
    except Exception as exc:
        # process_pdf 는 파이프라인 실패를 FAILED 로 기록하고 예외를 올리지 않는다.
        # 여기까지 오는 것은 그 바깥의 사고이므로 작업만 실패로 닫는다.
        _set_job(
            _jobs[job_id].model_copy(
                update={
                    "state": "FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                    "finished_at": _now(),
                }
            )
        )
        return

    _set_job(
        _jobs[job_id].model_copy(
            update={
                "state": "DONE",
                "document_id": outcome.document_id,
                "document_status": outcome.status,
                "error_count": outcome.error_count,
                "skipped": outcome.skipped,
                "message": outcome.message,
                "finished_at": _now(),
            }
        )
    )


@app.post("/documents", response_model=JobStatus, status_code=202)
async def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    skip_duplicates: bool = Query(default=True),
) -> JobStatus:
    """파일을 접수하고 작업 번호를 돌려준다. 처리는 뒤에서 이어진다."""
    filename = file.filename or "document.pdf"
    suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in config.UPLOAD_EXTENSIONS:
        raise HTTPException(
            415,
            f"지원하지 않는 형식입니다: .{suffix}. "
            f"허용: {', '.join(config.UPLOAD_EXTENSIONS)}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(400, "빈 파일입니다.")

    job = JobStatus(
        job_id=uuid.uuid4().hex,
        state="QUEUED",
        filename=filename,
        started_at=_now(),
    )
    _set_job(job)
    background.add_task(_run_job, job.job_id, filename, data, skip_duplicates)
    return job


@app.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, f"작업 {job_id} 을(를) 찾을 수 없습니다.")
    return job


@app.get("/jobs", response_model=list[JobStatus])
def list_jobs() -> list[JobStatus]:
    with _jobs_lock:
        return sorted(_jobs.values(), key=lambda j: j.started_at, reverse=True)


# --------------------------------------------------------------------------
# 검수
# --------------------------------------------------------------------------

@app.post("/documents/{doc_id}/recheck", response_model=RecheckResponse)
def recheck(doc_id: int, body: RecheckRequest) -> RecheckResponse:
    """고친 값을 저장하고 규칙 검증을 다시 돌린다. 상태는 바꾸지 않는다."""
    _load(doc_id)
    errors = pipeline.recheck(doc_id, body.fields, body.note)
    models = [
        ValidationError(
            field=e.field, message=e.message, severity=e.severity, source=e.source
        )
        for e in errors
    ]
    return RecheckResponse(
        document_id=doc_id,
        errors=models,
        critical_count=sum(1 for e in models if e.severity == "critical"),
    )


@app.post("/documents/{doc_id}/approve", response_model=DocumentDetail)
def approve(doc_id: int, body: ApproveRequest) -> DocumentDetail:
    """VALIDATED 로 마감한다. 남은 critical 오류가 있으면 force 를 요구한다.

    화면에서 사람이 누르는 버튼에 대응한다. 자동화가 스스로 승인하지 않도록,
    오류가 남아 있으면 한 번 되묻는 자리를 API 에도 그대로 둔다.
    """
    _load(doc_id)
    remaining = pipeline.recheck(doc_id, body.fields, body.note)
    critical = [e for e in remaining if e.severity == "critical"]
    if critical and not body.force:
        raise HTTPException(
            409,
            {
                "message": (
                    f"승인하려는 값에 아직 {len(critical)}건의 오류가 있습니다. "
                    f"그대로 승인하려면 force=true 로 다시 요청하세요."
                ),
                "errors": [
                    {"field": e.field, "message": e.message, "severity": e.severity}
                    for e in critical
                ],
            },
        )

    db.approve(doc_id, body.fields, body.note)
    return get_document(doc_id)


@app.delete("/documents/{doc_id}", response_model=dict)
def delete_document(doc_id: int) -> dict[str, Any]:
    """DB 행과 업로드 원본 파일을 함께 지운다. 되돌릴 수 없다."""
    _load(doc_id)
    deleted = pipeline.delete_documents([doc_id])
    return {"deleted": deleted, "document_id": doc_id}
