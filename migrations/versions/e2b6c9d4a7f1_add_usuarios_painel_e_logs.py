"""add usuarios do painel, convites de senha e log de acessos

Revision ID: e2b6c9d4a7f1
Revises: c5e8a1d2f7b3
Create Date: 2026-10-20 03:00:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'e2b6c9d4a7f1'
down_revision = 'c5e8a1d2f7b3'
branch_labels = None
depends_on = None


def upgrade():
    # `db.create_all()` (subida do app.py) já cria tabelas novas; só cria aqui o que faltar.
    existentes = set(sa.inspect(op.get_bind()).get_table_names())
    if 'usuario_painel' not in existentes:
        op.create_table(
            'usuario_painel',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('email', sa.String(length=120), nullable=False),
            sa.Column('nome', sa.String(length=120), nullable=False),
            sa.Column('senha_hash', sa.String(length=255), nullable=True),
            sa.Column('ativo', sa.Boolean(), nullable=False),
            sa.Column('pode_convidar', sa.Boolean(), nullable=False),
            sa.Column('ultimo_login', sa.DateTime(), nullable=True),
            sa.Column('criado_em', sa.DateTime(), nullable=False),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_usuario_painel_email', 'usuario_painel', ['email'], unique=True)
    if 'convite_senha' not in existentes:
        op.create_table(
            'convite_senha',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('usuario_id', sa.Integer(), nullable=False),
            sa.Column('token_hash', sa.String(length=64), nullable=False),
            sa.Column('expira_em', sa.DateTime(), nullable=False),
            sa.Column('usado_em', sa.DateTime(), nullable=True),
            sa.Column('criado_por', sa.String(length=120), nullable=True),
            sa.Column('criado_em', sa.DateTime(), nullable=False),
            sa.ForeignKeyConstraint(['usuario_id'], ['usuario_painel.id']),
            sa.PrimaryKeyConstraint('id'),
            sa.UniqueConstraint('token_hash'),
        )
        op.create_index('ix_convite_senha_usuario_id', 'convite_senha', ['usuario_id'])
    if 'log_acesso_painel' not in existentes:
        op.create_table(
            'log_acesso_painel',
            sa.Column('id', sa.Integer(), nullable=False),
            sa.Column('criado_em', sa.DateTime(), nullable=False),
            sa.Column('usuario', sa.String(length=120), nullable=False),
            sa.Column('tipo', sa.String(length=10), nullable=False),
            sa.Column('evento', sa.String(length=40), nullable=False),
            sa.Column('descricao', sa.String(length=400), nullable=False),
            sa.Column('ip', sa.String(length=64), nullable=True),
            sa.Column('metodo', sa.String(length=8), nullable=True),
            sa.Column('caminho', sa.String(length=200), nullable=True),
            sa.Column('status', sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint('id'),
        )
        op.create_index('ix_log_acesso_painel_criado_em', 'log_acesso_painel', ['criado_em'])
        op.create_index('ix_log_acesso_painel_usuario', 'log_acesso_painel', ['usuario'])
        op.create_index('ix_log_acesso_painel_tipo', 'log_acesso_painel', ['tipo'])


def downgrade():
    op.drop_table('log_acesso_painel')
    op.drop_table('convite_senha')
    op.drop_table('usuario_painel')
