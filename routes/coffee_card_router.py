from datetime import UTC, datetime
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import and_, update

from api_schemas.coffee_card_schemas import CoffeeCardCreate, CoffeeCardRead, CoffeeCardTransfer
from database import DB_dependency
from db_models.coffee_card_model import CoffeeCard_DB
from db_models.user_model import User_DB
from user.permission import Permission
from user.user_stuff import current_verified_user

coffee_card_router = APIRouter()


def is_cafe_authorized(user: User_DB) -> bool:
    """A user is authorized to handle other members' coffee cards if they have the
    manage Cafe permission, or if they are signed up for a current or upcoming
    cafe shift (i.e. they are working at the cafe)."""
    for post in user.posts:
        for perm in post.permissions:
            if Permission.verify_permission(perm.action, perm.target, "manage", "Cafe"):
                return True

    now = datetime.now(UTC)
    return any(shift.ends_at >= now for shift in user.cafe_shifts)


def cafe_authorization_check(user: Annotated[User_DB | None, Depends(current_verified_user)]) -> bool:
    # Like Permission.check, but also accepts users who are working at the cafe
    if user is None:
        return False
    return is_cafe_authorized(user)


def require_cafe_authorization(user: Annotated[User_DB | None, Depends(current_verified_user)]) -> User_DB:
    # Like Permission.require, but also accepts users who are working at the cafe
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    if not is_cafe_authorized(user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only cafe staff can do this")
    return user


@coffee_card_router.get("/mine", response_model=list[CoffeeCardRead])
def view_my_cards(user: Annotated[User_DB, Permission.member()], db: DB_dependency):
    return (
        db.query(CoffeeCard_DB).filter(CoffeeCard_DB.user_id == user.id).order_by(CoffeeCard_DB.id).all()
    )


@coffee_card_router.get("/user/{user_id}", response_model=list[CoffeeCardRead])
def view_user_cards(
    user_id: int,
    user: Annotated[User_DB, Permission.member()],
    cafe_authorized: Annotated[bool, Depends(cafe_authorization_check)],
    db: DB_dependency,
):
    if user_id != user.id and not cafe_authorized:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the user or cafe staff can view these cards")

    target = db.query(User_DB).filter_by(id=user_id).one_or_none()
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    return db.query(CoffeeCard_DB).filter(CoffeeCard_DB.user_id == user_id).order_by(CoffeeCard_DB.id).all()


@coffee_card_router.get("/{card_id}", response_model=CoffeeCardRead)
def view_card(
    card_id: int,
    user: Annotated[User_DB, Permission.member()],
    cafe_authorized: Annotated[bool, Depends(cafe_authorization_check)],
    db: DB_dependency,
):
    card = db.query(CoffeeCard_DB).filter_by(id=card_id).one_or_none()
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Coffee card not found")
    if card.user_id != user.id and not cafe_authorized:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only the card owner or cafe staff can view this card")
    return card


@coffee_card_router.post("/", response_model=CoffeeCardRead)
def create_card(
    data: CoffeeCardCreate,
    staff: Annotated[User_DB, Depends(require_cafe_authorization)],
    db: DB_dependency,
):
    # Adds a new card to a user's profile, e.g. after buying it at the cafe
    recipient = db.query(User_DB).filter_by(id=data.user_id).one_or_none()
    if recipient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    card = CoffeeCard_DB(user_id=recipient.id, total_coffees=data.total_coffees)
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@coffee_card_router.post("/{card_id}/cross-off", response_model=CoffeeCardRead)
def cross_off_coffee(
    card_id: int,
    user: Annotated[User_DB, Permission.member()],
    cafe_authorized: Annotated[bool, Depends(cafe_authorization_check)],
    db: DB_dependency,
):
    # Crosses off one coffee on the card, the owner or someone working at the cafe
    card = db.query(CoffeeCard_DB).filter_by(id=card_id).one_or_none()
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Coffee card not found")
    if card.user_id != user.id and not cafe_authorized:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Only the card owner or cafe staff can cross off a coffee"
        )

    # Atomic, so two simultaneous cross-offs can never drain the card past zero
    result = db.execute(
        update(CoffeeCard_DB)
        .where(and_(CoffeeCard_DB.id == card_id, CoffeeCard_DB.used_coffees < CoffeeCard_DB.total_coffees))
        .values(used_coffees=CoffeeCard_DB.used_coffees + 1)
    )
    if result.rowcount == 0:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Card is empty")

    db.commit()
    db.refresh(card)
    return card


@coffee_card_router.post("/{card_id}/transfer", response_model=CoffeeCardRead)
def transfer_card(
    card_id: int,
    data: CoffeeCardTransfer,
    user: Annotated[User_DB, Permission.member()],
    cafe_authorized: Annotated[bool, Depends(cafe_authorization_check)],
    db: DB_dependency,
):
    # Hands the card over to another user, just like handing over a physical card
    card = db.query(CoffeeCard_DB).filter_by(id=card_id).one_or_none()
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Coffee card not found")
    if card.user_id != user.id and not cafe_authorized:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Only the card owner or cafe staff can transfer this card"
        )

    recipient = db.query(User_DB).filter_by(id=data.user_id).one_or_none()
    if recipient is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")

    card.user = recipient
    db.commit()
    db.refresh(card)
    return card


@coffee_card_router.delete("/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_card(
    card_id: int,
    staff: Annotated[User_DB, Depends(require_cafe_authorization)],
    db: DB_dependency,
):
    # For when a card was added by mistake, e.g. scanned twice at the cafe
    card = db.query(CoffeeCard_DB).filter_by(id=card_id).one_or_none()
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Coffee card not found")

    db.delete(card)
    db.commit()
    return
