"""Define (ou troca) a senha de um usuário do painel direto no banco, sem subir o app.

Uso:
    python scripts/definir_senha_usuario.py antunes@mupa.app          (pergunta a senha, sem mostrar)
    NOVA_SENHA='...' python scripts/definir_senha_usuario.py antunes@mupa.app   (não interativo)

Serve pro primeiro acesso do administrador principal e pra recuperar acesso se alguém perder a
senha. A senha é gravada só como hash (scrypt), nunca em texto. O usuário precisa já existir
(o app cria os 3 usuários master na subida).
"""
import getpass
import os
import re
import sqlite3
import sys

from werkzeug.security import generate_password_hash

BANCO = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'instance', 'produtos.db')


def main():
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    email = sys.argv[1].strip().lower()
    senha = os.environ.get('NOVA_SENHA') or getpass.getpass(f'Nova senha para {email}: ')
    if len(senha) < 10 or not re.search(r'[A-Za-z]', senha) or not re.search(r'\d', senha):
        print('Senha fraca: use 10+ caracteres com letras e números.')
        return 1
    con = sqlite3.connect(BANCO)
    cur = con.execute('update usuario_painel set senha_hash=?, ativo=1 where email=?', (generate_password_hash(senha), email))
    con.commit()
    if cur.rowcount == 0:
        print(f'Usuário {email} não existe (suba o app uma vez para criar os usuários master).')
        return 1
    print(f'Senha de {email} definida.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
