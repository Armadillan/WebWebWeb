from sqlalchemy import ForeignKey
from .base_model import BaseModel_DB
from sqlalchemy.orm import relationship, Mapped, mapped_column
from helpers.db_util import created_at_column
from helpers.types import datetime_utc
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .user_model import User_DB


class CoffeeCard_DB(BaseModel_DB):
    """Digital version of the physical coffee card.

    A card is bought at the cafe for the price of 10 coffees and entitles
    the owner to 11 coffees (10 + 1 extra). Coffees are crossed off by the
    owner or by someone working at the cafe (honor system).
    """

    __tablename__ = "coffee_card_table"

    id: Mapped[int] = mapped_column(primary_key=True, init=False)

    user_id: Mapped[int] = mapped_column(ForeignKey("user_table.id"))
    user: Mapped["User_DB"] = relationship(back_populates="coffee_cards", init=False)

    # A standard card is 11 coffees, but the total is a field so that
    # other card sizes can be introduced without a schema change.
    total_coffees: Mapped[int] = mapped_column(default=11)
    used_coffees: Mapped[int] = mapped_column(default=0)

    created_at: Mapped[datetime_utc] = created_at_column()

    @property
    def remaining_coffees(self) -> int:
        return self.total_coffees - self.used_coffees
