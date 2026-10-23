"""chore(migration): make car_block_table.user_id unique

Revision ID: 387653983d3d
Revises: bcef3ab26f9a
Create Date: 2026-10-23 17:59:03

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '387653983d3d'
down_revision: Union[str, None] = 'bcef3ab26f9a'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A user can be blocked at most once. Remove any duplicate blocks that may
    # exist from the old check-then-insert race, keeping the oldest one per user
    op.execute(
        """
        DELETE FROM car_block_table a
        USING car_block_table b
        WHERE a.user_id = b.user_id AND a.id > b.id
        """
    )
    op.drop_index(op.f('ix_car_block_table_user_id'), table_name='car_block_table')
    op.create_unique_constraint('uq_car_block_table_user_id', 'car_block_table', ['user_id'])


def downgrade() -> None:
    op.drop_constraint('uq_car_block_table_user_id', 'car_block_table', type_='unique')
    op.create_index(op.f('ix_car_block_table_user_id'), 'car_block_table', ['user_id'], unique=False)
