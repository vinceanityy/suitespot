"""Tests for US7.1, tasks 7.1.1 and 7.1.2.

7.1.1 — earn_points / reverse_points / get_balance service layer.
7.1.2 — Points are awarded when POST /reservations/{id}/confirm succeeds;
         GET /users/me/points returns the current balance.
"""
from datetime import date, timedelta, datetime, timezone

import pytest

from app.models import Hotel, Reservation, Room, User
from app.models.points import PointsLedger
from app.services.points import earn_points, get_balance, reverse_points

TODAY = datetime.now(timezone.utc).date()
IN = TODAY + timedelta(days=10)
OUT = TODAY + timedelta(days=12)  # 2 nights


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def user(db_session):
    u = User(email="pts@test.com", hashed_password="x", first_name="P", last_name="T")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.fixture
def room(db_session):
    hotel = Hotel(name="H", address="1 Main", city="SJ", country="US")
    db_session.add(hotel)
    db_session.flush()
    r = Room(hotel_id=hotel.id, room_number="101", price_per_night=100, max_guests=2)
    db_session.add(r)
    db_session.commit()
    return r


@pytest.fixture
def reservation(db_session, user, room):
    res = Reservation(
        user_id=user.id, room_id=room.id,
        check_in_date=IN, check_out_date=OUT,
        total_price=200, status="confirmed",
    )
    db_session.add(res)
    db_session.commit()
    return res


# ── 7.1.1 service-layer tests ─────────────────────────────────────────────────

def test_earn_points_creates_ledger_row(db_session, reservation):
    entry = earn_points(db_session, reservation=reservation)
    assert entry.delta == 200          # $200 → 200 pts
    assert entry.reason == "stay_earned"
    assert entry.user_id == reservation.user_id
    assert entry.reservation_id == reservation.id


def test_earn_points_uses_floor_division(db_session, user, room):
    res = Reservation(
        user_id=user.id, room_id=room.id,
        check_in_date=IN, check_out_date=OUT,
        total_price=199.99, status="confirmed",
    )
    db_session.add(res)
    db_session.commit()
    entry = earn_points(db_session, reservation=res)
    assert entry.delta == 199          # floor($199.99) = 199


def test_get_balance_sums_all_rows(db_session, user, reservation):
    earn_points(db_session, reservation=reservation)
    assert get_balance(db_session, user_id=user.id) == 200


def test_get_balance_zero_when_no_rows(db_session, user):
    assert get_balance(db_session, user_id=user.id) == 0


def test_reverse_points_negates_earned(db_session, user, reservation):
    earn_points(db_session, reservation=reservation)
    reverse_points(db_session, reservation=reservation)
    assert get_balance(db_session, user_id=user.id) == 0


def test_reverse_returns_none_when_nothing_to_reverse(db_session, reservation):
    result = reverse_points(db_session, reservation=reservation)
    assert result is None


# ── 7.1.2 HTTP endpoint tests ─────────────────────────────────────────────────

def _register_and_login(client, email="pts_http@test.com"):
    client.post("/register", json={
        "email": email, "password": "Password1!",
        "first_name": "P", "last_name": "T",
    })
    r = client.post("/login", json={"email": email, "password": "Password1!"})
    return r.json()["access_token"]


def _make_pending_reservation(db_session, user_id, room, total=200):
    res = Reservation(
        user_id=user_id, room_id=room.id,
        check_in_date=date(2030, 3, 1), check_out_date=date(2030, 3, 3),
        total_price=total, status="pending",
    )
    db_session.add(res)
    db_session.commit()
    return res


def test_confirm_awards_points(client, db_session, room):
    token = _register_and_login(client)
    from app.models.user import User as UserModel
    user = db_session.query(UserModel).filter_by(email="pts_http@test.com").first()

    res = _make_pending_reservation(db_session, user.id, room, total=200)

    r = client.post(
        f"/reservations/{res.id}/confirm",
        json={"amount": 200, "payment_method": "card", "transaction_id": "txn_pts_1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200

    balance = get_balance(db_session, user_id=user.id)
    assert balance == 200


def test_confirm_idempotent_does_not_double_award(client, db_session, room):
    token = _register_and_login(client, email="pts_idem@test.com")
    from app.models.user import User as UserModel
    user = db_session.query(UserModel).filter_by(email="pts_idem@test.com").first()

    res = _make_pending_reservation(db_session, user.id, room, total=150)

    body = {"amount": 150, "payment_method": "card", "transaction_id": "txn_pts_2"}
    hdrs = {"Authorization": f"Bearer {token}"}
    r1 = client.post(f"/reservations/{res.id}/confirm", json=body, headers=hdrs)
    r2 = client.post(f"/reservations/{res.id}/confirm", json=body, headers=hdrs)
    assert r1.status_code == 200
    assert r2.status_code == 200
    assert r2.json()["already_confirmed"] is True

    # Only one earn row — no double award
    rows = db_session.query(PointsLedger).filter_by(
        reservation_id=res.id, reason="stay_earned"
    ).all()
    assert len(rows) == 1
    assert get_balance(db_session, user_id=user.id) == 150


def test_get_points_balance_unauthenticated_returns_401(client):
    r = client.get("/users/me/points")
    assert r.status_code == 401


def test_get_points_balance_zero_before_any_booking(client):
    token = _register_and_login(client, email="pts_zero@test.com")
    r = client.get("/users/me/points", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["points"] == 0


def test_get_points_balance_after_confirmation(client, db_session, room):
    token = _register_and_login(client, email="pts_bal@test.com")
    from app.models.user import User as UserModel
    user = db_session.query(UserModel).filter_by(email="pts_bal@test.com").first()

    res = _make_pending_reservation(db_session, user.id, room, total=300)
    client.post(
        f"/reservations/{res.id}/confirm",
        json={"amount": 300, "payment_method": "card", "transaction_id": "txn_pts_3"},
        headers={"Authorization": f"Bearer {token}"},
    )

    r = client.get("/users/me/points", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["points"] == 300


def test_get_points_balance_accumulates_across_bookings(client, db_session, room):
    token = _register_and_login(client, email="pts_acc@test.com")
    from app.models.user import User as UserModel
    user = db_session.query(UserModel).filter_by(email="pts_acc@test.com").first()

    for i, total in enumerate([100, 200]):
        check_in = date(2030, 4, 1 + i * 5)
        check_out = date(2030, 4, 2 + i * 5)
        res = Reservation(
            user_id=user.id, room_id=room.id,
            check_in_date=check_in, check_out_date=check_out,
            total_price=total, status="pending",
        )
        db_session.add(res)
        db_session.commit()
        client.post(
            f"/reservations/{res.id}/confirm",
            json={"amount": total, "payment_method": "card", "transaction_id": f"txn_acc_{i}"},
            headers={"Authorization": f"Bearer {token}"},
        )

    r = client.get("/users/me/points", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["points"] == 300  # 100 + 200
