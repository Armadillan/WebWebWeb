from zoneinfo import ZoneInfo
from fastapi import HTTPException, status
from api_schemas.car_booking_schema import CarBookingCreate, CarBookingUpdate
from db_models.council_model import Council_DB
from database import DB_dependency
from typing import Annotated
from sqlalchemy import or_, and_, literal, text
from user.permission import Permission
from db_models.user_model import User_DB
from db_models.car_booking_model import CarBooking_DB
from datetime import UTC, datetime
from db_models.car_block_model import CarBlock_DB
from mailer import bilf_mailer


def is_user_blocked(user_id: int, db: DB_dependency) -> bool:
    block = db.query(CarBlock_DB).filter(CarBlock_DB.user_id == user_id).first()
    return block is not None


# Fixed key for the car booking advisory lock. The value is arbitrary but must be stable.
CAR_BOOKING_LOCK_KEY = 48017


def acquire_car_booking_lock(db: DB_dependency) -> None:
    """Serialize car booking mutations with a transaction-scoped advisory lock.

    The overlap check and the insert/update that follows it must happen atomically,
    otherwise two concurrent requests can both pass the check and double-book the car.
    The lock is held until the current transaction commits or rolls back, which
    covers the whole request, and is then released automatically by PostgreSQL.
    """
    db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": CAR_BOOKING_LOCK_KEY})


def overlap_query_create(
    booking: CarBookingCreate,
    db: DB_dependency,
):
    result = (
        db.query(CarBooking_DB)
        .filter(
            and_(
                or_(
                    and_(booking.start_time >= CarBooking_DB.start_time, booking.start_time < CarBooking_DB.end_time),
                    and_(booking.end_time > CarBooking_DB.start_time, booking.end_time <= CarBooking_DB.end_time),
                    and_(booking.start_time <= CarBooking_DB.start_time, booking.end_time >= CarBooking_DB.end_time),
                ),
            )
        )
        .first()
    )

    if result:
        return True
    return False


def overlap_query_update(
    start_time: datetime,
    end_time: datetime,
    booking_id: int,
    db: DB_dependency,
):
    """Checks whether the interval [start_time, end_time) overlaps any other booking.

    start_time and end_time must be the *effective* times of the booking being
    updated (the new values if provided, otherwise the existing ones), so that
    partial updates are checked against the full resulting interval.
    """
    result = (
        db.query(CarBooking_DB)
        .filter(
            and_(
                or_(
                    and_(start_time >= CarBooking_DB.start_time, start_time < CarBooking_DB.end_time),
                    and_(end_time > CarBooking_DB.start_time, end_time <= CarBooking_DB.end_time),
                    and_(start_time <= CarBooking_DB.start_time, end_time >= CarBooking_DB.end_time),
                ),
                # filters out the booking we are editing
                literal(booking_id) != CarBooking_DB.booking_id,
            )
        )
        .first()
    )

    return result is not None


def create_new_booking(
    data: CarBookingCreate,
    db: DB_dependency,
    current_user: Annotated[User_DB, Permission.member()],
    manage_permission: Annotated[bool, Permission.check("manage", "Car")],
) -> CarBooking_DB:
    # Serialize the overlap check and the insert that follows it
    acquire_car_booking_lock(db)

    booking_confirmed = True  # Default to confirmed unless conditions below change it
    if data.end_time < data.start_time:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Booking end time cannot be before start time.")
    if data.start_time == data.end_time:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Booking start time cannot be equal to end time.")
    booking_overlaps = overlap_query_create(
        booking=data,
        db=db,
    )
    if booking_overlaps:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Booking overlaps with another booking.")
    if data.start_time < datetime.now(UTC):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Booking start time cannot be in the past.")

    # Unconfirm personal bookings /Vic, BilF 25/26
    if not manage_permission and data.personal:
        booking_confirmed = False

    # Require council_id if not personal booking
    if not data.personal and data.council_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Council ID is required for non-personal bookings.")

    # Remove council_id if personal booking
    if data.personal and data.council_id is not None:
        data.council_id = None

    # Check if the council_id is valid
    if data.council_id is not None:
        council = db.query(Council_DB).filter(Council_DB.id == data.council_id).one_or_none()
        if council is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid council ID.")

    # Disallow regular users from booking cars for other councils
    if not manage_permission and data.council_id is not None:  # For safety, we don't care if it is a personal booking
        if data.council_id not in [post.council_id for post in current_user.posts]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="You do not have permission to book cars for this council."
            )

    db_booking = CarBooking_DB(
        start_time=data.start_time,
        end_time=data.end_time,
        user_id=current_user.id,
        description=data.description,
        confirmed=booking_confirmed,
        personal=data.personal,
        council_id=data.council_id,
    )
    db.add(db_booking)

    db.flush()

    bilf_mailer.bilf_mailer(db_booking)

    db.commit()

    return db_booking


