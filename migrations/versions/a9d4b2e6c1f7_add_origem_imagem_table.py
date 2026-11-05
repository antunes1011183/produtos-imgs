"""add origem_imagem (URL de onde veio a foto atual de cada EAN, pro botão Reprocessar imagem)

Revision ID: a9d4b2e6c1f7
Revises: f3a7d1c8e5b2
Create Date: 2026-11-05 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'a9d4b2e6c1f7'
down_revision = 'f3a7d1c8e5b2'
branch_labels = None
depends_on = None


def upgrade():
    # `db.create_all()` (subida do app.py) já cria tabelas novas; só cria aqui o que faltar.
    if 'origem_imagem' not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            'origem_imagem',
            sa.Column('codbar', sa.String(length=64), nullable=False),
            sa.Column('url', sa.String(length=1000), nullable=False),
            sa.Column('fonte', sa.String(length=40), nullable=True),
            sa.Column('atualizado_em', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('codbar'),
        )


def downgrade():
    op.drop_table('origem_imagem')
