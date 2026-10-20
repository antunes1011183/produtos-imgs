"""Auditoria estática de segurança das rotas do app.py (não importa o app, não toca no banco).

Falha (exit 1) quando:
  1. existe rota SEM `@jwt_required()` que não esteja na lista `PUBLICAS` abaixo;
  2. a lista `PUBLICAS` tem rota que não existe mais (lista velha esconde buraco novo);
  3. o código volta a ter um segredo padrão conhecido (JWT) ou uma chave de API literal.

Por que existe: o Swagger (/apidocs) ficou aberto pra internet sem ninguém perceber, e o segredo
JWT padrão permitia forjar token de admin. Rota pública nova precisa ser DECISÃO consciente:
adicione aqui, com o motivo, na mesma mudança que cria a rota.

Uso:  python scripts/auditoria_seguranca_rotas.py
"""
import ast
import os
import re
import sys

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'app.py')

# rota -> motivo de ser pública
PUBLICAS = {
    '/': 'só redireciona para /configuracoes',
    '/sw.js': 'service worker do PWA (arquivo estático)',
    '/login': 'etapa 1 do login (senha); limitada por IP',
    '/login/verificar': 'etapa 2 do login (código por e-mail); limitada por IP',
    '/terminal/token': 'troca a chave do terminal por token de 3600s; limitada por IP',
    '/configuracoes': 'GET devolve só o HTML do painel (sem segredos); o resto exige JWT dentro da função',
    '/produto-imagem/<codbar>': 'consulta dos terminais; protegida por token via before_request (EXIGIR_TOKEN_IMAGENS)',
    '/produto-imagem/<string:codbar>/gerar-arte': 'upload dos terminais; protegida por token via before_request',
}


# Chaves que já estão no código/histórico do git e ainda dependem de rotação pelo dono da conta
# (o Google Custom Search e o TTS da Azure leem estas constantes direto). Não adicione novas.
LEGADAS_PENDENTES_DE_ROTACAO = ('AIzaSyDcgpSF', "AZURE_SUBSCRIPTION_KEY")


def _nome(dec):
    n = dec.func if isinstance(dec, ast.Call) else dec
    return n.attr if isinstance(n, ast.Attribute) else getattr(n, 'id', '')


def main():
    sys.stdout.reconfigure(encoding='utf-8')
    fonte = open(APP, encoding='utf-8').read()
    arvore = ast.parse(fonte)
    publicas, protegidas = {}, 0
    for no in ast.walk(arvore):
        if not isinstance(no, ast.FunctionDef):
            continue
        rotas = [d.args[0].value for d in no.decorator_list
                 if isinstance(d, ast.Call) and _nome(d) == 'route' and d.args and isinstance(d.args[0], ast.Constant)]
        if not rotas:
            continue
        if any(_nome(d) == 'jwt_required' for d in no.decorator_list):
            protegidas += len(rotas)
        else:
            for r in rotas:
                publicas[r] = no.name

    erros = []
    for rota, fn in sorted(publicas.items()):
        if rota not in PUBLICAS:
            erros.append(f'ROTA PÚBLICA NÃO AUTORIZADA: {rota}  ({fn}) — proteja com @jwt_required() ou justifique em PUBLICAS')
    for rota in PUBLICAS:
        if rota not in publicas:
            erros.append(f'PUBLICAS desatualizada: {rota} não existe mais (ou agora exige JWT) — remova da lista')

    if re.search(r"getenv\(\s*'JWT_SECRET_KEY'\s*,\s*'[^']+'", fonte):
        erros.append("JWT_SECRET_KEY com valor padrão literal no código")
    avisos = []
    padroes = r"(sk-[A-Za-z0-9_-]{20,}|AIza[0-9A-Za-z_-]{30,}|getenv\('AZURE_SUBSCRIPTION_KEY',\s*'[0-9a-f]{32}')"
    for m in re.finditer(padroes, fonte):
        trecho = m.group(1)
        linha = fonte[:m.start()].count(chr(10)) + 1
        if any(trecho.startswith(p) or p in trecho for p in LEGADAS_PENDENTES_DE_ROTACAO):
            avisos.append(f'chave literal CONHECIDA (linha {linha}): {trecho[:12]}… — gire a chave no provedor e mova para Config/variável de ambiente')
        else:
            erros.append(f'chave de API literal NOVA no código (linha {linha}): {trecho[:12]}…')

    print(f'Rotas com JWT: {protegidas} | públicas: {len(publicas)}')
    for r, fn in sorted(publicas.items()):
        print(f'  pública  {r:55s} {PUBLICAS.get(r, "??")}')
    for a in avisos:
        print('  AVISO:', a)
    if erros:
        print('\nPROBLEMAS:')
        for e in erros:
            print('  -', e)
        return 1
    print('\nOK: nenhuma rota pública fora da lista.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
