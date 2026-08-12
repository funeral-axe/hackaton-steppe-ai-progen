from typing import Optional

from database import User, UserRole


ROLE_HOME = {
    UserRole.ADMIN.value: "/admin",
    UserRole.OPERATOR.value: "/operator",
    UserRole.USER.value: "/user",
}


def get_user_role(user: Optional[User]) -> str:
    if not user or not user.role:
        return ""

    return user.role.strip().lower()


def has_role(user: Optional[User], *roles: str) -> bool:
    return get_user_role(user) in roles


def role_home(user: Optional[User]) -> str:
    if not user:
        return "/login"

    return ROLE_HOME.get(
        get_user_role(user),
        "/login",
    )
