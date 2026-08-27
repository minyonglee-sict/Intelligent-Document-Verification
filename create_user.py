"""로그인 계정을 만드는 CLI. 관리자 계정은 반드시 이 스크립트(또는 이미 있는
관리자가 화면의 "사용자 관리"에서 승격)로만 생긴다 -- 가입 화면은 항상 일반
사용자로만 계정을 만든다. 아무나 체크 한 번으로 관리자가 될 수 있으면, "관리자만
할 수 있다"는 제한 자체가 의미 없어지기 때문이다.

    python create_user.py 김검수 "김검수(재무팀)"
    python create_user.py 김관리 "김관리(IT팀)" --admin           # 첫 관리자 만들기
    python create_user.py 김검수 "김검수(재무팀)" --reset-password  # 비밀번호만 재설정
    python create_user.py 김검수 "김검수(재무팀)" --reset-password --admin  # 재설정 + 관리자로 승격

비밀번호는 인자로 받지 않는다 -- 셸 히스토리·프로세스 목록에 평문으로 남는 것을
피하려고 getpass 로 그 자리에서만 입력받는다.
"""

from __future__ import annotations

import argparse
import getpass
import sys

from app import auth, config, db


def main() -> int:
    parser = argparse.ArgumentParser(description="로그인 계정 만들기 / 비밀번호 재설정")
    parser.add_argument("username", help="로그인 아이디")
    parser.add_argument("display_name", help="화면에 보일 이름 (예: 김검수(재무팀))")
    parser.add_argument(
        "--reset-password", action="store_true",
        help="이미 있는 계정의 비밀번호만 새로 설정한다",
    )
    parser.add_argument(
        "--admin", action="store_true",
        help="관리자로 만든다(새 계정) 또는 관리자로 승격시킨다(--reset-password 와 같이 쓸 때)",
    )
    args = parser.parse_args()

    db.init_db()

    existing = db.get_user_by_username(args.username)
    if existing and not args.reset_password:
        print(f"이미 있는 아이디입니다: {args.username}. 비밀번호만 바꾸려면 --reset-password 를 쓰세요.")
        return 1
    if not existing and args.reset_password:
        print(f"없는 아이디입니다: {args.username}")
        return 1

    password = getpass.getpass("비밀번호: ")
    if len(password) < 8:
        print("비밀번호는 8자 이상이어야 합니다.")
        return 1
    if password != getpass.getpass("비밀번호 확인: "):
        print("두 입력이 다릅니다.")
        return 1

    password_hash, salt = auth.hash_password(password)

    if existing:
        # admin_reset_password 는 비밀번호를 바꾸는 김에 그 계정의 세션도 전부
        # 지운다 -- 재설정하는 이유가 "누가 이 계정을 쓰고 있는 것 같다"인 경우가
        # 흔해서, 예전 로그인을 그대로 살려두면 재설정한 의미가 없다.
        db.admin_reset_password(existing["id"], password_hash, salt)
        if args.admin:
            db.set_user_role(existing["id"], config.ROLE_ADMIN)
            print(f"'{args.username}' 비밀번호를 재설정하고 관리자로 승격했습니다.")
        else:
            print(f"'{args.username}' 비밀번호를 재설정했습니다.")
    else:
        role = config.ROLE_ADMIN if args.admin else config.ROLE_USER
        db.create_user(args.username, args.display_name, password_hash, salt, role=role)
        print(
            f"'{args.username}' ({args.display_name}, {config.ROLE_LABELS[role]}) 계정을 "
            f"만들었습니다. 세션 유효기간: {config.SESSION_TTL_HOURS}시간"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
