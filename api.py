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

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Literal, Optional

from fastapi import (
    APIRouter,
    Depends,
    FastAPI,
    File,
    Form,
    Header,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import chat_bridge
from app import auth, config, db, mailer, mq, pipeline, report, validator
from app.schemas import InvoiceFields


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    db.init_db()
    # 처리 도중 프로세스가 죽으면 PROCESSING 행이 남아 같은 파일의 재업로드를
    # 영원히 막는다. 화면 진입 때와 같은 정리를 여기서도 한 번 돌린다.
    db.cleanup_stale_processing()

    # MCP 연결은 앱이 사는 동안 하나만 유지한다. 요청마다 새로 붙으면 그때마다
    # 파이썬 프로세스가 떠서 질문 하나에 수 초가 걸린다. 붙지 못해도 API 는 떠야
    # 한다 -- 채팅만 못 쓰고 나머지 경로는 그대로 돈다.
    try:
        await chat_bridge.bridge.start()
    except Exception:
        pass

    yield

    await chat_bridge.bridge.stop()


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
    # PROCESSING/FAILED 상태처럼 머리말 추출이 아직 안 됐거나 못 끝난 문서는
    # doc_type 이 비어 있다 -- 그런 경우 "UNKNOWN"/"문서"로 채워서 돌려준다.
    doc_type: str = "UNKNOWN"
    doc_type_label: str = "문서"
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
    doc_type = row.get("doc_type") or "UNKNOWN"
    doc_type_label = validator.DOC_TYPE_LABELS.get(
        doc_type, validator.DOC_TYPE_LABELS["UNKNOWN"]
    )["name"]
    return DocumentSummary(
        id=row["id"],
        filename=row["filename"],
        status=row["status"],
        status_label=config.STATUS_LABELS.get(row["status"], row["status"]),
        doc_type=doc_type,
        doc_type_label=doc_type_label,
        page_count=row.get("page_count"),
        model=row.get("model"),
        error_count=error_count,
        created_at=row.get("created_at"),
        validated_at=row.get("validated_at") or None,
    )


# --------------------------------------------------------------------------
# 인증
#
# 계정은 두 갈래로 생긴다: 관리자가 create_user.py 로 직접 만들거나, 누구나
# 화면의 가입 화면에서 스스로 만든다. 로그인하면 세션 토큰을 돌려주고, 그
# 뒤로는 모든 요청이 Authorization: Bearer <토큰> 을 실어 보낸다. /health 와
# /auth/* 만 로그인 없이 열려 있다 -- 로그인·가입 화면 자체가 뜨려면 그 전에
# 뭔가를 부를 수 있어야 하기 때문이다. 나머지는 전부 `protected` 라우터에
# 물려서, 매 함수마다 Depends(require_user) 를 반복해 적지 않아도 자동으로
# 막힌다.
# --------------------------------------------------------------------------

class LoginRequest(BaseModel):
    username: str
    password: str


class SignupRequest(BaseModel):
    username: str
    display_name: str
    password: str


class ForgotPasswordRequest(BaseModel):
    username: str
    message: str = ""


class LoginResponse(BaseModel):
    token: str
    username: str
    display_name: str
    role: str


class CurrentUser(BaseModel):
    username: str
    display_name: str
    role: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


def require_user(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "로그인이 필요합니다.")
    token = authorization.removeprefix("Bearer ").strip()
    user = db.get_session_user(token)
    if not user:
        raise HTTPException(401, "로그인이 만료되었거나 유효하지 않습니다. 다시 로그인하세요.")
    return user


def require_admin(user: dict[str, Any] = Depends(require_user)) -> dict[str, Any]:
    if user.get("role") != config.ROLE_ADMIN:
        raise HTTPException(403, "관리자만 할 수 있습니다.")
    return user


protected = APIRouter(dependencies=[Depends(require_user)])
admin_router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


@app.post("/auth/login", response_model=LoginResponse)
def login(body: LoginRequest) -> LoginResponse:
    user = db.get_user_by_username(body.username.strip())
    if not user or not auth.verify_password(
        body.password, user["password_hash"], user["password_salt"]
    ):
        # 아이디가 없는지 비밀번호가 틀렸는지는 구분해 주지 않는다 -- 구분해 주면
        # 존재하는 아이디를 무차별로 캐낼 수 있는 자리가 생긴다.
        raise HTTPException(401, "아이디 또는 비밀번호가 올바르지 않습니다.")
    token = auth.new_token()
    db.create_session(int(user["id"]), token, config.SESSION_TTL_HOURS)
    return LoginResponse(
        token=token,
        username=user["username"],
        display_name=user["display_name"],
        role=user.get("role") or config.ROLE_USER,
    )


@app.post("/auth/signup", response_model=LoginResponse, status_code=201)
def signup(body: SignupRequest) -> LoginResponse:
    username = body.username.strip()
    display_name = body.display_name.strip()
    if not username or not display_name:
        raise HTTPException(400, "아이디와 이름을 입력하세요.")
    if len(body.password) < 8:
        raise HTTPException(400, "비밀번호는 8자 이상이어야 합니다.")
    if db.get_user_by_username(username):
        raise HTTPException(409, f"이미 있는 아이디입니다: {username}")

    # 역할은 요청 본문에서 안 받는다 -- SignupRequest 에 role 필드 자체가 없다.
    # 가입은 항상 일반 사용자다; 관리자 승격은 화면(사용자 관리)에서만 된다.
    password_hash, salt = auth.hash_password(body.password)
    user_id = db.create_user(username, display_name, password_hash, salt, role=config.ROLE_USER)
    token = auth.new_token()
    db.create_session(user_id, token, config.SESSION_TTL_HOURS)
    return LoginResponse(token=token, username=username, display_name=display_name, role=config.ROLE_USER)


@app.post("/auth/forgot-password", response_model=dict)
def forgot_password(body: ForgotPasswordRequest) -> dict[str, Any]:
    """로그인 못 하는 사람이 로그인 화면에서 관리자에게 재설정을 요청하는 자리다.
    본인 확인 수단이 없으니 여기서 실제로 비밀번호를 바꾸지는 않는다 -- 관리자에게
    메일만 보내고, 실제 재설정은 사람이 create_user.py --reset-password 로 한다.
    """
    username = body.username.strip()
    if not username:
        raise HTTPException(400, "아이디를 입력하세요.")

    user = db.get_user_by_username(username)
    if user:
        subject = f"[Intelligent Document Verification] 비밀번호 재설정 요청: {username}"
        text = (
            f"아이디: {username} ({user['display_name']})\n\n"
            f"요청 메시지:\n{body.message.strip() or '(없음)'}\n\n"
            f"재설정하려면:\n"
            f'  python create_user.py {username} "{user["display_name"]}" --reset-password\n'
        )
        try:
            mailer.send_email(config.ADMIN_CONTACT_EMAIL, subject, text)
        except Exception as exc:
            raise HTTPException(503, f"메일을 보내지 못했습니다: {exc}")
    # 없는 아이디여도 결과는 똑같이 보여준다 -- 로그인 실패 메시지를 아이디/비밀번호로
    # 구분해 주지 않는 것과 같은 이유로, 있는 아이디를 무차별로 캐낼 자리를 안 만든다.
    return {"ok": True}


@app.post("/auth/logout", response_model=dict)
def logout(authorization: Optional[str] = Header(default=None)) -> dict[str, Any]:
    if authorization and authorization.startswith("Bearer "):
        db.delete_session(authorization.removeprefix("Bearer ").strip())
    return {"ok": True}


@app.get("/auth/me", response_model=CurrentUser)
def me(user: dict[str, Any] = Depends(require_user)) -> CurrentUser:
    return CurrentUser(
        username=user["username"],
        display_name=user["display_name"],
        role=user.get("role") or config.ROLE_USER,
    )


@app.post("/auth/change-password", response_model=dict)
def change_password(
    body: ChangePasswordRequest, authorization: Optional[str] = Header(default=None)
) -> dict[str, Any]:
    """현재 비밀번호를 확인한 뒤 바꾼다. Depends(require_user) 대신 여기서 직접
    토큰을 검사하는 이유는, db.change_password 가 "이 세션은 살려두고 나머지는
    로그아웃" 하려면 토큰 문자열 자체가 있어야 하기 때문이다(logout 과 같은 이유).
    """
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "로그인이 필요합니다.")
    token = authorization.removeprefix("Bearer ").strip()
    session_user = db.get_session_user(token)
    if not session_user:
        raise HTTPException(401, "로그인이 만료되었거나 유효하지 않습니다. 다시 로그인하세요.")

    user = db.get_user_by_username(session_user["username"])
    if not user or not auth.verify_password(
        body.current_password, user["password_hash"], user["password_salt"]
    ):
        raise HTTPException(401, "현재 비밀번호가 올바르지 않습니다.")
    if len(body.new_password) < 8:
        raise HTTPException(400, "새 비밀번호는 8자 이상이어야 합니다.")

    password_hash, salt = auth.hash_password(body.new_password)
    db.change_password(int(user["id"]), password_hash, salt, token)
    return {"ok": True}


