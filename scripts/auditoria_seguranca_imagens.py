"""Auditoria de segurança das imagens já salvas em IMAGES_FOLDER (static/imgs_produtos).

Passa cada imagem já salva pelo mesmo classificador de conteúdo usado em tempo real
(`_imagem_e_segura`, ver `save_image_from_response` no app.py e a seção do incidente do EAN
7898909864181 no CLAUDE.md) — qualquer imagem marcada como imprópria é MOVIDA pra fila de
quarentena (não apagada direto): o arquivo sai de `IMAGES_FOLDER`/`PROCESSED_IMAGES_FOLDER`,
vira uma linha em `ImagemQuarentena` e o produto é bloqueado automaticamente (mesma lógica de
`_quarentenar_imagem`). Um humano revisa depois na aba "Quarentena" do painel — confirma (apaga
o arquivo de vez) ou libera (falso positivo, restaura a imagem).

Uso:
    ./venv/Scripts/python.exe scripts/auditoria_seguranca_imagens.py

Roda inteiramente dentro do próprio ambiente da aplicação (mesmo banco, mesmas pastas) — não
precisa de nenhum argumento. Pensado pra rodar tanto neste ambiente de dev quanto no srv-mupa
(produção), onde o catálogo de imagens é bem maior — cada imagem custa uma chamada ao Gemini
(`gemini-2.5-flash-lite`), então numa base grande isso pode levar bastante tempo e consumir cota
real da API. Recomendado rodar em background (`nohup ... &` ou equivalente) e acompanhar o log.
"""
import os
import sys

# Garante que o script funcione rodando de qualquer diretório, desde que esteja dentro da
# pasta do projeto (mesmo padrão de scripts avulsos já existentes em scripts/).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from app import (  # noqa: E402
    app, db, _imagem_e_segura, _quarentenar_imagem,
    IMAGES_FOLDER, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS,
)


def main():
    with app.app_context():
        arquivos = sorted(os.listdir(IMAGES_FOLDER))
        total = len(arquivos)
        impropias = []
        erros = []

        print(f'Iniciando auditoria de {total} imagem(ns) em {IMAGES_FOLDER}...', flush=True)

        for i, nome in enumerate(arquivos, 1):
            caminho = os.path.join(IMAGES_FOLDER, nome)
            if not os.path.isfile(caminho):
                continue
            codbar = nome.rsplit('.', 1)[0]
            try:
                with open(caminho, 'rb') as f:
                    dados = f.read()
                segura = _imagem_e_segura(dados)
                status = 'OK' if segura else 'IMPROPRIA — indo pra quarentena'
                print(f'[{i}/{total}] {codbar}: {status}', flush=True)

                if not segura:
                    impropias.append(codbar)
                    # Move pra quarentena (copia os bytes já lidos + registra + bloqueia o
                    # produto) e só DEPOIS remove os arquivos das pastas normais — nessa ordem,
                    # pra nunca ficar num estado intermediário sem nenhuma cópia do arquivo se
                    # algo falhar no meio.
                    _quarentenar_imagem(dados, codbar, origem='auditoria')
                    for pasta in (IMAGES_FOLDER, PROCESSED_IMAGES_FOLDER):
                        for ext in ALLOWED_EXTENSIONS:
                            p = os.path.join(pasta, f'{codbar}.{ext}')
                            if os.path.exists(p):
                                os.remove(p)
                                print(f'    removido de {pasta}: {p}', flush=True)
            except Exception as e:
                print(f'[{i}/{total}] {codbar}: ERRO ({e})', flush=True)
                erros.append(codbar)

        print()
        print('===== RESUMO =====')
        print(f'Total verificado: {total}')
        print(f'Impropias encontradas e movidas pra quarentena: {len(impropias)} -> {impropias}')
        print(f'Erros de classificacao (permitidas por padrao, nao tocadas): {len(erros)} -> {erros}')
        if impropias:
            print()
            print('Revise os casos acima na aba "Quarentena" do painel (/configuracoes) antes de')
            print('confirmar ou liberar cada um.')


if __name__ == '__main__':
    main()
