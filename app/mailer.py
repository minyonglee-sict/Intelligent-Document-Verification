"""메일 발송. 표준 라이브러리(smtplib)만 쓴다 -- 이 기능 하나 때문에 외부
패키지를 새로 늘리지 않는다.

지금은 비밀번호 재설정 요청(api.py의 /auth/forgot-password)이 첫 번째로 쓰지만,
범용으로 만들어뒀다 -- 나중에 오류 신고 접수 알림 같은 것도 같은 send_email 을
그대로 쓰면 된다.
"""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from email.utils import formataddr

from . import config


def send_email(to: str, subject: str, body: str) -> None:
    """SMTP_USER/SMTP_PASSWORD 가 설정 안 되어 있으면 RuntimeError.

    조용히 무시하지 않는 이유: 호출부(예: 비밀번호 재설정 요청 화면)가 "요청을
    보냈습니다"라고 사용자에게 보여줬는데 실제로는 안 나갔으면, 그 사람은 계속
    비밀번호를 못 바꾼 채로 관리자 답을 기다리게 된다.
    """
    if not config.SMTP_USER or not config.SMTP_PASSWORD:
        raise RuntimeError(
            "메일 발송이 설정되지 않았습니다 (SMTP_USER/SMTP_PASSWORD 환경변수 필요)."
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    # 생짜 주소만 쓰면("cyh110477@gmail.com") 자동 발송 티가 나서 스팸함으로
    # 분류되기 쉽다. 표시 이름을 붙여서("Intelligent Document Verification
    # <cyh110477@gmail.com>") 조금이라도 낮춘다 -- formataddr 이 필요하면 알아서
    # 따옴표·인코딩을 처리해 준다.
    msg["From"] = formataddr((config.SMTP_FROM_NAME, config.SMTP_FROM))
    msg["To"] = to
    msg.set_content(body)

    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=15) as server:
        server.starttls()
        server.login(config.SMTP_USER, config.SMTP_PASSWORD)
        server.send_message(msg)
