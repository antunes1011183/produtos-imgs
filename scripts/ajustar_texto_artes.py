"""Aplica os novos tamanhos de texto nas artes horizontais JÁ GERADAS, sem chamar IA (custo zero).

Por que dá pra fazer: o painel colorido da arte é uma cor SÓLIDA com o texto desenhado por cima, e
o layout do texto é determinístico (posições calculadas a partir da altura). Então, em vez de
regerar a cena:
  1. acha a barra de sublinhado (âncora) e, pelas fórmulas do layout, descobre quantas linhas tem a
     marca e o resto do nome;
  2. recorta cada linha do título e do texto menor, reduz pro novo tamanho (título -50%, texto menor
     -25%) e recoloca no lugar que a arte nova usaria;
  3. sobe o sublinhado e a frase de baixo (que mantêm o tamanho) pro novo lugar.
Nada da cena/foto do produto é regerado.

Uso (no servidor, dentro da pasta do projeto):
    venv/bin/python scripts/ajustar_texto_artes.py                    # simulação: só diz o que faria
    venv/bin/python scripts/ajustar_texto_artes.py --apenas 789...    # um EAN
    venv/bin/python scripts/ajustar_texto_artes.py --aplicar          # grava (backup antes)
    venv/bin/python scripts/ajustar_texto_artes.py --arquivo X.webp --saida Y.png   # teste fora da pasta
Idempotente: artes que já estão no tamanho novo são puladas. Só toca <ean>.webp (horizontal).
"""
import argparse
import os
import shutil
import sys
import time

import numpy as np
from PIL import Image, ImageChops, ImageDraw
from scipy import ndimage

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import app as A  # noqa: E402

LIMIAR = 24          # distância (soma dos canais) da cor do painel pra contar como "texto"
FATOR_TITULO = 0.5   # título maior (marca) -50%
FATOR_TEXTO = 0.75   # texto menor (resto do nome) -25%
FILTRO = Image.LANCZOS  # filtro da redução dos recortes de texto


def layout(H, novo):
    """Constantes do layout de compor_texto_na_arte — antigas (novo=False) ou novas (novo=True)."""
    ft, fr = (FATOR_TITULO, FATOR_TEXTO) if novo else (1.0, 1.0)
    return {
        'y0': int(H * 0.13),
        'f_m': max(10, int(H * 0.095 * ft)), 'f_r': max(10, int(H * 0.048 * fr)),
        'p_m': int(H * 0.1 * ft), 'p_r': int(H * 0.052 * fr),
        'gap': int(H * 0.008), 'sob': int(H * 0.015), 'acc': max(4, int(H * 0.01)),
    }


def y_sublinhado(L, nb, nr):
    return L['y0'] + L['p_m'] * nb + L['gap'] + L['p_r'] * nr + L['sob']


