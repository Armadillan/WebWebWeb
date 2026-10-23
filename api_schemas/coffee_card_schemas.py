from pydantic import Field
from api_schemas.base_schema import BaseSchema
from api_schemas.user_schemas import SimpleUserRead
from helpers.types import datetime_utc


class CoffeeCardRead(BaseSchema):
    id: int
    user_id: int
    user: SimpleUserRead
    total_coffees: int
    used_coffees: int
    remaining_coffees: int
    created_at: datetime_utc


class CoffeeCardCreate(BaseSchema):
    # The user the card is added to (e.g. identified by scanning their QR code)
    user_id: int
    # How many coffees the card is worth. Defaults to the standard card: 10 bought + 1 extra.
    total_coffees: int = Field(default=11, ge=1, le=100)


class CoffeeCardTransfer(BaseSchema):
    # The user the card is transferred to
    user_id: int
