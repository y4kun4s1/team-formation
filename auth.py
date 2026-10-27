import bcrypt


def hash_password(password: str) -> str:
    """비밀번호를 bcrypt 해시로 바꾼다. 원래 비밀번호로 되돌릴 수 없다."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=10)).decode("ascii")


def check_password(password: str, hashed: str) -> bool:
    """입력한 비밀번호를 같은 방식으로 해시해서 저장된 값과 비교한다. 잘못된 입력은 False."""
    try:
        return bcrypt.checkpw(password.encode("utf-8"), hashed.encode("ascii"))
    except (ValueError, TypeError):
        return False
