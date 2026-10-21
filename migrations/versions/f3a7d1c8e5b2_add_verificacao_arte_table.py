"""add verificacao_arte (conferência foto x EAN antes de gerar a arte)

Revision ID: f3a7d1c8e5b2
Revises: e2b6c9d4a7f1
Create Date: 2026-10-21 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'f3a7d1c8e5b2'
down_revision = 'e2b6c9d4a7f1'
branch_labels = None
depends_on = None


def upgrade():
    # `db.create_all()` (subida do app.py) já cria tabelas novas; só cria aqui o que faltar.
    if 'verificacao_arte' not in set(sa.inspect(op.get_bind()).get_table_names()):
        op.create_table(
            'verificacao_arte',
            sa.Column('codbar', sa.String(length=64), nullable=False),
            sa.Column('hash_imagem', sa.String(length=40), nullable=False),
            sa.Column('descricao_cadastro', sa.String(length=400), nullable=False),
            sa.Column('descricao_usada', sa.String(length=400), nullable=False),
            sa.Column('veredito', sa.String(length=12), nullable=False),
            sa.Column('motivo', sa.String(length=500), nullable=True),
            sa.Column('texto_lido', sa.String(length=500), nullable=True),
            sa.Column('verificado_em', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('codbar'),
        )


def downgrade():
    op.drop_table('verificacao_arte')
