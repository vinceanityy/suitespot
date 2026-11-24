"""US7.1, task 7.1.1 — Points ledger earn rules.

Earn rate: 1 point per $1 of the reservation's total_price (rounded down).
Points are recorded as a new PointsLedger row; the running balance is the
sum of all delta rows for the user.

Usage:
    earn_points(db, reservation=reservation)       # on confirmation
    reverse_points(db, reservation=reservation)    # on refund / cancellation
    get_balance(db, user_id=user_id)               # current balance

Design note:
    earn_points and reverse_points do NOT call db.commit().  The caller is
    responsible for committing so that points changes are atomic with the
    status change that triggers them (e.g. reservation → confirmed).
    Both functions are idempotent: a second call for the same reservation
    is a no-op, returning the existing ledger row.
"""

from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.points import PointsLedger
from app.models.reservation import Reservation

POINTS_PER_DOLLAR = 1  # 1 point per $1 spent


def _points_for(reservation: Reservation) -> int:
    """How many points a reservation earns (floor division)."""
    return int(Decimal(str(reservation.total_price)))


def earn_points(
    db: Session,
    *,
    reservation: Reservation,
    note: str | None = None,
) -> PointsLedger:
    """Award points for a completed/confirmed stay and return the ledger row.

    Idempotent: if a stay_earned row already exists for this reservation,
    returns it without creating a duplicate (the UniqueConstraint on
    (reservation_id, reason) makes double-insertion a constraint violation,
    which we catch and handle gracefully instead of letting it 500).

    Does NOT commit — the caller commits so that points + reservation status
    change atomically.
    """
    # Idempotency check: return existing row if already awarded.
    existing = db.execute(
        select(PointsLedger).where(
            PointsLedger.reservation_id == reservation.id,
            PointsLedger.reason == "stay_earned",
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    delta = _points_for(reservation)
    entry = PointsLedger(
        user_id=reservation.user_id,
        reservation_id=reservation.id,
        delta=delta,
        reason="stay_earned",
        note=note,
    )
    db.add(entry)
    try:
        db.flush()  # surfaces the constraint without committing
    except IntegrityError:
        db.rollback()
        # Race: another request inserted the row between our SELECT and flush.
        existing = db.execute(
            select(PointsLedger).where(
                PointsLedger.reservation_id == reservation.id,
                PointsLedger.reason == "stay_earned",
            )
        ).scalar_one()
        return existing
    return entry


def reverse_points(
    db: Session,
    *,
    reservation: Reservation,
    note: str | None = None,
) -> PointsLedger | None:
    """Reverse the net points impact of this reservation for the user.

    This covers both earned points (stay_earned rows) AND any redeemed points
    (points_redeemed rows, which are negative).  On a refund after redemption
    the user gets their spent points back, so we negate the full net sum.

    Returns None when the net is already zero (nothing to reverse).
    Does NOT commit — the caller commits atomically with the cancellation.
    """
    # Idempotency: if a stay_reversed row already exists, don't add another.
    already = db.execute(
        select(PointsLedger).where(
            PointsLedger.reservation_id == reservation.id,
            PointsLedger.reason == "stay_reversed",
        )
    ).scalar_one_or_none()
    if already is not None:
        return already

    # Sum ALL ledger rows for this reservation (earn + redeem).
    # points_redeemed rows are negative, so -net returns them on refund.
    net: int = db.execute(
        select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(
            PointsLedger.reservation_id == reservation.id
        )
    ).scalar_one()

    if net == 0:
        return None

    entry = PointsLedger(
        user_id=reservation.user_id,
        reservation_id=reservation.id,
        delta=-net,
        reason="stay_reversed",
        note=note,
    )
    db.add(entry)
    db.flush()
    return entry


def get_balance(db: Session, *, user_id: str) -> int:
    """Return the current points balance for a user (never negative)."""
    total: int = db.execute(
        select(func.coalesce(func.sum(PointsLedger.delta), 0)).where(
            PointsLedger.user_id == user_id
        )
    ).scalar_one()
    return max(0, int(total))
