"""기존 SQLite 데이터를 MS-SQL로 옮긴다. 한 번만 실행하면 된다.

    python migrate_sqlite_to_mssql.py            # 옮기기
    python migrate_sqlite_to_mssql.py --dry-run  # 무엇이 옮겨질지만 확인

SQLite 파일은 지우지 않는다. 옮긴 뒤 결과를 확인하고 직접 지울 것.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from typing import Any

from app import config, db


def _sqlite_rows(conn: sqlite3.Connection, sql: str, *params) -> list[dict[str, Any]]:
    conn.row_factory = sqlite3.Row
    return [dict(r) for r in conn.execute(sql, params)]


def main() -> int:
    parser = argparse.ArgumentParser(description="SQLite -> MS-SQL 데이터 이관")
    parser.add_argument("--dry-run", action="store_true", help="옮기지 않고 내용만 출력")
    args = parser.parse_args()

    if not config.SQLITE_PATH.exists():
        print(f"옮길 SQLite 파일이 없습니다: {config.SQLITE_PATH}")
        return 0

    src = sqlite3.connect(config.SQLITE_PATH)
    documents = _sqlite_rows(src, "SELECT * FROM documents ORDER BY id")
    columns = {r["name"] for r in _sqlite_rows(src, "PRAGMA table_info(documents)")}
    has_line_items = bool(
        _sqlite_rows(src, "SELECT name FROM sqlite_master WHERE type='table' AND name='line_items'")
    )

    print(f"원본  : {config.SQLITE_PATH}")
    print(f"대상  : {config.MSSQL_DATABASE} @ {config.MSSQL_SERVER}")
    print(f"문서  : {len(documents)}건")
    print(f"품목 테이블 존재: {has_line_items}")
    print()

    if args.dry_run:
        for doc in documents:
            items = (
                _sqlite_rows(src, "SELECT COUNT(*) AS n FROM line_items WHERE document_id=?", doc["id"])[0]["n"]
                if has_line_items else 0
            )
            errors = _sqlite_rows(
                src, "SELECT COUNT(*) AS n FROM validation_errors WHERE document_id=?", doc["id"]
            )[0]["n"]
            print(f"  #{doc['id']:>3} {doc['status']:<10} 품목 {items:>3}행  오류 {errors}건  {doc['filename']}")
        print("\n--dry-run 이므로 아무것도 쓰지 않았습니다.")
        return 0

    db.init_db()
    ok, message = db.health_check()
    print(f"[MS-SQL] {message}")
    if not ok:
        return 1

    existing = {d["filename"] for d in db.list_documents()}
    moved = skipped = 0

    for doc in documents:
        if doc["filename"] in existing:
            print(f"  건너뜀 #{doc['id']} {doc['filename']} (대상에 같은 파일명이 이미 있음)")
            skipped += 1
            continue

        # 1) 자리 예약 (해시 중복 검사는 끄고 원본 그대로 옮긴다)
        reserved, _ = db.reserve_document(
            filename=doc["filename"],
            file_hash=doc["file_hash"] or "",
            stored_path=doc["stored_path"] or "",
            skip_duplicates=False,
        )
        new_id = reserved["id"]

        # 2) 필드 복원 (컬럼 구조 / 예전 fields_json 양쪽 지원)
        if "fields_json" in columns and doc.get("fields_json"):
            from app.schemas import InvoiceFields

            fields = InvoiceFields.model_validate_json(doc["fields_json"])
        else:
            header = {name: doc.get(name) for name in db.HEADER_FIELDS}
            items = (
                _sqlite_rows(
                    src,
                    "SELECT description, quantity, unit_price, amount FROM line_items"
                    " WHERE document_id=? ORDER BY position",
                    doc["id"],
                )
                if has_line_items else []
            )
            from app.schemas import InvoiceFields, LineItem

            fields = InvoiceFields(
                **header,
                line_items=[
                    LineItem(
                        description=i["description"] or "",
                        quantity=i["quantity"],
                        unit_price=i["unit_price"],
                        amount=i["amount"],
                    )
                    for i in items
                ],
            )

        # 3) 본문과 검증 오류
        from app.schemas import ValidationIssue

        errors = [
            ValidationIssue(
                field=e["field"] or "document",
                message=e["message"],
                severity=e["severity"],
                source=e["source"],
            )
            for e in _sqlite_rows(
                src,
                "SELECT field, message, severity, source, resolved FROM validation_errors"
                " WHERE document_id=? ORDER BY id",
                doc["id"],
            )
            if not e["resolved"]
        ]

        db.finalize_document(
            new_id,
            status=doc["status"],
            is_valid=None if doc["is_valid"] is None else bool(doc["is_valid"]),
            markdown=doc["markdown"] or "",
            docling_json=json.loads(doc["docling_json"]) if doc["docling_json"] else {},
            fields=fields,
            errors=errors,
            model=doc["model"] or "",
            page_count=doc["page_count"] or 0,
            failure_reason=doc["failure_reason"],
        )

        # 4) 승인 이력과 검수 메모는 finalize 로 채워지지 않으므로 따로 넣는다
        with db.connect() as conn:
            conn.execute(
                "UPDATE dbo.documents SET reviewer_note=?, created_at=?, updated_at=?,"
                " validated_at=? WHERE id=?",
                doc["reviewer_note"],
                doc["created_at"],
                doc["updated_at"],
                doc["validated_at"],
                new_id,
            )
            if doc["status"] == config.STATUS_VALIDATED:
                conn.execute(
                    "UPDATE dbo.validation_errors SET resolved=1 WHERE document_id=?",
                    new_id,
                )

        print(f"  옮김  #{doc['id']} -> #{new_id}  {doc['filename']} "
              f"(품목 {len(fields.line_items)}행, 오류 {len(errors)}건)")
        moved += 1

    src.close()
    print(f"\n완료: {moved}건 이관, {skipped}건 건너뜀")
    print(f"SQLite 파일은 그대로 두었습니다: {config.SQLITE_PATH}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
