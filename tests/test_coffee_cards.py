# type: ignore
from datetime import datetime, timedelta, timezone

import pytest

from db_models.cafe_shift_model import CafeShift_DB
from .basic_factories import auth_headers, create_membered_user, user_data_factory


def login(client, email, password="Password123"):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def make_member(client, db_session, email, last_name):
    """Create a new member and return (user, token)."""
    data = user_data_factory(email=email, last_name=last_name)
    user = create_membered_user(client, db_session, **data)
    return user, login(client, email)


def give_shift(db_session, user, starts_at, ends_at):
    shift = CafeShift_DB(user_id=user.id, starts_at=starts_at, ends_at=ends_at)
    db_session.add(shift)
    db_session.commit()
    return shift


def create_card(client, token, user_id, total_coffees=11):
    return client.post("/coffee-cards/", json={"user_id": user_id, "total_coffees": total_coffees}, headers=auth_headers(token))


@pytest.fixture
def cafe_staff(client, db_session):
    """A member who is currently signed up for a cafe shift."""
    user, token = make_member(client, db_session, "staff@example.com", "Staff")
    now = datetime.now(timezone.utc)
    give_shift(db_session, user, now - timedelta(hours=1), now + timedelta(hours=1))
    return user, token


def get_card(client, token, card_id):
    return client.get(f"/coffee-cards/{card_id}", headers=auth_headers(token))


def cross_off(client, token, card_id):
    return client.post(f"/coffee-cards/{card_id}/cross-off", headers=auth_headers(token))


def transfer(client, token, card_id, user_id):
    return client.post(f"/coffee-cards/{card_id}/transfer", json={"user_id": user_id}, headers=auth_headers(token))


# Viewing cards


def test_view_my_cards_is_empty_for_new_user(client, member_token):
    resp = client.get("/coffee-cards/mine", headers=auth_headers(member_token))
    assert resp.status_code == 200
    assert resp.json() == []


def test_unauthenticated_cannot_view_or_manage_cards(client):
    assert client.get("/coffee-cards/mine").status_code == 401
    assert client.get("/coffee-cards/1").status_code == 401
    assert client.post("/coffee-cards/", json={"user_id": 1}).status_code == 401
    assert client.post("/coffee-cards/1/cross-off").status_code == 401
    assert client.delete("/coffee-cards/1").status_code == 401


def test_non_member_cannot_view_cards(client, non_member_token):
    assert client.get("/coffee-cards/mine", headers=auth_headers(non_member_token)).status_code == 403


