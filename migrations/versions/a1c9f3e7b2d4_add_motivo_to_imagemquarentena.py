"""add motivo to imagemquarentena

Revision ID: a1c9f3e7b2d4
Revises: 840efabc3897
Create Date: 2026-09-22 16:40:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'a1c9f3e7b2d4'
down_revision = '840efabc3897'
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table('imagem_quarentena', schema=None) as batch_op:
        batch_op.add_column(sa.Column('motivo', sa.String(length=30), nullable=True))


def downgrade():
    with op.batch_alter_table('imagem_quarentena', schema=None) as batch_op:
        batch_op.drop_column('motivo')