# --------------------------------------------------------------------------
# 사용자 관리 (관리자 전용)
#
# 목록 조회·역할 변경·비밀번호 강제 재설정. 전부 admin_router 에 물려서
# require_admin 을 거친다 -- role='admin' 이 아니면 403.
# --------------------------------------------------------------------------

class AdminUserSummary(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label: str
    created_at: str


class SetRoleRequest(BaseModel):
    role: str


class AdminResetPasswordRequest(BaseModel):
    new_password: str


def _admin_user_model(row: dict[str, Any]) -> AdminUserSummary:
    role = row.get("role") or config.ROLE_USER
    return AdminUserSummary(
        id=int(row["id"]),
        username=row["username"],
        display_name=row["display_name"],
        role=role,
        role_label=config.ROLE_LABELS.get(role, role),
        created_at=row["created_at"],
    )


@admin_router.get("/users", response_model=list[AdminUserSummary])
def admin_list_users() -> list[AdminUserSummary]:
    return [_admin_user_model(r) for r in db.list_users()]


@admin_router.post("/users/{user_id}/role", response_model=AdminUserSummary)
def admin_set_role(
    user_id: int, body: SetRoleRequest, admin: dict[str, Any] = Depends(require_admin)
) -> AdminUserSummary:
    if body.role not in (config.ROLE_ADMIN, config.ROLE_USER):
        raise HTTPException(400, f"알 수 없는 역할입니다: {body.role}")
    # 마지막 남은 관리자를 강등시키면 아무도 이 화면에 못 들어와서 되돌릴 수도
    # 없는 상태가 된다 -- 그 경우만 막는다.
    if (
        body.role == config.ROLE_USER
        and int(user_id) == int(admin["id"])
        and db.count_admins(exclude_user_id=user_id) == 0
    ):
        raise HTTPException(409, "마지막 남은 관리자는 스스로를 강등시킬 수 없습니다.")
    db.set_user_role(user_id, body.role)
    users = {u["id"]: u for u in db.list_users()}
    if user_id not in users:
        raise HTTPException(404, f"사용자 #{user_id} 을(를) 찾을 수 없습니다.")
    return _admin_user_model(users[user_id])


@admin_router.post("/users/{user_id}/reset-password", response_model=dict)
def admin_reset_password(user_id: int, body: AdminResetPasswordRequest) -> dict[str, Any]:
    if len(body.new_password) < 8:
        raise HTTPException(400, "새 비밀번호는 8자 이상이어야 합니다.")
    users = {u["id"]: u for u in db.list_users()}
    if user_id not in users:
        raise HTTPException(404, f"사용자 #{user_id} 을(를) 찾을 수 없습니다.")
    password_hash, salt = auth.hash_password(body.new_password)
    db.admin_reset_password(user_id, password_hash, salt)
    return {"ok": True}


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


@protected.get("/documents", response_model=list[DocumentSummary])
def list_documents(
    status: Optional[str] = Query(default=None, description="ERROR/PENDING/VALIDATED/FAILED"),
) -> list[DocumentSummary]:
    if status and status not in config.STATUS_LABELS:
        raise HTTPException(400, f"알 수 없는 상태입니다: {status}")
    rows = db.list_documents(status)
    counts = db.error_counts()
    return [_summary(r, counts.get(r["id"], 0)) for r in rows]


@protected.get("/documents/counts")
def status_counts() -> dict[str, int]:
    return db.status_counts()


def _load(doc_id: int) -> dict[str, Any]:
    row = db.get_document(doc_id)
    if not row:
        raise HTTPException(404, f"문서 #{doc_id} 을(를) 찾을 수 없습니다.")
    return row


@protected.get("/documents/{doc_id}", response_model=DocumentDetail)
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


@protected.get("/documents/{doc_id}/markdown", response_model=dict)
def get_markdown(doc_id: int) -> dict[str, Any]:
    """Docling 추출 원문. 검수 화면에서 원문과 대조할 때 쓴다."""
    row = _load(doc_id)
    return {"document_id": doc_id, "markdown": row.get("markdown") or ""}


@protected.get("/documents/{doc_id}/docling-json", response_model=dict)
def get_docling_json(doc_id: int) -> dict[str, Any]:
    """Docling 원시 출력. 좌표(prov.bbox)가 들어 있어 복원 문제를 볼 때 쓴다.

    DB에는 문자열로 저장되어 있으므로 풀어서 돌려준다. 수 MB가 되기도 해서
    문서 목록에는 싣지 않고 이 경로로만 낸다.
    """
    row = _load(doc_id)
    raw = row.get("docling_json")
    if not raw:
        return {"document_id": doc_id, "docling_json": {}}
    try:
        import json as _json

        return {"document_id": doc_id, "docling_json": _json.loads(raw)}
    except ValueError as exc:
        raise HTTPException(500, f"Docling JSON 을 해석하지 못했습니다: {exc}")


# --------------------------------------------------------------------------
# 처리 (비동기, RabbitMQ 큐)
#
# 예전엔 이 프로세스의 메모리 딕셔너리에 작업 상태를 두고 FastAPI BackgroundTasks
# 로 같은 프로세스 안에서 처리했다. 그러면 엔진을 재시작할 때마다 처리 중이던
# 작업이 그냥 사라지고, 처리량을 늘리려면 엔진 자체를 늘려야 했다(문서 1건에
# 수 분씩 걸리는데 엔진은 API 도 같이 받는 프로세스라 좋은 방법이 아니다).
#
# 지금은 api.py 가 dbo.jobs 에 작업을 기록하고 RabbitMQ 에 발행만 한다. 실제
# 처리는 별도 프로세스(worker.py, 여러 대로 늘릴 수 있다)가 큐를 소비하며
# 한다. api.py 와 worker.py 는 dbo.jobs 를 통해서만 상태를 주고받는다.
# --------------------------------------------------------------------------

def _job_model(row: dict[str, Any]) -> JobStatus:
    return JobStatus(
        job_id=row["job_id"],
        state=row["state"],
        filename=row["filename"],
        document_id=row.get("document_id"),
        document_status=row.get("document_status"),
        error_count=int(row.get("error_count") or 0),
        skipped=bool(row.get("skipped")),
        message=row.get("message") or "",
        started_at=row["started_at"],
        finished_at=row.get("finished_at") or None,
    )


@protected.post("/documents", response_model=JobStatus, status_code=202)
async def upload_document(
    file: UploadFile = File(...),
    skip_duplicates: bool = Query(default=True),
) -> JobStatus:
    """파일을 접수해 dbo.jobs 에 기록하고, RabbitMQ 에 처리 작업을 발행한다.

    실제 처리는 별도로 떠 있는 worker.py 가 큐에서 이 작업을 받아서 한다 --
    이 함수는 발행까지만 하고 바로 돌아간다.
    """
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

    job_id = uuid.uuid4().hex
    db.create_job(job_id, filename)
    try:
        mq.publish_job(job_id, filename, data, skip_duplicates)
    except Exception as exc:
        # 큐에 못 실었으면 아무도 이 작업을 처리하지 않는다. 202로 접수됐다고
        # 해놓고 영영 안 끝나는 작업을 만들 수는 없으니, job 을 바로 실패로
        # 닫고 업로드 자체를 오류로 돌려준다 -- RabbitMQ가 안 떠 있을 때 이렇게 된다.
        db.update_job(
            job_id, state="FAILED",
            message=f"작업 큐에 올리지 못했습니다: {exc}", finished_at=_now(),
        )
        raise HTTPException(503, f"문서 처리 큐(RabbitMQ)에 연결하지 못했습니다: {exc}")

    return _job_model(db.get_job(job_id))


@protected.get("/jobs/{job_id}", response_model=JobStatus)
def get_job(job_id: str) -> JobStatus:
    row = db.get_job(job_id)
    if row is None:
        raise HTTPException(404, f"작업 {job_id} 을(를) 찾을 수 없습니다.")
    return _job_model(row)


@protected.get("/jobs", response_model=list[JobStatus])
def list_jobs() -> list[JobStatus]:
    return [_job_model(r) for r in db.list_jobs()]


# --------------------------------------------------------------------------
# 검수
# --------------------------------------------------------------------------

@protected.post("/documents/{doc_id}/recheck", response_model=RecheckResponse)
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


@protected.post("/documents/{doc_id}/approve", response_model=DocumentDetail)
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


class BulkApproveRequest(BaseModel):
    document_ids: list[int]


@protected.post("/documents/bulk-approve", response_model=dict)
def bulk_approve(body: BulkApproveRequest) -> dict[str, Any]:
    """검증을 통과해 고칠 것이 없는 건들을 한 번에 마감한다.

    필드는 손대지 않고 상태만 넘긴다. 값을 고쳐야 하는 건(ERROR)은 여기로 오면
    안 되므로, 넘어온 것 중 PENDING 인 것만 추린다 -- 목록이 갱신되기 전에 눌러
    ERROR 건이 섞여 들어오는 것을 막는다.
    """
    pending = {
        r["id"] for r in db.list_documents(config.STATUS_PENDING)
    }
    targets = [doc_id for doc_id in body.document_ids if doc_id in pending]
    skipped = [doc_id for doc_id in body.document_ids if doc_id not in pending]
    return {"approved": db.bulk_approve(targets), "skipped": skipped}


@protected.delete("/documents/{doc_id}", response_model=dict)
def delete_document(doc_id: int) -> dict[str, Any]:
    """DB 행과 업로드 원본 파일을 함께 지운다. 되돌릴 수 없다."""
    _load(doc_id)
    deleted = pipeline.delete_documents([doc_id])
    return {"deleted": deleted, "document_id": doc_id}


class BulkDeleteRequest(BaseModel):
    document_ids: list[int]


@protected.post("/documents/bulk-delete", response_model=dict)
def bulk_delete(body: BulkDeleteRequest) -> dict[str, Any]:
    """여러 건을 한 번에 지운다. 되돌릴 수 없다.

    한 건씩 DELETE 를 반복하면 중간에 끊겼을 때 어디까지 지워졌는지 알기 어렵다.
    파이프라인이 이미 목록을 받으므로 한 번에 넘긴다.
    """
    if not body.document_ids:
        return {"deleted": 0, "document_ids": []}
    return {
        "deleted": pipeline.delete_documents(body.document_ids),
        "document_ids": body.document_ids,
    }


# --------------------------------------------------------------------------
# 화면에서 MCP 도구 쓰기
#
# 사용자는 한국말로 묻고, LLM 이 어떤 도구를 부를지 골라 실행한 뒤 답을 만든다.
# 실제 루프는 chat_bridge 에 있다.
# --------------------------------------------------------------------------

class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    question: str
    # 대화 기록은 화면이 들고 보낸다. 서버는 상태를 갖지 않는다.
    history: list[ChatTurn] = Field(default_factory=list)


class ChatToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    result: str = ""


class ChatResponse(BaseModel):
    answer: str
    tool_calls: list[ChatToolCall] = Field(default_factory=list)
    rounds: int = 0


@protected.get("/chat/tools", response_model=dict)
def chat_tools() -> dict[str, Any]:
    """채팅이 쓸 수 있는 MCP 도구 목록. 연결 상태 확인에도 쓴다."""
    return {
        "connected": chat_bridge.bridge.session is not None,
        "model": config.OLLAMA_MODEL,
        "tools": [
            {
                "name": t["function"]["name"],
                "description": t["function"]["description"].splitlines()[0],
            }
            for t in chat_bridge.bridge.tools
        ],
    }


@protected.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest) -> ChatResponse:
    """질문 하나에 답한다. 어떤 도구를 거쳤는지도 함께 돌려준다."""
    if not body.question.strip():
        raise HTTPException(400, "질문이 비어 있습니다.")
    if chat_bridge.bridge.session is None:
        raise HTTPException(503, "MCP 서버에 연결되어 있지 않습니다. 엔진을 다시 시작하세요.")

    result = await chat_bridge.answer(
        body.question.strip(),
        [turn.model_dump() for turn in body.history],
    )
    return ChatResponse(**result)


