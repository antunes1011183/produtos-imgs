"""add imagem quarentena table

Revision ID: 840efabc3897
Revises: d47bd29d1c4b
Create Date: 2026-09-17 21:39:11.128353

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '840efabc3897'
down_revision = 'd47bd29d1c4b'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        'imagem_quarentena',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codbar', sa.String(length=64), nullable=False),
        sa.Column('arquivo', sa.String(length=255), nullable=True),
        sa.Column('origem', sa.String(length=30), nullable=True),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('decisao', sa.String(length=20), nullable=True),
        sa.Column('revisado_em', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    with op.batch_alter_table('imagem_quarentena', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_imagem_quarentena_codbar'), ['codbar'], unique=False)
        batch_op.create_index(batch_op.f('ix_imagem_quarentena_criado_em'), ['criado_em'], unique=False)
        batch_op.create_index(batch_op.f('ix_imagem_quarentena_decisao'), ['decisao'], unique=False)


def downgrade():
    with op.batch_alter_table('imagem_quarentena', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_imagem_quarentena_decisao'))
        batch_op.drop_index(batch_op.f('ix_imagem_quarentena_criado_em'))
        batch_op.drop_index(batch_op.f('ix_imagem_quarentena_codbar'))
    op.drop_table('imagem_quarentena')
