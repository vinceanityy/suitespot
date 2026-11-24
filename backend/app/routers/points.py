"""US7.1, task 7.1.2 — Points balance endpoint.

GET /users/me/points
  Returns the authenticated user's current loyalty-points balance.
  Balance is the sum of all PointsLedger delta rows for the user (never
  negative — capped at zero by get_balance).
"""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models import User
from app.services.points import get_balance

router = APIRouter(prefix="/users", tags=["points"])


@router.get("/me/points")
def my_points_balance(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the calling user's current loyalty-points balance.

    - **200** — `{"points": <int>}` — balance (>= 0).
    - **401** — not authenticated.
    """
    balance = get_balance(db, user_id=user.id)
    return {"points": balance}
