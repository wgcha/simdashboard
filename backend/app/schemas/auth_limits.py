"""Shared password-account limits for registration and login."""

PASSWORD_USERNAME_MAX_LENGTH = 80
PASSWORD_MIN_LENGTH = 8
PASSWORD_MAX_LENGTH = 256


# New passwords (registration, password change, admin temporary password; user decision 2026-10-14):
# at least 8 characters with letters, digits and special characters, and not containing the user ID.
# Existing passwords keep working at login.
PASSWORD_POLICY_TEXT = "8자 이상, 영문·숫자·특수문자를 모두 포함하고 아이디를 포함하지 않아야 합니다."


def password_policy_problem(password: str, username: str | None = None) -> str | None:
    """Korean reason why ``password`` is not acceptable as a new password, or ``None``. Never echoes it."""
    if len(password) < PASSWORD_MIN_LENGTH:
        return f"비밀번호는 {PASSWORD_MIN_LENGTH}자 이상이어야 합니다."
    if len(password) > PASSWORD_MAX_LENGTH:
        return f"비밀번호는 {PASSWORD_MAX_LENGTH}자 이하여야 합니다."
    if not any(ch.isascii() and ch.isalpha() for ch in password):
        return "비밀번호에 영문자를 포함하세요."
    if not any(ch.isascii() and ch.isdigit() for ch in password):
        return "비밀번호에 숫자를 포함하세요."
    if not any(not ch.isalnum() and not ch.isspace() for ch in password):
        return "비밀번호에 특수문자(예: ! @ # $ %)를 포함하세요."
    name = (username or "").strip().lower()
    if len(name) >= 3 and name in password.lower():
        return "비밀번호에 아이디를 포함할 수 없습니다."
    return None
