"""Corrige a dupla codificação ('LOÃ\\x87Ã\\x83O' -> 'LOÇÃO') nos textos do catálogo.

Uso (no servidor, de dentro da pasta do projeto):
    venv/bin/python scripts/reparar_mojibake_catalogo.py            # só mostra o que mudaria
    venv/bin/python scripts/reparar_mojibake_catalogo.py --aplicar  # faz backup e grava

Só toca em texto que decodifica como UTF-8 válido depois de desfazer a dupla codificação; texto já
correto não muda. Antes de gravar, copia o banco inteiro pra instance/produtos.db.bak_mojibake_<data>.
Pode rodar com o serviço no ar (grava em lotes, com tempo de espera pra lock).
"""
import argparse
import os
import sqlite3
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
from texto_utils import reparar_mojibake  # noqa: E402

COLUNAS = ('description', 'marca', 'categoriaText', 'embalagem')
LOTE = 5000


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--db', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'instance', 'produtos.db'))
    args = ap.parse_args()
    db = os.path.abspath(args.db)

    con = sqlite3.connect(db, timeout=60)
    mudancas = []
    contagem = {c: 0 for c in COLUNAS}
    for row in con.execute(f"select codbar, {', '.join(COLUNAS)} from produto"):
        novos = {}
        for nome, valor in zip(COLUNAS, row[1:]):
            if valor:
                novo = reparar_mojibake(valor)
                if novo != valor:
                    novos[nome] = novo
                    contagem[nome] += 1
        if novos:
            mudancas.append((row[0], novos))
    print(f'{len(mudancas)} produtos com texto a corrigir: {contagem}')
    for codbar, novos in mudancas[:5]:
        print('  ex.:', codbar, novos)
    if not args.aplicar:
        print('(simulação — nada foi gravado; use --aplicar)')
        return

    bak = f"{db}.bak_mojibake_{time.strftime('%Y%m%d_%H%M%S')}"
    dst = sqlite3.connect(bak)
    con.backup(dst)
    dst.close()
    print('backup:', bak)

    feitos = 0
    for i in range(0, len(mudancas), LOTE):
        lote = mudancas[i:i + LOTE]
        for codbar, novos in lote:
            sets = ', '.join(f'{k} = ?' for k in novos)
            con.execute(f'update produto set {sets} where codbar = ?', [*novos.values(), codbar])
        con.commit()
        feitos += len(lote)
        print(f'  gravados {feitos}/{len(mudancas)}', flush=True)
    con.close()
    print('pronto')


if __name__ == '__main__':
    main()
