"""add busca_imagem_bloqueada to produto

Revision ID: d47bd29d1c4b
Revises: 809d6d739559
Create Date: 2026-09-17 15:37:00.234602

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'd47bd29d1c4b'
down_revision = '809d6d739559'
branch_labels = None
depends_on = None


def upgrade():
    # server_default=false() garante que ADD COLUMN NOT NULL funciona direto numa tabela com
    # ~945 mil linhas já existentes (cada uma recebe False na hora da migração) — mesmo padrão
    # da migration anterior (tem_foto). Sem índice: diferente de tem_foto, essa coluna não é
    # usada pra ordenar o catálogo, só é lida pontualmente por EAN.
    with op.batch_alter_table('produto', schema=None) as batch_op:
        batch_op.add_column(sa.Column('busca_imagem_bloqueada', sa.Boolean(), nullable=False, server_default=sa.false()))


def downgrade():
    with op.batch_alter_table('produto', schema=None) as batch_op:
        batch_op.drop_column('busca_imagem_bloqueada')
