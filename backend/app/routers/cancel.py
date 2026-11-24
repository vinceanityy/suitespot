"""POST /reservations/{id}/cancel: cancel my reservation (US2.2, task 2.2.1).
US7.1, task 7.1.4 — reverse points on cancellation.
"""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies.auth import get_current_user
from app.models import Reservation, User
from app.schemas.cancel import CancelResponse
from app.services.cancellation import (
    NotCancellable,
    ReservationNotFound,
    cancel_reservation,
)
from app.services.points import reverse_points

router = APIRouter(prefix="/reservations", tags=["reservations"])


def _error(kind: str, message: str) -> list[dict]:
    # Same shape as FastAPI's 422 errors, so the frontend handles all errors alike.
    return [{"type": kind, "loc": ["path", "reservation_id"], "msg": message}]


@router.post("/{reservation_id}/cancel", response_model=CancelResponse)
def cancel(
    reservation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        result = cancel_reservation(db, reservation_id=reservation_id, user_id=user.id)
    except ReservationNotFound:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=_error("not_found", "Reservation not found."),
        )
    except NotCancellable as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=_error("not_cancellable", error.message),
        )

    # 7.1.4 — reverse any points earned (or redeemed) for this reservation.
    # cancel_reservation already committed; re-fetch the reservation to pass to reverse_points.
    reservation = db.execute(
        select(Reservation).where(Reservation.id == reservation_id)
    ).scalar_one_or_none()
    if reservation is not None:
        try:
            rev = reverse_points(db, reservation=reservation)
            if rev is not None:
                db.commit()
        except Exception:
            # Points reversal is a loyalty perk, not a cancellation blocker.
            pass

    return result