def ajustar(img):
    """Devolve (imagem_nova | None, mensagem)."""
    W, H = img.size
    if (W, H) != (A.ARTE_LARGURA_HORIZONTAL, A.ARTE_ALTURA_HORIZONTAL):
        return None, f'tamanho {W}x{H} (não é a arte horizontal)'
    rgb = np.array(img.convert('RGB')).astype(np.int32)

    # região segura: o painel é cor sólida até a borda mais à esquerda da curva
    molde = Image.new('RGBA', (W, H))
    Xc = int(A._desenhar_painel_curvo(ImageDraw.Draw(molde), W, H, (0, 0, 0, 255))) - 4   # borda MAIS À ESQUERDA da curva
    pm = np.array(molde.getchannel('A')) > 0
    borda_linha = np.where(pm.any(axis=1), W - 1 - np.argmax(pm[:, ::-1], axis=1), 0)       # borda real, linha a linha
    R = np.arange(W)[None, :] < (borda_linha[:, None] - 5)                                 # dentro do painel sólido
    margin = int(W * 0.06)
    P = np.median(rgb[H - 70:H - 10, 10:margin].reshape(-1, 3), axis=0)
    diff = np.abs(rgb - P).sum(axis=2)
    texto = (diff > LIMIAR) & R      # palavra longa pode passar da borda mínima: usa a borda da linha

    # âncora: barra de sublinhado = linhas com a faixa [margin, margin+0.2W) quase toda preenchida
    larg = int(W * 0.2)
    cheia = texto[:int(H * 0.7), margin:margin + larg].sum(axis=1) >= 0.85 * larg
    ys = np.where(cheia)[0]
    if not len(ys):
        return None, 'sublinhado não encontrado'
    y_u = int(ys[0])

    Lv, Ln = layout(H, False), layout(H, True)
    achou_v = [(nb, nr) for nb in (1, 2) for nr in (0, 1, 2) if abs(y_sublinhado(Lv, nb, nr) - y_u) <= 2]
    achou_n = [(nb, nr) for nb in (1, 2) for nr in (0, 1, 2) if abs(y_sublinhado(Ln, nb, nr) - y_u) <= 2]
    if achou_n and not achou_v:
        return None, 'já está no tamanho novo'
    if achou_v and achou_n:
        return None, 'ambígua (não dá pra distinguir antiga de nova)'
    if len(achou_v) == 2 and achou_v[0][0] != achou_v[1][0]:
        # 2 linhas de título sem resto  x  1 linha de título + 2 de resto: o sublinhado cai a 2px nos dois casos.
        # Desempata pela espessura do traço na zona logo abaixo da 1ª linha: haste de título ~7px, de texto menor ~2px.
        ya, yb = Lv['y0'] + Lv['p_m'] + 22, y_u - 14
        corridas = []
        for y in range(ya, yb):
            linha = texto[y, :Xc]
            if not linha.any():
                continue
            borda = np.diff(np.concatenate(([0], linha.astype(np.int8), [0])))
            corridas += list(np.where(borda == -1)[0] - np.where(borda == 1)[0])
        if len(corridas) >= 8:
            espesso = float(np.median(corridas)) >= 4.5
            achou_v = [c for c in achou_v if c[0] == (2 if espesso else 1)]
    if len(achou_v) != 1:
        return None, f'layout não reconhecido (sublinhado em y={y_u})'
    nb, nr = achou_v[0]

    # fim do bloco da frase de baixo
    linhas_txt = np.where(texto[y_u:int(H * 0.75), :].any(axis=1))[0]
    y_fim = y_u + int(linhas_txt.max()) + 1
    y_ini_apagar = Lv['y0'] - 8

    alvo = rgb.copy()
    zona = R[y_ini_apagar:y_fim + 4]
    alvo[y_ini_apagar:y_fim + 4][zona] = P.astype(np.int32)          # apaga texto antigo (cor sólida)

    # bloco sublinhado + frase de baixo: copiado como está e subido pro novo lugar
    y_u_novo = y_sublinhado(Ln, nb, nr)
    bloco = rgb[y_u - 2:y_fim + 3, :Xc].copy()
    alvo[y_u_novo - 2:y_u_novo - 2 + bloco.shape[0], :Xc] = bloco

    # linhas do título e do texto menor: recorte (alpha = cobertura do branco), reduz e recoloca
    pad = 8
    branco_menos_P = (255 - P).clip(min=1)
    cobertura = (((rgb - P) / branco_menos_P).mean(axis=2)).clip(0, 1) * R
    XL = int(borda_linha[Lv['y0']:y_u].max()) - 5

    tela = Image.new('L', (W, H), 0)

    # Cada letra pertence a UMA linha: rotula os componentes conexos de tinta e atribui cada um à linha
    # cuja origem é a última acima do topo dele. Assim a cauda do "y" (ligada ao corpo) fica com o título
    # e o acento solto de "Á" fica com o texto de baixo, mesmo que as caixas das linhas se sobreponham.
    origens_v = [(Lv['y0'] + Lv['p_m'] * k, Lv['f_m']) for k in range(nb)] +                 [(Lv['y0'] + Lv['p_m'] * nb + Lv['gap'] + Lv['p_r'] * j, Lv['f_r']) for j in range(nr)]
    origens_n = [(Ln['y0'] + Ln['p_m'] * k, Ln['f_m']) for k in range(nb)] +                 [(Ln['y0'] + Ln['p_m'] * nb + Ln['gap'] + Ln['p_r'] * j, Ln['f_r']) for j in range(nr)]
    zona = np.zeros_like(texto)
    zona[Lv['y0'] - 8:y_u - 3] = texto[Lv['y0'] - 8:y_u - 3]
    rotulos, n = ndimage.label(zona, structure=np.ones((3, 3)))
    dono_de = np.full(n + 1, -1, dtype=np.int32)
    for i, sl in enumerate(ndimage.find_objects(rotulos), 1):
        topo = sl[0].start
        dono_de[i] = max([k for k, (o, _) in enumerate(origens_v) if o <= topo + 2] or [0])
    dono = dono_de[rotulos]

    def colar_linha(k):
        (o_ant, f_ant), (o_novo, f_novo) = origens_v[k], origens_n[k]
        cov = cobertura * ndimage.binary_dilation(dono == k, iterations=2)     # inclui o halo da compressão
        topo = 2                                                                  # folga acima da origem (acentos)
        alt = int(1.35 * f_ant) + topo                                            # vai até o fim da cauda dos descendentes
        recorte = (cov[o_ant - topo:o_ant - topo + alt, margin - pad:XL] * 255).astype(np.uint8)
        s = f_novo / f_ant
        im = Image.fromarray(recorte).resize(
            (max(1, round(recorte.shape[1] * s)), max(1, round(recorte.shape[0] * s))), FILTRO)
        x, y = margin - round(pad * s), o_novo - round(topo * s)
        regiao = tela.crop((x, y, x + im.width, y + im.height))
        tela.paste(ImageChops.lighter(regiao, im), (x, y))

    for k in range(len(origens_v)):
        colar_linha(k)

    a = np.array(tela).astype(np.float32)[:, :, None] / 255.0
    cena = alvo.astype(np.float32)
    mascara = np.zeros((H, W, 1), np.float32)
    mascara[:, :Xc] = 1.0
    alvo = (cena * (1 - a * mascara) + 255.0 * a * mascara).clip(0, 255).astype(np.uint8)
    return Image.fromarray(alvo, 'RGB'), f'ok (marca {nb} linha(s), resto {nr}; sublinhado {y_u}→{y_u_novo})'


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--aplicar', action='store_true')
    ap.add_argument('--apenas')
    ap.add_argument('--arquivo')
    ap.add_argument('--saida')
    ap.add_argument('--backup', default=os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'instance', 'artes_bak_tamanhos'))
    args = ap.parse_args()

    if args.arquivo:
        novo, msg = ajustar(Image.open(args.arquivo))
        print(msg)
        if novo and args.saida:
            novo.save(args.saida)
        return

    pasta = A.ARTES_FOLDER
    nomes = sorted(n for n in os.listdir(pasta) if n.endswith('.webp') and '_vertical' not in n)
    if args.apenas:
        nomes = [n for n in nomes if n.startswith(args.apenas)]
    contagem, ajustadas = {}, 0
    if args.aplicar:
        os.makedirs(args.backup, exist_ok=True)
    for n in nomes:
        caminho = os.path.join(pasta, n)
        try:
            novo, msg = ajustar(Image.open(caminho))
        except Exception as e:  # uma arte ruim nunca derruba o lote
            novo, msg = None, f'erro: {e}'
        chave = msg.split(' (')[0] if novo is None else 'ajustada'
        contagem[chave] = contagem.get(chave, 0) + 1
        if novo is None:
            if not msg.startswith('já está'):
                print(f'  pulada {n}: {msg}')
            continue
        if args.aplicar:
            shutil.copy2(caminho, os.path.join(args.backup, n))
            tmp = caminho + '.tmp'
            novo.save(tmp, format='WEBP', quality=92, method=4)
            os.replace(tmp, caminho)
            ajustadas += 1
    print('resumo:', contagem, '| gravadas:' if args.aplicar else '| (simulação)', ajustadas if args.aplicar else '')


if __name__ == '__main__':
    main()
