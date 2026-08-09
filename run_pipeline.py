"""UI 없이 파이프라인을 실행하는 CLI (배치 처리 / 스모크 테스트용).

    python run_pipeline.py invoice-0-2.pdf invoice-0-3.pdf
    python run_pipeline.py *.pdf --force
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from app import config, db, pipeline, validator


def main() -> int:
    # 1건에 수 분씩 걸린다. 출력을 파일로 넘기면 블록 버퍼링 탓에 진행 상황이
    # 한참 뒤에야 보이므로 줄 단위로 흘려보낸다.
    sys.stdout.reconfigure(line_buffering=True)

    parser = argparse.ArgumentParser(description="PDF 검증 파이프라인 실행")
    parser.add_argument("paths", nargs="+", help="처리할 PDF 경로")
    parser.add_argument(
        "--force", action="store_true", help="중복 해시여도 다시 처리한다"
    )
    args = parser.parse_args()

    db.init_db()

    ok, message = validator.health_check()
    print(f"[ollama] {message}")
    if not ok:
        return 1

    exit_code = 0
    for raw_path in args.paths:
        path = Path(raw_path)
        if not path.is_file():
            print(f"[skip] {path} — 파일이 없습니다")
            continue

        print(f"\n=== {path.name} ===")
        outcome = pipeline.process_pdf(
            path.name, path.read_bytes(), skip_duplicates=not args.force
        )
        print(f"  status   : {outcome.status}")
        print(f"  doc id   : {outcome.document_id}")
        if outcome.message:
            print(f"  note     : {outcome.message}")

        if outcome.document_id and not outcome.skipped:
            doc = db.get_document(outcome.document_id)
            fields = db.load_fields(doc)
            print(
                f"  invoice  : {fields.invoice_number} / {fields.issue_date} "
                f"/ {fields.vendor_name} / total={fields.total_amount}"
            )
            print(f"  items    : {len(fields.line_items)}")
            for err in db.get_errors(outcome.document_id):
                mark = "!" if err["severity"] == "critical" else "-"
                print(f"    [{mark}] ({err['source']}) {err['field']}: {err['message']}")

        if outcome.status == config.STATUS_FAILED:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
