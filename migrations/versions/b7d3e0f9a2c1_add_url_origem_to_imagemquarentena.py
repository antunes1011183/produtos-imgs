"""add url_origem to imagemquarentena

Revision ID: b7d3e0f9a2c1
Revises: a1c9f3e7b2d4
Create Date: 2026-09-23 14:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'b7d3e0f9a2c1'
down_revision = 'a1c9f3e7b2d4'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('imagem_quarentena', schema=None) as batch_op:
        batch_op.add_column(sa.Column('url_origem', sa.String(length=1000), nullable=True))


def downgrade():
    with op.batch_alter_table('imagem_quarentena', schema=None) as batch_op:
        batch_op.drop_column('url_origem')
