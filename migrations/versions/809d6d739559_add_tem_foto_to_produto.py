"""add tem_foto to produto

Revision ID: 809d6d739559
Revises: 063a17eb3305
Create Date: 2026-09-17 09:57:55.188939

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '809d6d739559'
down_revision = '063a17eb3305'
branch_labels = None
depends_on = None


def upgrade():
    # Migration escrita à mão a partir do que o autogenerate sugeriu: o autogenerate também
    # queria "corrigir" tipos TEXT->String(255) (ruído — SQLite não distingue os dois) e
    # DROPAR 3 índices existentes (ix_produto_cat/ix_produto_desc/ix_produto_marca, criados
    # fora do model declarado em Python) que não têm nada a ver com esta mudança — removido.
    # server_default=false() é o que garante que ADD COLUMN NOT NULL funciona numa tabela com
    # ~945 mil linhas já existentes (cada uma recebe tem_foto=False na hora da migração, em vez
    # de falhar por violar NOT NULL sem valor).
    with op.batch_alter_table('produto', schema=None) as batch_op:
        batch_op.add_column(sa.Column('tem_foto', sa.Boolean(), nullable=False, server_default=sa.false()))
        batch_op.create_index(batch_op.f('ix_produto_tem_foto'), ['tem_foto'], unique=False)


def downgrade():
    with op.batch_alter_table('produto', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_produto_tem_foto'))
        batch_op.drop_column('tem_foto')