# --------------------------------------------------------------------------
# 오류 신고
#
# 신고는 DB가 아니라 파일로 남는다(app.report). 신고할 고장 중에 DB 연결 실패도
# 있어서, 저장에 DB가 필요하면 정작 그 고장을 신고할 수 없기 때문이다.
# --------------------------------------------------------------------------

class ReportSummary(BaseModel):
    slug: str
    number: int
    status: str
    created_at: str
    section: str
    document_id: Optional[int] = None
    message: str
    images: list[str] = Field(default_factory=list)
    exception: Optional[str] = None


def _report_model(record: dict[str, Any]) -> ReportSummary:
    context = record.get("context") or {}
    return ReportSummary(
        slug=record.get("slug") or record["path"].name,
        number=int(record.get("number") or 0),
        status=record.get("status") or report.STATUS_OPEN,
        created_at=record.get("created_at") or "",
        section=record.get("section") or "-",
        document_id=record.get("document_id"),
        message=record.get("message") or "",
        images=list(record.get("images") or []),
        exception=context.get("exception"),
    )


@protected.get("/reports", response_model=list[ReportSummary])
def list_reports(
    scope: Literal["open", "all"] = Query(default="all"),
) -> list[ReportSummary]:
    records = report.load_all()
    if scope == "open":
        records = [r for r in records if r.get("status") == report.STATUS_OPEN]
    return [_report_model(r) for r in records]


