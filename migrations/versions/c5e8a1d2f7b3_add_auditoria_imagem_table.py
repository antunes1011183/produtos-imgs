"""add auditoria_imagem table

Revision ID: c5e8a1d2f7b3
Revises: b7d3e0f9a2c1
Create Date: 2026-10-19 10:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'c5e8a1d2f7b3'
down_revision = 'b7d3e0f9a2c1'
branch_labels = None
depends_on = None


def upgrade():
    # `db.create_all()` (rodado na subida do app.py) já cria tabelas novas sozinho — se ela já
    # existir quando esta migration rodar, não há nada a fazer (mesmo cuidado de idempotência
    # necessário em qualquer ambiente que suba o app antes de rodar `flask db upgrade`).
    if 'auditoria_imagem' in sa.inspect(op.get_bind()).get_table_names():
        return
    op.create_table(
        'auditoria_imagem',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('codbar', sa.String(length=64), nullable=False),
        sa.Column('hash_imagem', sa.String(length=40), nullable=True),
        sa.Column('motivos', sa.String(length=300), nullable=False),
        sa.Column('detalhes', sa.Text(), nullable=True),
        sa.Column('ia_hash', sa.String(length=40), nullable=True),
        sa.Column('decisao', sa.String(length=20), nullable=True),
        sa.Column('criado_em', sa.DateTime(), nullable=False),
        sa.Column('atualizado_em', sa.DateTime(), nullable=False),
        sa.Column('revisado_em', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_auditoria_imagem_codbar', 'auditoria_imagem', ['codbar'], unique=True)
    op.create_index('ix_auditoria_imagem_decisao', 'auditoria_imagem', ['decisao'], unique=False)


def downgrade():
    op.drop_index('ix_auditoria_imagem_decisao', table_name='auditoria_imagem')
    op.drop_index('ix_auditoria_imagem_codbar', table_name='auditoria_imagem')
    op.drop_table('auditoria_imagem')