def booking_update(
    booking_id: int,
    data: CarBookingUpdate,
    current_user: Annotated[User_DB, Permission.member()],
    manage_permission: Annotated[bool, Permission.check("manage", "Car")],
    db: DB_dependency,
) -> CarBooking_DB:
    # Serialize the overlap check and the update that follows it
    acquire_car_booking_lock(db)

    car_booking = db.query(CarBooking_DB).filter(CarBooking_DB.booking_id == booking_id).first()
    if car_booking is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Booking not found.")
    if (car_booking.user != current_user) and (not manage_permission):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="You do not have permission to update this booking.")

    # Only staff with the manage Car permission may change the confirmed status
    if data.confirmed is not None and not manage_permission:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="Only staff can change the confirmed status of a booking."
        )

    if data.confirmed is not None:
        booking_confirmed = data.confirmed  # default to updated value, fallback to previous value
    else:
        booking_confirmed = car_booking.confirmed

    # only check for illegal overlap if new times are provided
    if data.start_time is not None or data.end_time is not None:
        # Use new values if provided, otherwise use existing values
        check_start_time = data.start_time if data.start_time is not None else car_booking.start_time
        check_end_time = data.end_time if data.end_time is not None else car_booking.end_time

        if check_start_time >= check_end_time:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST, detail="Booking start time cannot be after nor equal to end time."
            )

        # Consistent with create: a (new) start time cannot be in the past
        if data.start_time is not None and data.start_time < datetime.now(UTC):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Booking start time cannot be in the past.")

        booking_overlaps = overlap_query_update(
            start_time=check_start_time,
            end_time=check_end_time,
            booking_id=booking_id,
            db=db,
        )

        if booking_overlaps:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Booking overlaps with another booking.")

    # unconfirms personal bookings that now fall outside of school hours
    if not manage_permission and (data.personal or data.personal is None and car_booking.personal):

        stockholm_tz = ZoneInfo("Europe/Stockholm")

        # Unconfirm booking between 17:00 and 08:00
        if data.start_time is not None:
            sthlm_start_time = data.start_time.astimezone(stockholm_tz)
            if sthlm_start_time.hour < 8 or sthlm_start_time.hour >= 17:
                booking_confirmed = False
            # Unconfirm booking on weekends
            if sthlm_start_time.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
                booking_confirmed = False
            # Unconfirm large movements of times
            if abs((data.start_time - car_booking.start_time).total_seconds()) >= 24 * 3600:  # 24h
                booking_confirmed = False
        if data.end_time is not None:
            sthlm_end_time = data.end_time.astimezone(stockholm_tz)
            if sthlm_end_time.hour < 8 or sthlm_end_time.hour >= 17:
                booking_confirmed = False
            if sthlm_end_time.weekday() >= 5:
                booking_confirmed = False
            if abs((data.end_time - car_booking.end_time).total_seconds()) >= 24 * 3600:  # 24h
                booking_confirmed = False

        # Unconfirm booking of > 24h, unless the previous was > 20h (allowing for a little bit of editing)
        check_start_time = data.start_time if data.start_time is not None else car_booking.start_time
        check_end_time = data.end_time if data.end_time is not None else car_booking.end_time

        if (check_end_time - check_start_time).total_seconds() > 24 * 3600 and (
            car_booking.end_time - car_booking.start_time
        ).total_seconds() <= 20 * 3600:
            booking_confirmed = False

    # Remove council_id if personal booking
    if data.personal and data.council_id is not None:
        data.council_id = None

    # Check if the council_id is valid
    if data.council_id is not None:
        council = db.query(Council_DB).filter(Council_DB.id == data.council_id).one_or_none()
        if council is None:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid council ID.")

    # Disallow regular users from booking cars for other councils
    if not manage_permission and data.council_id is not None:
        if data.council_id not in [post.council_id for post in current_user.posts]:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, detail="You do not have permission to book cars for this council."
            )

    # Require a council_id if the booking changes from personal to council
    if (
        data.personal is not None
        and data.personal is False
        and car_booking.personal is True
        and data.council_id is None
    ):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Council ID is required when making non-personal booking."
        )

    car_booking.confirmed = booking_confirmed

    for attr in ["description", "start_time", "end_time", "personal", "council_id"]:
        value = getattr(data, attr, None)
        if value is not None:
            setattr(car_booking, attr, value)

    db.commit()

    return car_booking