@protected.get("/reports/counts")
def report_counts() -> dict[str, int]:
    records = report.load_all()
    open_count = sum(1 for r in records if r.get("status") == report.STATUS_OPEN)
    return {"open": open_count, "total": len(records)}


class _UploadShim:
    """report.create 가 기대하는 모양(.name/.getvalue())으로 감싼다."""

    def __init__(self, name: str, data: bytes) -> None:
        self.name = name
        self._data = data

    def getvalue(self) -> bytes:
        return self._data


@protected.post("/reports", response_model=ReportSummary, status_code=201)
async def create_report(
    message: str = Form(...),
    section: str = Form(default="일반"),
    document_id: Optional[int] = Form(default=None),
    attach_context: bool = Form(default=True),
    # 브라우저에서 붙여넣은 캡처는 data URL 로 온다. 파일로 고른 것은 multipart.
    pasted: list[str] = Form(default_factory=list),
    files: list[UploadFile] = File(default_factory=list),
) -> ReportSummary:
    if not message.strip():
        raise HTTPException(400, "증상을 한 줄이라도 적어야 합니다.")

    context = report.collect_context(document_id if attach_context else None)
    uploads = [
        _UploadShim(f.filename or "screenshot.png", await f.read()) for f in files
    ]
    folder = report.create(
        message=message.strip(),
        section=section,
        doc_id=document_id,
        pasted_images=pasted,
        uploaded_files=uploads,
        context=context,
    )
    for record in report.load_all():
        if record["path"] == folder:
            return _report_model(record)
    raise HTTPException(500, "신고를 저장했지만 다시 읽지 못했습니다.")


