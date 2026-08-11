from datetime import datetime
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy.orm import Session

from database import SessionLog, User, get_db


def get_current_user(
    request: Request,
    db: Session = Depends(get_db),
) -> Optional[User]:
    token = request.cookies.get("session_token")
    if not token:
        return None

    session = (
        db.query(SessionLog)
        .filter(
            SessionLog.session_token == token,
            SessionLog.is_active.is_(True),
        )
        .first()
    )

    if not session or not session.user or not session.user.is_active:
        return None

    session.last_activity = datetime.utcnow()
    db.commit()

    return session.user