def test_owner_can_view_card(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    resp = get_card(client, member_token, card_id)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user_id"] == membered_user.id
    assert data["total_coffees"] == 11
    assert data["used_coffees"] == 0
    assert data["remaining_coffees"] == 11


def test_cafe_staff_can_view_card(client, cafe_staff, membered_user):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    assert get_card(client, staff_token, card_id).status_code == 200


def test_other_member_cannot_view_card(client, cafe_staff, membered_user, db_session):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    _, other_token = make_member(client, db_session, "other@example.com", "Other")
    assert get_card(client, other_token, card_id).status_code == 403


def test_view_nonexistent_card(client, member_token):
    assert get_card(client, member_token, 999999).status_code == 404


def test_user_can_list_own_cards(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    for _ in range(2):
        assert create_card(client, staff_token, membered_user.id).status_code == 200

    resp = client.get(f"/coffee-cards/user/{membered_user.id}", headers=auth_headers(member_token))
    assert resp.status_code == 200
    assert len(resp.json()) == 2


def test_cafe_staff_can_list_cards_of_user(client, cafe_staff, membered_user):
    _, staff_token = cafe_staff
    create_card(client, staff_token, membered_user.id)

    resp = client.get(f"/coffee-cards/user/{membered_user.id}", headers=auth_headers(staff_token))
    assert resp.status_code == 200
    assert len(resp.json()) == 1


def test_other_member_cannot_list_cards_of_user(client, membered_user, db_session, member_token):
    _, other_token = make_member(client, db_session, "other@example.com", "Other")
    assert client.get(f"/coffee-cards/user/{membered_user.id}", headers=auth_headers(other_token)).status_code == 403


def test_cafe_staff_cannot_list_cards_of_nonexistent_user(client, cafe_staff):
    _, staff_token = cafe_staff
    assert client.get("/coffee-cards/user/999999", headers=auth_headers(staff_token)).status_code == 404


# Creating cards


def test_cafe_staff_can_create_card(client, cafe_staff, membered_user):
    _, staff_token = cafe_staff

    resp = create_card(client, staff_token, membered_user.id)
    assert resp.status_code == 200
    card = resp.json()
    assert card["user_id"] == membered_user.id
    assert card["total_coffees"] == 11
    assert card["used_coffees"] == 0
    assert card["remaining_coffees"] == 11
    assert card["user"] == {"id": membered_user.id, "first_name": "Member", "last_name": "User"}


def test_member_with_upcoming_shift_can_create_card(client, db_session, membered_user):
    user, token = make_member(client, db_session, "future-staff@example.com", "Future")
    now = datetime.now(timezone.utc)
    give_shift(db_session, user, now + timedelta(days=2), now + timedelta(days=2, hours=2))

    assert create_card(client, token, membered_user.id).status_code == 200


def test_member_with_only_past_shift_cannot_create_card(client, db_session, membered_user):
    user, token = make_member(client, db_session, "past-staff@example.com", "Past")
    now = datetime.now(timezone.utc)
    give_shift(db_session, user, now - timedelta(days=2), now - timedelta(days=2, hours=2))

    assert create_card(client, token, membered_user.id).status_code == 403


def test_plain_member_cannot_create_card(client, member_token, membered_user):
    assert create_card(client, member_token, membered_user.id).status_code == 403


def test_admin_with_manage_cafe_permission_can_create_card(client, admin_token, membered_user):
    assert create_card(client, admin_token, membered_user.id).status_code == 200


def test_cafe_staff_cannot_create_card_for_nonexistent_user(client, cafe_staff):
    _, staff_token = cafe_staff
    assert create_card(client, staff_token, 999999).status_code == 404


def test_user_can_own_multiple_cards(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff

    ids = set()
    for _ in range(3):
        resp = create_card(client, staff_token, membered_user.id)
        assert resp.status_code == 200
        ids.add(resp.json()["id"])
    assert len(ids) == 3

    resp = client.get("/coffee-cards/mine", headers=auth_headers(member_token))
    assert [card["id"] for card in resp.json()] == sorted(ids)


def test_create_card_with_custom_total(client, cafe_staff, membered_user):
    _, staff_token = cafe_staff

    resp = create_card(client, staff_token, membered_user.id, total_coffees=5)
    assert resp.status_code == 200
    assert resp.json()["total_coffees"] == 5

    assert create_card(client, staff_token, membered_user.id, total_coffees=0).status_code == 422
    assert create_card(client, staff_token, membered_user.id, total_coffees=101).status_code == 422


# Crossing off coffees


def test_owner_can_cross_off(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    resp = cross_off(client, member_token, card_id)
    assert resp.status_code == 200
    data = resp.json()
    assert data["used_coffees"] == 1
    assert data["remaining_coffees"] == 10


def test_cafe_staff_can_cross_off_other_users_card(client, cafe_staff, membered_user):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    resp = cross_off(client, staff_token, card_id)
    assert resp.status_code == 200
    assert resp.json()["remaining_coffees"] == 10


def test_other_member_cannot_cross_off(client, cafe_staff, membered_user, db_session):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    _, other_token = make_member(client, db_session, "other@example.com", "Other")
    assert cross_off(client, other_token, card_id).status_code == 403


def test_cross_off_empty_card(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id, total_coffees=1).json()["id"]

    assert cross_off(client, member_token, card_id).status_code == 200
    assert cross_off(client, member_token, card_id).status_code == 400


def test_cross_off_nonexistent_card(client, member_token):
    assert cross_off(client, member_token, 999999).status_code == 404


# Transferring cards


def test_owner_can_transfer_card(client, cafe_staff, membered_user, member_token, db_session):
    _, staff_token = cafe_staff
    other, other_token = make_member(client, db_session, "target@example.com", "Target")
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    resp = transfer(client, member_token, card_id, other.id)
    assert resp.status_code == 200
    assert resp.json()["user_id"] == other.id

    assert client.get("/coffee-cards/mine", headers=auth_headers(member_token)).json() == []
    resp = client.get(f"/coffee-cards/user/{other.id}", headers=auth_headers(other_token))
    assert [card["id"] for card in resp.json()] == [card_id]


def test_cafe_staff_can_transfer_card(client, cafe_staff, membered_user, db_session):
    _, staff_token = cafe_staff
    other, _ = make_member(client, db_session, "target@example.com", "Target")
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    assert transfer(client, staff_token, card_id, other.id).status_code == 200


def test_other_member_cannot_transfer_card(client, cafe_staff, membered_user, db_session):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    other, other_token = make_member(client, db_session, "other@example.com", "Other")
    assert transfer(client, other_token, card_id, other.id).status_code == 403


def test_transfer_to_nonexistent_user(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    assert transfer(client, member_token, card_id, 999999).status_code == 404


def test_transfer_nonexistent_card(client, member_token, membered_user):
    assert transfer(client, member_token, 999999, membered_user.id).status_code == 404


# Deleting cards


def test_cafe_staff_can_delete_card(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    assert client.delete(f"/coffee-cards/{card_id}", headers=auth_headers(staff_token)).status_code == 204
    assert get_card(client, member_token, card_id).status_code == 404


def test_owner_cannot_delete_card(client, cafe_staff, membered_user, member_token):
    _, staff_token = cafe_staff
    card_id = create_card(client, staff_token, membered_user.id).json()["id"]

    assert client.delete(f"/coffee-cards/{card_id}", headers=auth_headers(member_token)).status_code == 403


def test_delete_nonexistent_card(client, cafe_staff):
    _, staff_token = cafe_staff
    assert client.delete("/coffee-cards/999999", headers=auth_headers(staff_token)).status_code == 404