def _report_folder(slug: str):
    """slug 로 신고 폴더를 찾는다. data/reports 밖으로 나가는 경로는 막는다."""
    root = config.REPORTS_DIR.resolve()
    folder = (config.REPORTS_DIR / slug).resolve()
    if folder.parent != root or not folder.is_dir():
        raise HTTPException(404, f"신고 {slug} 을(를) 찾을 수 없습니다.")
    return folder


@protected.get("/reports/{slug}/images/{name}")
def report_image(slug: str, name: str) -> FileResponse:
    folder = _report_folder(slug)
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        raise HTTPException(404, "첨부 파일을 찾을 수 없습니다.")
    return FileResponse(path)


@protected.post("/reports/{slug}/status", response_model=dict)
def set_report_status(slug: str, status: Literal["OPEN", "RESOLVED"]) -> dict[str, Any]:
    _report_folder(slug)
    if not report.set_status(slug, status):
        raise HTTPException(404, f"신고 {slug} 을(를) 찾을 수 없습니다.")
    return {"slug": slug, "status": status}


@protected.delete("/reports/{slug}", response_model=dict)
def delete_report(slug: str) -> dict[str, Any]:
    _report_folder(slug)
    return {"deleted": report.delete([slug]), "slug": slug}


# 여기까지 등록된 /documents, /jobs, /chat*, /reports* 전부를 한 번에 로그인
# 필수로 묶는다. /health, /auth/* 는 위에서 이미 app 에 직접 등록했으므로
# 이 라우터를 안 거치고 그대로 공개로 남는다.
app.include_router(protected)
# /admin/* 은 로그인은 물론 role='admin' 까지 요구한다(require_admin).
app.include_router(admin_router)
