"""로그인 인증 -- 비밀번호 해시와 세션 토큰을 만들고 검증한다.

DB 세션(dbo.sessions) 자체는 db.py 가 관리한다. 여기는 순수 계산만 한다:
bcrypt 같은 추가 의존성을 늘리지 않으려고, 표준 라이브러리 PBKDF2-HMAC-SHA256으로
비밀번호를 해시한다 -- 이 프로젝트가 이미 pyodbc/fastapi 외엔 외부 패키지를 최소로
유지해 온 방식과 같다.

계정은 관리자가 create_user.py 로 직접 만들거나, 화면의 가입 화면에서 누구나
스스로 만든다.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

_ITERATIONS = 200_000


def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    """(해시, 솔트) 를 16진 문자열 쌍으로 돌려준다. salt 를 안 주면 새로 만든다."""
    salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt), _ITERATIONS
    )
    return digest.hex(), salt


def verify_password(password: str, password_hash: str, salt: str) -> bool:
    """타이밍 공격을 막으려고 문자열 비교 대신 hmac.compare_digest 를 쓴다."""
    computed, _ = hash_password(password, salt)
    return hmac.compare_digest(computed, password_hash)


def new_token() -> str:
    """추측할 수 없는 세션 토큰. URL-safe 가 아니어도 되므로 hex 로 충분하다."""
    return secrets.token_hex(32)
