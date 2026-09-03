"""RabbitMQ 연결 -- 문서 처리 작업을 큐에 올리고(publish_job), 워커가 그 큐를
소비할 때 쓰는 연결도 여기서 만든다(consume).

api.py(발행자)와 worker.py(소비자) 둘 다 이 모듈을 쓴다. 큐 이름·연결 문자열을
두 군데서 따로 정의하면 언젠가 어긋난다.

api.py는 여기서 예외가 나면 그대로 위로 올려서 업로드 자체를 실패로 처리한다
(브로커가 죽어 있는데 202를 돌려주면, 사용자는 접수된 줄 알지만 영영 처리 안
되는 작업이 생긴다).
"""

from __future__ import annotations

import base64
import json
import traceback
from typing import Any, Callable

import pika

from . import config


def connection(*, heartbeat: int | None = None) -> pika.BlockingConnection:
    """RabbitMQ 연결을 새로 연다. 호출부가 닫아야 한다.

    heartbeat 를 주면 기본값(서버 협상값, 보통 60초) 대신 그 값을 쓴다 --
    consume() 이 긴 하트비트가 필요해서 쓴다 (아래 주석 참고).
    """
    params = pika.URLParameters(config.RABBITMQ_URL)
    if heartbeat is not None:
        params.heartbeat = heartbeat
    return pika.BlockingConnection(params)


def _declare_queue(channel: Any) -> None:
    # durable=True -- RabbitMQ 프로세스가 재시작돼도 큐 자체(와 그 안의 durable
    # 메시지)가 남는다. 이게 없으면 브로커만 재시작해도 대기 중이던 작업이
    # 통째로 사라진다.
    channel.queue_declare(queue=config.DOCUMENT_QUEUE, durable=True)


def publish_job(job_id: str, filename: str, data: bytes, skip_duplicates: bool) -> None:
    """업로드된 파일을 큐에 올린다.

    파일 자체(bytes)를 메시지 안에 base64로 담아 보낸다 -- 별도 스테이징
    폴더 없이, 워커가 메시지 하나만 받으면 처리에 필요한 게 다 있게 하려는
    것이다. 송장·영수증 PDF는 보통 몇 MB 안팎이라 메시지로도 충분하다
    (수십 MB짜리 첨부가 흔해지면 이 방식은 재고해야 한다).
    """
    body = json.dumps(
        {
            "job_id": job_id,
            "filename": filename,
            "skip_duplicates": skip_duplicates,
            "data_b64": base64.b64encode(data).decode("ascii"),
        }
    ).encode("utf-8")

    conn = connection()
    try:
        channel = conn.channel()
        _declare_queue(channel)
        channel.basic_publish(
            exchange="",
            routing_key=config.DOCUMENT_QUEUE,
            body=body,
            # delivery_mode=2 -- 메시지 자체도 디스크에 남긴다(durable). 큐만
            # durable 이고 메시지가 아니면, 브로커 재시작 시 큐는 살아도 안의
            # 메시지는 날아간다.
            properties=pika.BasicProperties(delivery_mode=2, content_type="application/json"),
        )
    finally:
        conn.close()


def consume(on_message: Callable[[dict[str, Any]], None]) -> None:
    """document_jobs 큐를 계속 듣는다. worker.py 가 부른다.

    메시지마다 on_message(payload) 를 부른다. payload 에는 job_id/filename/
    skip_duplicates 와, base64를 다시 풀어낸 원본 파일 bytes(data)가 들어있다.

    prefetch_count=1 -- 한 번에 한 건만 받는다. 문서 1건 처리에 수 분이 걸리는데
    여러 건을 미리 받아 쌓아두면, 워커를 여러 대 띄워도 한 워커가 독차지하고
    나머지는 노는 상황이 생긴다. 1건씩 받아야 다음 워커가 바로 다음 건을 집는다.

    heartbeat 를 길게 잡는다 -- on_message(콜백) 안에서 Docling+Ollama 처리가
    통째로 동기적으로 도는 동안 pika가 하트비트에 응답을 못 해, 기본값이면
    처리 도중 RabbitMQ가 연결을 끊어버린다(CONSUMER_HEARTBEAT_SECONDS 주석 참고).
    """
    conn = connection(heartbeat=config.CONSUMER_HEARTBEAT_SECONDS)
    channel = conn.channel()
    _declare_queue(channel)
    channel.basic_qos(prefetch_count=1)

    def _callback(ch: Any, method: Any, _properties: Any, body: bytes) -> None:
        try:
            payload = json.loads(body)
            payload["data"] = base64.b64decode(payload.pop("data_b64"))
            on_message(payload)
        except Exception:
            # 여기까지 오면 pipeline.process_pdf 바깥의 예상 못 한 사고다
            # (그 함수 자체는 실패를 삼켜 FAILED 로 기록하지, 예외를 던지지
            # 않는다). 메시지를 재배달(nack+requeue)하면 이 메시지가 계속
            # 같은 이유로 실패하는 한 워커가 그 자리에 멈춘 것처럼 보이므로,
            # ack 하고 다음 메시지로 넘어간다 -- 이 작업의 최종 상태는 아래
            # finally 이전에 on_message 안에서 이미 FAILED 로 기록됐거나,
            # on_message 자체가 못 불렸다면 job 이 RUNNING/QUEUED 로 남는다
            # (재시도가 필요하면 사람이 다시 올려야 한다).
            traceback.print_exc()
        finally:
            ch.basic_ack(delivery_tag=method.delivery_tag)

    channel.basic_consume(queue=config.DOCUMENT_QUEUE, on_message_callback=_callback)
    try:
        channel.start_consuming()
    except KeyboardInterrupt:
        channel.stop_consuming()
    finally:
        conn.close()
