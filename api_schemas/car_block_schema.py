from typing import Annotated

from pydantic import StringConstraints

from api_schemas.base_schema import BaseSchema
from helpers.constants import MAX_CAR_BLOCK
from helpers.types import datetime_utc


class CarBlockRead(BaseSchema):
    id: int
    user_id: int
    reason: str
    blocked_by: int
    created_at: datetime_utc


class CarBlockCreate(BaseSchema):
    user_id: int
    reason: Annotated[str, StringConstraints(max_length=MAX_CAR_BLOCK)]
