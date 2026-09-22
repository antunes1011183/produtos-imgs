"""Auditoria de segurança/qualidade das imagens já salvas (static/processed_images, com
static/imgs_produtos como fallback pra arquivos legados que nunca ganharam uma versão
processada — ver "single-copy storage" no CLAUDE.md).

Passa cada imagem já salva pelas MESMAS duas checagens usadas em tempo real dentro de
`save_image_from_response`:
1. `_imagem_e_segura` — conteúdo impróprio (nudez/violência/etc, ver o incidente do EAN
   7898909864181 no CLAUDE.md).
2. `_imagem_corresponde_descricao` — a imagem plausivelmente É do produto cadastrado (`motivo`
   'descricao_incompativel', ver seção "Verificação de correspondência imagem × descrição" no
   CLAUDE.md); só roda quando o produto tem uma `description` real pra comparar.

Qualquer imagem reprovada em qualquer uma das duas é MOVIDA pra fila de quarentena (não apagada
direto): o arquivo sai de `IMAGES_FOLDER`/`PROCESSED_IMAGES_FOLDER`, vira uma linha em
`ImagemQuarentena` (com o `motivo` certo) e o produto é bloqueado automaticamente (mesma lógica
de `_quarentenar_imagem`). Um humano revisa depois na aba "Quarentena" do painel — confirma
(apaga o arquivo de vez) ou libera (falso positivo, restaura a imagem).

Uma imagem só é checada UMA vez (a que está sendo servida de verdade pro EAN — processada com
prioridade sobre a crua, mesma regra usada em toda a aplicação), nunca as duas cópias do mesmo
produto — evita gastar duas chamadas ao Gemini pra uma coisa que o público só vê de um jeito.

Uso:
    ./venv/Scripts/python.exe scripts/auditoria_seguranca_imagens.py

Roda inteiramente dentro do próprio ambiente da aplicação (mesmo banco, mesmas pastas) — não
precisa de nenhum argumento. Pensado pra rodar tanto neste ambiente de dev quanto no srv-mupa
(produção), onde o catálogo de imagens é bem maior — cada imagem custa até duas chamadas ao
Gemini (`gemini-2.5-flash-lite`: uma de segurança, e uma de correspondência quando o produto tem
description), então numa base grande isso pode levar bastante tempo e consumir cota real da API.
Recomendado rodar em background (`nohup ... &` ou equivalente) e acompanhar o log.
"""
import os
import sys

# Garante que o script funcione rodando de qualquer diretório, desde que esteja dentro da
# pasta do projeto (mesmo padrão de scripts avulsos já existentes em scripts/).
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)
os.chdir(BASE_DIR)

from app import (  # noqa: E402
    app, Produto, _imagem_e_segura, _imagem_corresponde_descricao, _quarentenar_imagem,
    find_existing_image, IMAGES_FOLDER, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS,
)


def _codbars_com_imagem():
    """Une os EANs com arquivo em qualquer uma das duas pastas (nome do arquivo, sem
    extensão) — o catálogo tem produtos com foto só numa das duas (legado só na crua, ou já
    migrado só pra processada)."""
    codbars = set()
    for pasta in (IMAGES_FOLDER, PROCESSED_IMAGES_FOLDER):
        for nome in os.listdir(pasta):
            caminho = os.path.join(pasta, nome)
            if os.path.isfile(caminho):
                codbars.add(nome.rsplit('.', 1)[0])
    return sorted(codbars)


def main():
    with app.app_context():
        codbars = _codbars_com_imagem()
        total = len(codbars)
        reprovadas = []  # (codbar, motivo)
        erros = []

        print(f'Iniciando auditoria de {total} produto(s) com imagem (processed_images + imgs_produtos como fallback legado)...', flush=True)

        for i, codbar in enumerate(codbars, 1):
            # Mesma prioridade usada em toda a aplicação: processada primeiro (é a que
            # realmente é servida hoje), crua só como fallback de arquivo legado.
            caminho = (find_existing_image(codbar, PROCESSED_IMAGES_FOLDER, ALLOWED_EXTENSIONS)
                       or find_existing_image(codbar, IMAGES_FOLDER, ALLOWED_EXTENSIONS))
            if not caminho:
                continue  # não deveria acontecer (codbar veio de uma dessas pastas), defesa extra
            try:
                with open(caminho, 'rb') as f:
                    dados = f.read()

                motivo = None
                if not _imagem_e_segura(dados):
                    motivo = 'impropria'
                else:
                    produto = Produto.query.filter_by(codbar=codbar).first()
                    descricao = (produto.description or '').strip() if produto else ''
                    if descricao and not _imagem_corresponde_descricao(dados, descricao, produto.marca):
                        motivo = 'descricao_incompativel'

                status = 'OK' if not motivo else f'REPROVADA ({motivo}) — indo pra quarentena'
                print(f'[{i}/{total}] {codbar}: {status}', flush=True)

                if motivo:
                    reprovadas.append((codbar, motivo))
                    # Move pra quarentena (copia os bytes já lidos + registra + bloqueia o
                    # produto) e só DEPOIS remove os arquivos das pastas normais — nessa ordem,
                    # pra nunca ficar num estado intermediário sem nenhuma cópia do arquivo se
                    # algo falhar no meio.
                    _quarentenar_imagem(dados, codbar, origem='auditoria', motivo=motivo)
                    for pasta in (IMAGES_FOLDER, PROCESSED_IMAGES_FOLDER):
                        for ext in ALLOWED_EXTENSIONS:
                            p = os.path.join(pasta, f'{codbar}.{ext}')
                            if os.path.exists(p):
                                os.remove(p)
                                print(f'    removido de {pasta}: {p}', flush=True)
            except Exception as e:
                print(f'[{i}/{total}] {codbar}: ERRO ({e})', flush=True)
                erros.append(codbar)

        impropias = [c for c, m in reprovadas if m == 'impropria']
        incompativeis = [c for c, m in reprovadas if m == 'descricao_incompativel']

        print()
        print('===== RESUMO =====')
        print(f'Total verificado: {total}')
        print(f'Impropias encontradas e movidas pra quarentena: {len(impropias)} -> {impropias}')
        print(f'Nao correspondem a descricao cadastrada, movidas pra quarentena: {len(incompativeis)} -> {incompativeis}')
        print(f'Erros de classificacao (permitidas por padrao, nao tocadas): {len(erros)} -> {erros}')
        if reprovadas:
            print()
            print('Revise os casos acima na aba "Quarentena" do painel (/configuracoes) antes de')
            print('confirmar ou liberar cada um.')


if __name__ == '__main__':
    main()
