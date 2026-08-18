"""Intelligent Document Verification — MCP 서버.

실행:  python mcp_server.py          (stdio, MCP 클라이언트가 띄운다)

Streamlit(main.py)·HTTP API(api.py)와 나란히 서는 또 하나의 진입점이다. 업무 규칙을
새로 두지 않고 app/ 아래 로직을 호출하기만 한다 -- 규칙을 여러 벌로 만들면 화면과
엔진의 판정이 갈라지고, 그때부터는 어느 쪽이 옳은지 아무도 모른다.

무엇을 내고 무엇을 안 내는가

  낸다   조회와 진단. 문서 목록·상세·원문, 저장된 값으로 다시 파싱·검증해 보기,
         오류 신고 읽기. 전부 DB를 바꾸지 않는다.

  안 낸다  승인(approve) -- 이 시스템의 설계가 '최종 상태 전환은 사람의 승인으로만'
           이다. 도구로 내주면 자동화가 스스로 승인할 수 있게 되어 설계가 무너진다.
           삭제(delete) -- 문서 행과 업로드 원본을 함께 지운다. 되돌릴 수 없다.
           업로드 처리 -- 한 건에 수 분이 걸려 도구 호출로 기다릴 수 없다.
           필요하면 화면에서 한다.

  예외   신고를 '처리 완료'로 표시하는 것만 쓰기 동작으로 낸다. 되돌릴 수 있고,
         고친 뒤 닫는 것이 이 도구의 주 용도이기 때문이다.

stdout 은 MCP 프로토콜 통로다. 여기서는 절대 print 하지 않는다.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from mcp.server import MCPServer

from app import config, db, layout_recovery, report, table_parser, validator

mcp = MCPServer(
    name="idv",
    title="Intelligent Document Verification",
    instructions=(
        "송장·영수증 검증 시스템의 문서를 조회하고 진단합니다. "
        "승인과 삭제는 사람이 화면에서 합니다."
    ),
    version="1.0.0",
)

# 원문은 통째로 넘기면 응답이 수만 자가 된다. 기본 상한을 두고 필요하면 늘리게 한다.
_DEFAULT_MARKDOWN_CHARS = 6000


def _fail(exc: Exception) -> dict[str, Any]:
    """예외를 올리지 않고 사유를 돌려준다. 무엇이 막혔는지 보이는 편이 낫다."""
    return {"error": f"{type(exc).__name__}: {exc}"}


def _item(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": row.get("position"),
        "description": row.get("description"),
        "quantity": row.get("quantity"),
        "unit_price": row.get("unit_price"),
        "tax": row.get("tax"),
        "amount": row.get("amount"),
    }


# --------------------------------------------------------------------------
# 조회
# --------------------------------------------------------------------------

@mcp.tool()
def list_documents(status: Optional[str] = None, limit: int = 50) -> dict[str, Any]:
    """업로드된 송장·영수증 문서의 목록.

    '오류 난 문서', '검수할 문서', '승인 대기' 를 물으면 이 도구를 쓴다.
    사용자가 올린 버그 신고를 찾는 list_reports 와 혼동하지 말 것.

    Args:
        status: ERROR(검증 오류) / PENDING(승인 대기) / VALIDATED(승인 완료) /
                FAILED(처리 실패) / PROCESSING. 비우면 전체.
        limit: 최대 건수 (기본 50).
    """
    try:
        if status and status not in config.STATUS_LABELS:
            return {"error": f"알 수 없는 상태입니다: {status}"}
        rows = db.list_documents(status)
        counts = db.error_counts()
        return {
            "total": len(rows),
            "documents": [
                {
                    "id": r["id"],
                    "filename": r["filename"],
                    "status": r["status"],
                    "error_count": counts.get(r["id"], 0),
                    "page_count": r.get("page_count"),
                    "created_at": r.get("created_at"),
                }
                for r in rows[:limit]
            ],
        }
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def document_counts() -> dict[str, Any]:
    """상태별 문서 수. 지금 무엇이 밀려 있는지 한눈에 본다."""
    try:
        return {"database": config.MSSQL_DATABASE, "counts": db.status_counts()}
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def get_document(document_id: int, include_items: bool = True) -> dict[str, Any]:
    """문서 한 건의 저장된 값: 머리말 필드, 품목, 검증 오류.

    Args:
        document_id: 문서 ID.
        include_items: 품목 목록을 포함할지 (행이 많으면 응답이 길어진다).
    """
    try:
        row = db.get_document(document_id)
        if not row:
            return {"error": f"문서 #{document_id} 을(를) 찾을 수 없습니다."}

        fields = db.load_fields(row)
        errors = db.get_errors(document_id, only_open=False)
        out: dict[str, Any] = {
            "id": row["id"],
            "filename": row["filename"],
            "status": row["status"],
            "page_count": row.get("page_count"),
            "model": row.get("model"),
            "created_at": row.get("created_at"),
            "validated_at": row.get("validated_at") or None,
            "reviewer_note": row.get("reviewer_note") or None,
            "failure_reason": row.get("failure_reason") or None,
            "header": fields.model_dump(exclude={"line_items"}),
            "line_item_count": len(fields.line_items),
            "errors": [
                {
                    "field": e.get("field"),
                    "severity": e.get("severity"),
                    "source": e.get("source"),
                    "resolved": bool(e.get("resolved")),
                    "message": e.get("message"),
                }
                for e in errors
            ],
        }
        if include_items:
            out["line_items"] = [_item(i) for i in db.get_line_items(document_id)]
        return out
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def get_document_markdown(
    document_id: int, max_chars: int = _DEFAULT_MARKDOWN_CHARS, offset: int = 0
) -> dict[str, Any]:
    """Docling 이 추출한 원문(Markdown).

    값이 왜 그렇게 뽑혔는지 보려면 결국 원문을 봐야 한다. 길면 잘라서 돌려주므로
    offset 을 옮겨 가며 읽는다.
    """
    try:
        row = db.get_document(document_id)
        if not row:
            return {"error": f"문서 #{document_id} 을(를) 찾을 수 없습니다."}
        markdown = row.get("markdown") or ""
        chunk = markdown[offset : offset + max_chars]
        return {
            "document_id": document_id,
            "total_chars": len(markdown),
            "offset": offset,
            "returned_chars": len(chunk),
            "has_more": offset + len(chunk) < len(markdown),
            "markdown": chunk,
        }
    except Exception as exc:
        return _fail(exc)


# --------------------------------------------------------------------------
# 진단
# --------------------------------------------------------------------------

@mcp.tool()
def reparse_document(document_id: int) -> dict[str, Any]:
    """저장된 원문으로 파싱·검증을 지금 코드로 다시 돌려 본다. DB는 바꾸지 않는다.

    Docling 변환도 LLM 호출도 하지 않는다 -- markdown 과 docling_json 이 이미 DB에
    있으므로 표 파서·좌표 복원·규칙 검증만 다시 태운다. 그래서 몇 밀리초에 끝난다.

    파서나 검증 규칙을 고친 뒤 '이 문서가 지금은 어떻게 읽히는가' 를 확인하는 용도다.
    저장된 결과와 달라지면 differs_from_saved 로 알려준다 -- 다시 올려야 반영된다.
    """
    try:
        row = db.get_document(document_id)
        if not row:
            return {"error": f"문서 #{document_id} 을(를) 찾을 수 없습니다."}

        markdown = row.get("markdown") or ""
        if not markdown:
            return {"error": "저장된 원문이 없습니다. 처리에 실패한 문서일 수 있습니다."}

        parsed = table_parser.parse_line_items(markdown)
        merged, restored = layout_recovery.recover_missing_items(
            parsed, row.get("docling_json")
        )

        fields = db.load_fields(row)
        fields.line_items = merged
        issues = validator.rule_check(fields, markdown)

        checked = [
            i for i in merged
            if None not in (i.quantity, i.unit_price, i.amount)
        ]
        arithmetic_ok = sum(
            1 for i in checked
            if abs(i.quantity * i.unit_price - i.amount) <= config.AMOUNT_TOLERANCE
        )

        saved_items = db.get_line_items(document_id)
        return {
            "document_id": document_id,
            "filename": row["filename"],
            "saved": {
                "status": row["status"],
                "line_items": len(saved_items),
                "errors": len(db.get_errors(document_id, only_open=False)),
            },
            "reparsed": {
                "line_items_from_table": len(parsed),
                "line_items_recovered_by_layout": restored,
                "line_items_total": len(merged),
                "arithmetic_checked": len(checked),
                "arithmetic_ok": arithmetic_ok,
                "errors": [
                    {"field": i.field, "severity": i.severity, "message": i.message}
                    for i in issues
                ],
                "critical_count": sum(1 for i in issues if i.severity == "critical"),
            },
            "differs_from_saved": len(merged) != len(saved_items),
            "note": (
                "DB는 그대로다. 이 결과를 반영하려면 문서를 다시 업로드해야 한다."
            ),
        }
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def parse_table(markdown: str) -> dict[str, Any]:
    """마크다운 표를 품목으로 읽어 본다. 저장하지 않는다.

    표 파서만 따로 시험할 때 쓴다. 문제가 되는 표를 붙여 넣으면 어떤 열이 어떻게
    잡히는지 바로 확인할 수 있다.
    """
    try:
        items = table_parser.parse_line_items(markdown)
        return {
            "line_items": [
                {
                    "position": i.position,
                    "description": i.description,
                    "quantity": i.quantity,
                    "unit_price": i.unit_price,
                    "tax": i.tax,
                    "amount": i.amount,
                }
                for i in items
            ],
            "count": len(items),
        }
    except Exception as exc:
        return _fail(exc)


# --------------------------------------------------------------------------
# 오류 신고
# --------------------------------------------------------------------------

@mcp.tool()
def list_reports(scope: str = "open") -> dict[str, Any]:
    """사용자가 화면에서 직접 접수한 버그 신고(불만 접수) 목록.

    사람이 '이 화면이 이상하다' 며 캡처와 함께 올린 것이다.
    문서의 검증 오류와는 전혀 다르다 -- 그쪽은 list_documents(status='ERROR') 다.

    Args:
        scope: open(미처리) 또는 all(전체).
    """
    try:
        records = report.load_all()
        if scope == "open":
            records = [r for r in records if r.get("status") == report.STATUS_OPEN]
        return {
            "count": len(records),
            "reports": [
                {
                    "slug": r.get("slug"),
                    "number": r.get("number"),
                    "status": r.get("status"),
                    "created_at": r.get("created_at"),
                    "section": r.get("section"),
                    "document_id": r.get("document_id"),
                    "summary": (r.get("message") or "").splitlines()[0][:120],
                    "images": r.get("images") or [],
                }
                for r in records
            ],
        }
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def read_report(slug: str) -> dict[str, Any]:
    """신고 한 건의 본문(report.md) 전체.

    증상뿐 아니라 문서 행·검증 오류·저장된 품목·미저장 편집값·Docling 원문·환경이
    함께 담겨 있다. 캡처 이미지는 파일로만 있으므로 경로를 돌려준다.
    """
    try:
        folder = config.REPORTS_DIR / slug
        body = folder / "report.md"
        if not body.is_file():
            return {"error": f"신고 {slug} 을(를) 찾을 수 없습니다."}
        return {
            "slug": slug,
            "path": str(body),
            "images": [str(p) for p in sorted(folder.glob("screenshot_*"))],
            "report_md": body.read_text(encoding="utf-8"),
        }
    except Exception as exc:
        return _fail(exc)


@mcp.tool()
def resolve_report(slug: str, reopen: bool = False) -> dict[str, Any]:
    """신고를 처리 완료로 표시한다 (reopen=True 면 다시 연다).

    되돌릴 수 있는 유일한 쓰기 동작이라 도구로 낸다. 고친 뒤 신고를 닫는 것이
    이 서버의 주 용도다.
    """
    try:
        status = report.STATUS_OPEN if reopen else report.STATUS_RESOLVED
        if not report.set_status(slug, status):
            return {"error": f"신고 {slug} 을(를) 찾을 수 없습니다."}
        return {"slug": slug, "status": status}
    except Exception as exc:
        return _fail(exc)


# --------------------------------------------------------------------------
# 환경
# --------------------------------------------------------------------------

@mcp.tool()
def health() -> dict[str, Any]:
    """DB·Ollama 연결과 지금 보고 있는 데이터베이스."""
    result: dict[str, Any] = {"database_name": config.MSSQL_DATABASE}
    try:
        ok, message = db.health_check()
        result["database"] = {"ok": ok, "message": message.splitlines()[0]}
    except Exception as exc:
        result["database"] = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    try:
        ok, message = validator.health_check()
        result["ollama"] = {"ok": ok, "message": message.splitlines()[0]}
    except Exception as exc:
        result["ollama"] = {"ok": False, "message": f"{type(exc).__name__}: {exc}"}
    return result


if __name__ == "__main__":
    mcp.run(transport="stdio")
