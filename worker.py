"""문서 처리 워커 -- RabbitMQ 큐(document_jobs)를 듣다가, 작업이 오면
파이프라인(app.pipeline.process_pdf)을 돌리고 결과를 dbo.jobs 에 남긴다.

api.py 는 업로드를 접수해 이 큐에 발행만 한다. 실제 Docling 추출·Ollama 호출·
DB 저장은 전부 이 프로세스에서 일어난다 -- api.py 와 분리해 둔 이유는:

  - 문서 1건에 수 분이 걸리는데, api.py 는 화면·MCP·Java 백엔드가 같이 쓰는
    프로세스라 거기서 오래 붙들면 다른 요청까지 느려진다.
  - 이 프로세스를 여러 개 띄우면 그만큼 동시에 처리된다(RabbitMQ가 알아서
    작업을 나눠준다) -- api.py 를 늘릴 필요가 없다.
  - api.py 가 재시작돼도(코드 고칠 때마다 그런다) 큐에 쌓인 작업은 안 사라진다.

실행:  python worker.py
       (여러 창에서 동시에 띄우면 그만큼 병렬로 처리된다)
"""

from __future__ import annotations

import sys
from typing import Any

from app import config, db, mq, pipeline


def _now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def handle_message(payload: dict[str, Any]) -> None:
    job_id = payload["job_id"]
    filename = payload["filename"]
    skip_duplicates = bool(payload["skip_duplicates"])
    data = payload["data"]

    print(f"[worker] #{job_id[:8]} 시작: {filename} ({len(data)} bytes)", flush=True)
    db.update_job(job_id, state="RUNNING")

    try:
        outcome = pipeline.process_pdf(filename, data, skip_duplicates=skip_duplicates)
    except Exception as exc:
        # process_pdf 자체는 파이프라인 실패를 삼켜 FAILED 로 기록하고 예외를
        # 올리지 않는다(app/pipeline.py 참고). 여기까지 오는 것은 그 바깥의
        # 예상 못 한 사고이므로 작업만 실패로 닫는다.
        db.update_job(
            job_id,
            state="FAILED",
            message=f"{type(exc).__name__}: {exc}",
            finished_at=_now(),
        )
        print(f"[worker] #{job_id[:8]} 실패(예외): {exc}", flush=True)
        return

    db.update_job(
        job_id,
        state="DONE",
        document_id=outcome.document_id,
        document_status=outcome.status,
        error_count=outcome.error_count,
        skipped=int(outcome.skipped),
        message=outcome.message,
        finished_at=_now(),
    )
    print(
        f"[worker] #{job_id[:8]} 끝: 문서 #{outcome.document_id} -> {outcome.status}",
        flush=True,
    )


def main() -> int:
    sys.stdout.reconfigure(line_buffering=True)

    db.init_db()
    db.cleanup_stale_processing()

    print(f"[worker] RabbitMQ 연결: {config.RABBITMQ_URL}")
    print(f"[worker] 큐: {config.DOCUMENT_QUEUE}")
    print("[worker] 대기 중... (Ctrl+C 로 종료)")
    try:
        mq.consume(handle_message)
    except KeyboardInterrupt:
        print("\n[worker] 종료합니다.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
