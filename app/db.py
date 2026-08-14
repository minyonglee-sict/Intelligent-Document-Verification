"""MS SQL Server 저장 계층.

Streamlit은 요청마다 스크립트를 재실행하고 여러 스레드에서 동작하므로,
커넥션을 전역으로 재사용하지 않고 호출 단위로 열고 닫는다.

SQLite에서 옮겨오며 달라진 것들:
  - AUTOINCREMENT -> IDENTITY(1,1), 새 id 는 OUTPUT INSERTED.id 로 받는다
  - LIMIT n       -> TOP n
  - PRAGMA/WAL    -> 없음. 외래키는 SQL Server가 기본 강제한다
  - BEGIN IMMEDIATE -> 조회에 WITH (UPDLOCK, HOLDLOCK) 을 걸어 같은 효과를 낸다
파라미터 표시자는 양쪽 다 '?' 라 쿼리 본문은 대부분 그대로 쓴다.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator, Optional

import pyodbc

from . import config
from .schemas import InvoiceFields, InvoiceHeader, LineItem, ValidationIssue

# 송장 머리말 필드는 JSON 덩어리가 아니라 실제 컬럼으로 저장한다.
# 컬럼이어야 SQL로 바로 조회·집계할 수 있고, 화면에서도 있는 그대로 보인다.
HEADER_COLUMNS: list[tuple[str, str]] = [
    ("doc_type", "NVARCHAR(20)"),
    ("invoice_number", "NVARCHAR(100)"),
    ("issue_date", "NVARCHAR(32)"),
    ("due_date", "NVARCHAR(32)"),
    ("vendor_name", "NVARCHAR(300)"),
    ("buyer_name", "NVARCHAR(300)"),
    ("po_number", "NVARCHAR(100)"),
    ("currency", "NVARCHAR(16)"),
    ("subtotal", "FLOAT"),
    ("tax", "FLOAT"),
    ("shipping", "FLOAT"),
    ("total_amount", "FLOAT"),
]
HEADER_FIELDS = [name for name, _ in HEADER_COLUMNS]

# 스키마에 필드를 추가하고 여기에 컬럼 추가를 잊으면 그 값이 조용히 저장되지 않는다.
_schema_only = set(InvoiceHeader.model_fields) - set(HEADER_FIELDS)
_table_only = set(HEADER_FIELDS) - set(InvoiceHeader.model_fields)
if _schema_only or _table_only:
    raise RuntimeError(
        f"InvoiceHeader 와 documents 컬럼이 어긋납니다. "
        f"스키마에만: {_schema_only or '-'} / 테이블에만: {_table_only or '-'}"
    )

_HEADER_DDL = ",\n        ".join(f"{name} {kind}" for name, kind in HEADER_COLUMNS)

SCHEMA_STATEMENTS = [
    f"""
    IF OBJECT_ID('dbo.documents', 'U') IS NULL
    CREATE TABLE dbo.documents (
        id              INT IDENTITY(1,1) PRIMARY KEY,
        filename        NVARCHAR(400)  NOT NULL,
        file_hash       NVARCHAR(64),
        stored_path     NVARCHAR(1000),
        status          NVARCHAR(20)   NOT NULL,
        is_valid        BIT,
        page_count      INT            DEFAULT 0,
        markdown        NVARCHAR(MAX),
        docling_json    NVARCHAR(MAX),
        {_HEADER_DDL},
        model           NVARCHAR(100),
        failure_reason  NVARCHAR(MAX),
        reviewer_note   NVARCHAR(MAX),
        created_at      NVARCHAR(32)   NOT NULL,
        updated_at      NVARCHAR(32)   NOT NULL,
        validated_at    NVARCHAR(32)
    )
    """,
    """
    IF OBJECT_ID('dbo.line_items', 'U') IS NULL
    CREATE TABLE dbo.line_items (
        id          INT IDENTITY(1,1) PRIMARY KEY,
        document_id INT NOT NULL
                    REFERENCES dbo.documents(id) ON DELETE CASCADE,
        position    INT NOT NULL,
        description NVARCHAR(MAX),
        quantity    FLOAT,
        unit_price  FLOAT,
        amount      FLOAT,
        tax         FLOAT
    )
    """,
    """
    IF OBJECT_ID('dbo.validation_errors', 'U') IS NULL
    CREATE TABLE dbo.validation_errors (
        id          INT IDENTITY(1,1) PRIMARY KEY,
        document_id INT NOT NULL
                    REFERENCES dbo.documents(id) ON DELETE CASCADE,
        field       NVARCHAR(100),
        message     NVARCHAR(MAX)  NOT NULL,
        severity    NVARCHAR(20)   NOT NULL DEFAULT 'critical',
        source      NVARCHAR(20)   NOT NULL DEFAULT 'llm',
        resolved    BIT            NOT NULL DEFAULT 0,
        created_at  NVARCHAR(32)   NOT NULL
    )
    """,
    "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_documents_status')"
    " CREATE INDEX idx_documents_status ON dbo.documents(status)",
    "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_documents_hash')"
    " CREATE INDEX idx_documents_hash ON dbo.documents(file_hash)",
    "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_errors_document')"
    " CREATE INDEX idx_errors_document ON dbo.validation_errors(document_id)",
    "IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='idx_line_items_document')"
    " CREATE INDEX idx_line_items_document ON dbo.line_items(document_id, position)",
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# --------------------------------------------------------------------------
# 연결
# --------------------------------------------------------------------------

@contextmanager
def connect(*, autocommit: bool = False) -> Iterator[pyodbc.Connection]:
    conn = pyodbc.connect(
        config.connection_string(), timeout=config.MSSQL_TIMEOUT, autocommit=autocommit
    )
    try:
        yield conn
        if not autocommit:
            conn.commit()
    except Exception:
        if not autocommit:
            conn.rollback()
        raise
    finally:
        conn.close()


def _rows(cursor) -> list[dict[str, Any]]:
    """pyodbc Row 를 dict 로 바꾼다. 호출부가 SQLite 때와 같은 모양을 쓰도록."""
    columns = [c[0] for c in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _one(cursor) -> Optional[dict[str, Any]]:
    rows = _rows(cursor)
    return rows[0] if rows else None


def health_check() -> tuple[bool, str]:
    """DB 연결 확인. 사이드바에서 쓴다."""
    try:
        with connect(autocommit=True) as conn:
            row = conn.execute(
                "SELECT @@VERSION, DB_NAME(), SUSER_NAME()"
            ).fetchone()
    except Exception as exc:
        return False, f"MS-SQL 연결 실패 ({config.MSSQL_SERVER}): {exc}"
    version = row[0].splitlines()[0].strip()
    return True, f"{row[1]} @ {config.MSSQL_SERVER} ({row[2]})\n{version}"


def init_db() -> None:
    """DB가 없으면 만들고, 테이블·인덱스를 보장한다."""
    with pyodbc.connect(
        config.connection_string("master"), timeout=config.MSSQL_TIMEOUT, autocommit=True
    ) as conn:
        exists = conn.execute(
            "SELECT 1 FROM sys.databases WHERE name = ?", config.MSSQL_DATABASE
        ).fetchone()
        if not exists:
            # CREATE DATABASE 는 배치 단독으로만 실행할 수 있어 파라미터를 못 쓴다.
            name = config.MSSQL_DATABASE.replace("]", "]]")
            conn.execute(f"CREATE DATABASE [{name}]")

    with connect(autocommit=True) as conn:
        for statement in SCHEMA_STATEMENTS:
            conn.execute(statement)
        _add_missing_columns(conn)
        _ensure_sequential_ids(conn)


def _add_missing_columns(conn: pyodbc.Connection) -> None:
    """이미 만들어진 테이블에 나중에 늘어난 컬럼을 채워 넣는다."""
    wanted = {
        "documents": HEADER_COLUMNS,
        "line_items": [("tax", "FLOAT")],
    }
    for table, columns in wanted.items():
        existing = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sys.columns WHERE object_id = OBJECT_ID(?)",
                f"dbo.{table}",
            )
        }
        for name, kind in columns:
            if name not in existing:
                conn.execute(f"ALTER TABLE dbo.{table} ADD {name} {kind}")


# init_db()는 Streamlit이 스크립트를 다시 실행할 때마다 불린다. 아래 손질은
# 프로세스당 한 번이면 충분하고, 매 rerun 마다 걸면 낭비인 데다 RESEED가 다른
# 세션의 INSERT와 겹칠 위험이 있다.
_identity_checked = False


def _ensure_sequential_ids(conn: pyodbc.Connection) -> None:
    """id 가 1씩 이어지도록 IDENTITY 를 손본다.

    SQL Server 는 INT IDENTITY 의 다음 번호를 1000개씩 미리 잡아 두고, 서비스가
    재시작되면 쓰지 않은 캐시를 그냥 버린다. 그래서 재시작만 해도 번호가 1000씩
    건너뛴다 (이 DB에서 실제로 15 -> 1018 이 되었다).

      - IDENTITY_CACHE 를 꺼서 앞으로의 점프를 막고,
      - 이미 앞서 나간 카운터는 MAX(id) 로 되돌려 회수한다.

    되돌리기는 카운터가 실제 행보다 앞서 있을 때만 한다. 그 번호를 이미 쓴 행이
    있는데 되돌리면 다음 INSERT 가 기본키 충돌로 실패한다.
    """
    global _identity_checked
    if _identity_checked:
        return
    _identity_checked = True

    cached = conn.execute(
        "SELECT value FROM sys.database_scoped_configurations WHERE name='IDENTITY_CACHE'"
    ).fetchone()
    if cached and int(cached[0]):
        # SQL Server 2017+ 의 DB 단위 설정. 현재 연결된 DB에 적용된다.
        conn.execute("ALTER DATABASE SCOPED CONFIGURATION SET IDENTITY_CACHE = OFF")

    highest, current, row_count = conn.execute(
        "SELECT ISNULL(MAX(id), 0), IDENT_CURRENT('dbo.documents'), COUNT(*)"
        " FROM dbo.documents"
    ).fetchone()

    # 빈 테이블에서는 회수할 것이 없다. 그런데도 RESEED 를 걸면 안 된다 --
    # 한 번도 INSERT 되지 않은 테이블은 RESEED 값을 '다음 값' 으로 쓰기 때문에
    # (기존 테이블은 값+1 을 쓴다) 첫 문서가 id 0 을 받는다. 새로 만든 DB에서
    # 실제로 그렇게 되어, 화면에 '#0' 문서가 생겼다.
    if int(row_count) == 0:
        return

    if int(current) > int(highest):
        conn.execute(
            f"DBCC CHECKIDENT ('dbo.documents', RESEED, {int(highest)}) WITH NO_INFOMSGS"
        )


# --------------------------------------------------------------------------
# 쓰기
# --------------------------------------------------------------------------

def _write_fields(conn: pyodbc.Connection, doc_id: int, fields: InvoiceFields) -> None:
    """머리말은 documents 컬럼에, 품목은 line_items 행으로 저장한다."""
    assignments = ", ".join(f"{name}=?" for name in HEADER_FIELDS)
    conn.execute(
        f"UPDATE dbo.documents SET {assignments} WHERE id=?",
        *(getattr(fields, name) for name in HEADER_FIELDS),
        doc_id,
    )
    conn.execute("DELETE FROM dbo.line_items WHERE document_id=?", doc_id)
    if fields.line_items:
        # position 은 문서에 적힌 품목 번호다. 원문과 나란히 대조하려면 저장 순서로
        # 새로 매기면 안 된다 (실제로 번호가 66 다음 76으로 뛰는 송장이 있다).
        # 다만 한 행이라도 번호가 없으면 섞어 쓰지 않고 전부 순번으로 돌린다.
        # 검수 화면에서 행을 추가하면 그 행만 번호가 없어 중복이 생기기 때문이다.
        numbers = [i.position for i in fields.line_items]
        if any(n is None for n in numbers):
            numbers = list(range(1, len(fields.line_items) + 1))

        cursor = conn.cursor()
        cursor.fast_executemany = True
        cursor.executemany(
            "INSERT INTO dbo.line_items (document_id, position, description,"
            " quantity, unit_price, amount, tax) VALUES (?,?,?,?,?,?,?)",
            [
                (doc_id, pos, i.description, i.quantity, i.unit_price, i.amount, i.tax)
                for pos, i in zip(numbers, fields.line_items)
            ],
        )


def reserve_document(
    *,
    filename: str,
    file_hash: str,
    stored_path: str,
    skip_duplicates: bool,
) -> tuple[dict[str, Any], bool]:
    """처리 시작 시점에 PROCESSING 행을 만들어 해시를 선점한다.

    (행, 새로 만들었는지) 를 돌려준다. 이미 같은 해시가 등록되어 있으면
    (기존 행, False). UPDLOCK/HOLDLOCK 으로 조회 구간에 잠금을 걸어, 동시에
    시작한 두 실행 중 하나만 새 행을 얻는다.
    """
    ts = _now()
    with connect() as conn:
        if skip_duplicates:
            found = _one(
                conn.execute(
                    "SELECT TOP 1 id, filename, status FROM dbo.documents"
                    " WITH (UPDLOCK, HOLDLOCK)"
                    " WHERE file_hash=? AND status<>? ORDER BY id DESC",
                    file_hash,
                    config.STATUS_FAILED,
                )
            )
            if found:
                return found, False

        row = _one(
            conn.execute(
                "INSERT INTO dbo.documents (filename, file_hash, stored_path, status,"
                " created_at, updated_at)"
                " OUTPUT INSERTED.id VALUES (?,?,?,?,?,?)",
                filename,
                file_hash,
                stored_path,
                config.STATUS_PROCESSING,
                ts,
                ts,
            )
        )
        doc_id = int(row["id"])
    return {"id": doc_id, "filename": filename, "status": config.STATUS_PROCESSING}, True


def finalize_document(
    doc_id: int,
    *,
    status: str,
    is_valid: Optional[bool],
    markdown: str,
    docling_json: dict,
    fields: Optional[InvoiceFields],
    errors: list[ValidationIssue],
    model: str,
    page_count: int = 0,
    failure_reason: Optional[str] = None,
) -> None:
    """reserve_document로 잡아둔 행을 처리 결과로 채운다."""
    ts = _now()
    with connect() as conn:
        conn.execute(
            "UPDATE dbo.documents"
            "   SET status=?, is_valid=?, page_count=?, markdown=?, docling_json=?,"
            "       model=?, failure_reason=?, updated_at=?"
            " WHERE id=?",
            status,
            None if is_valid is None else int(is_valid),
            page_count,
            markdown,
            json.dumps(docling_json, ensure_ascii=False),
            model,
            failure_reason,
            ts,
            doc_id,
        )
        if fields is not None:
            _write_fields(conn, doc_id, fields)
        conn.execute("DELETE FROM dbo.validation_errors WHERE document_id=?", doc_id)
        _insert_errors(conn, doc_id, errors, ts)


def cleanup_stale_processing() -> int:
    """중단된 PROCESSING 행을 FAILED로 정리한다.

    앱이 처리 중에 죽으면 행이 PROCESSING으로 남아 같은 파일의 재업로드를
    영원히 막는다. 앱 시작 시 한 번 훑어 풀어준다.
    """
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=config.STALE_PROCESSING_MINUTES)
    ).isoformat(timespec="seconds")
    with connect() as conn:
        cursor = conn.execute(
            "UPDATE dbo.documents"
            "   SET status=?, failure_reason=?, updated_at=?"
            " WHERE status=? AND created_at<?",
            config.STATUS_FAILED,
            "처리 도중 중단되었습니다 (앱 재시작 시 정리됨).",
            _now(),
            config.STATUS_PROCESSING,
            cutoff,
        )
        return max(cursor.rowcount, 0)


def _insert_errors(
    conn: pyodbc.Connection, doc_id: int, errors: list[ValidationIssue], ts: str
) -> None:
    if not errors:
        return
    conn.cursor().executemany(
        "INSERT INTO dbo.validation_errors (document_id, field, message, severity,"
        " source, created_at) VALUES (?,?,?,?,?,?)",
        [(doc_id, e.field, e.message, e.severity, e.source, ts) for e in errors],
    )


def update_fields(doc_id: int, fields: InvoiceFields, note: Optional[str] = None) -> None:
    """검수자가 화면에서 수정한 필드를 반영한다."""
    with connect() as conn:
        _write_fields(conn, doc_id, fields)
        conn.execute(
            "UPDATE dbo.documents SET reviewer_note=?, updated_at=? WHERE id=?",
            note,
            _now(),
            doc_id,
        )


def replace_errors(doc_id: int, errors: list[ValidationIssue]) -> None:
    """재검증 결과로 에러 로그를 교체한다."""
    with connect() as conn:
        conn.execute("DELETE FROM dbo.validation_errors WHERE document_id=?", doc_id)
        _insert_errors(conn, doc_id, errors, _now())


def approve(doc_id: int, fields: InvoiceFields, note: Optional[str] = None) -> None:
    """검수 완료: 수정된 필드를 저장하고 상태를 VALIDATED로 전환, 에러는 해소 처리."""
    ts = _now()
    with connect() as conn:
        _write_fields(conn, doc_id, fields)
        conn.execute(
            "UPDATE dbo.documents"
            "   SET reviewer_note=?, status=?, is_valid=1, validated_at=?, updated_at=?"
            " WHERE id=?",
            note,
            config.STATUS_VALIDATED,
            ts,
            ts,
            doc_id,
        )
        conn.execute(
            "UPDATE dbo.validation_errors SET resolved=1 WHERE document_id=?", doc_id
        )


def bulk_approve(doc_ids: list[int]) -> int:
    """필드는 손대지 않고 상태만 VALIDATED로 넘긴다.

    검증을 통과해 고칠 것이 없는 건들을 한 번에 마감할 때 쓴다.
    """
    if not doc_ids:
        return 0
    ts = _now()
    placeholders = ",".join("?" * len(doc_ids))
    with connect() as conn:
        cursor = conn.execute(
            f"UPDATE dbo.documents"
            f"   SET status=?, is_valid=1, validated_at=?, updated_at=?"
            f" WHERE id IN ({placeholders})",
            config.STATUS_VALIDATED,
            ts,
            ts,
            *doc_ids,
        )
        affected = max(cursor.rowcount, 0)
        conn.execute(
            f"UPDATE dbo.validation_errors SET resolved=1"
            f" WHERE document_id IN ({placeholders})",
            *doc_ids,
        )
    return affected


def delete_documents(doc_ids: list[int]) -> int:
    """문서들을 자식 행과 함께 지운다. 실제로 지워진 건수를 돌려준다.

    외래키에 ON DELETE CASCADE 가 걸려 있지만, 제약 없이 만들어진 DB 도 있으므로
    자식부터 명시적으로 지운다.

    DB 행만 지운다. 화면에서 부를 것은 업로드 원본까지 함께 치우는
    pipeline.delete_documents 다.
    """
    if not doc_ids:
        return 0
    placeholders = ",".join("?" * len(doc_ids))
    with connect() as conn:
        conn.execute(
            f"DELETE FROM dbo.validation_errors WHERE document_id IN ({placeholders})",
            *doc_ids,
        )
        conn.execute(
            f"DELETE FROM dbo.line_items WHERE document_id IN ({placeholders})",
            *doc_ids,
        )
        cursor = conn.execute(
            f"DELETE FROM dbo.documents WHERE id IN ({placeholders})", *doc_ids
        )
        return max(cursor.rowcount, 0)


# --------------------------------------------------------------------------
# 읽기
# --------------------------------------------------------------------------

def list_documents(status: Optional[str] = None) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, filename, status, is_valid, page_count, model, failure_reason,"
        "       created_at, updated_at, validated_at"
        "  FROM dbo.documents"
    )
    params: tuple = ()
    if status:
        sql += " WHERE status=?"
        params = (status,)
    sql += " ORDER BY id DESC"
    with connect(autocommit=True) as conn:
        return _rows(conn.execute(sql, *params))


def stored_paths(doc_ids: list[int]) -> list[str]:
    """문서들의 업로드 원본 경로. 경로가 비어 있는 행은 빠진다."""
    if not doc_ids:
        return []
    placeholders = ",".join("?" * len(doc_ids))
    with connect(autocommit=True) as conn:
        rows = _rows(
            conn.execute(
                f"SELECT stored_path FROM dbo.documents WHERE id IN ({placeholders})",
                *doc_ids,
            )
        )
    return [r["stored_path"] for r in rows if r["stored_path"]]


def get_document(doc_id: int) -> Optional[dict[str, Any]]:
    with connect(autocommit=True) as conn:
        return _one(conn.execute("SELECT * FROM dbo.documents WHERE id=?", doc_id))


def get_errors(doc_id: int, *, only_open: bool = True) -> list[dict[str, Any]]:
    sql = "SELECT * FROM dbo.validation_errors WHERE document_id=?"
    if only_open:
        sql += " AND resolved=0"
    sql += " ORDER BY CASE severity WHEN 'critical' THEN 0 ELSE 1 END, id"
    with connect(autocommit=True) as conn:
        return _rows(conn.execute(sql, doc_id))


def error_counts() -> dict[int, int]:
    """document_id -> 미해결 에러 개수."""
    with connect(autocommit=True) as conn:
        rows = _rows(
            conn.execute(
                "SELECT document_id, COUNT(*) AS n FROM dbo.validation_errors"
                " WHERE resolved=0 GROUP BY document_id"
            )
        )
    return {int(r["document_id"]): int(r["n"]) for r in rows}


def status_counts() -> dict[str, int]:
    with connect(autocommit=True) as conn:
        rows = _rows(
            conn.execute(
                "SELECT status, COUNT(*) AS n FROM dbo.documents GROUP BY status"
            )
        )
    return {r["status"]: int(r["n"]) for r in rows}


def get_line_items(doc_id: int) -> list[dict[str, Any]]:
    with connect(autocommit=True) as conn:
        return _rows(
            conn.execute(
                "SELECT id, position, description, quantity, unit_price, amount, tax"
                "  FROM dbo.line_items WHERE document_id=? ORDER BY position",
                doc_id,
            )
        )


def load_fields(row: dict[str, Any]) -> InvoiceFields:
    """documents 컬럼 + line_items 행을 InvoiceFields 로 되살린다."""
    header = {name: row.get(name) for name in HEADER_FIELDS}
    header["doc_type"] = header.get("doc_type") or "UNKNOWN"
    items = [
        LineItem(
            position=i["position"],
            description=i["description"] or "",
            quantity=i["quantity"],
            unit_price=i["unit_price"],
            amount=i["amount"],
            tax=i.get("tax"),
        )
        for i in get_line_items(int(row["id"]))
    ]
    try:
        return InvoiceFields(**header, line_items=items)
    except Exception:
        return InvoiceFields()
