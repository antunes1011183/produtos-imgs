"""Melhoria de qualidade + remoção de fundo de foto de produto.

Por que não basta `rembg(alpha_matting=True)`: o matting do rembg "erode" 10 px da região segura do
produto antes de refinar a borda. Produto fino (caneta, haste, corpo transparente) ou foto pequena
perde exatamente essa região e some quase inteiro. Aqui o rembg entra só como máscara (sem matting) e,
quando a foto tem fundo uniforme (a maioria das fotos de varejo), o próprio fundo é usado para conferir
e recuperar o que o modelo apagou do produto.
"""
import logging

import cv2
import numpy as np
from PIL import Image

LADO_MAXIMO = 2000        # acima disso a foto é reduzida (a arte do terminal tem no máximo 1920 px)
ALVO_MENOR_LADO = 720     # foto menor que isso é ampliada antes de processar
_sessao_rembg = None


def _sessao():
    """Modelo carregado uma vez só (antes o rembg recarregava o u2net a cada imagem)."""
    global _sessao_rembg
    if _sessao_rembg is None:
        from rembg import new_session
        _sessao_rembg = new_session('u2net')
    return _sessao_rembg


def melhorar_qualidade(img):
    """Ampliação (LANCZOS) de foto pequena, redução de ruído/artefato de JPEG sem borrar borda e nitidez
    leve. Não inventa detalhe que a foto original não tem; deixa o resultado e a máscara mais limpos.
    Roda ANTES da remoção de fundo: o modelo enxerga mais detalhe e a borda sai mais precisa."""
    rgb = img.convert('RGB')
    w, h = rgb.size
    if max(w, h) > LADO_MAXIMO:
        k = LADO_MAXIMO / max(w, h)
        rgb = rgb.resize((round(w * k), round(h * k)), Image.LANCZOS)
    elif min(w, h) < ALVO_MENOR_LADO:
        k = min(4.0, ALVO_MENOR_LADO / min(w, h))
        rgb = rgb.resize((round(w * k), round(h * k)), Image.LANCZOS)
    arr = cv2.fastNlMeansDenoisingColored(np.array(rgb), None, 3, 3, 5, 15)
    suave = cv2.GaussianBlur(arr, (0, 0), 1.1)
    arr = cv2.addWeighted(arr, 1.7, suave, -0.7, 0)
    return Image.fromarray(arr)


def _mascara_rembg(rgb_img):
    """Máscara 0-255 do rembg (u2net), sem matting. Foto pequena é ampliada antes: objeto fino some em baixa resolução."""
    from rembg import remove
    w, h = rgb_img.size
    img = rgb_img
    if min(w, h) < 640:
        k = 640 / min(w, h)
        img = rgb_img.resize((round(w * k), round(h * k)), Image.LANCZOS)
    m = remove(img, session=_sessao(), only_mask=True)
    if m.size != (w, h):
        m = m.resize((w, h), Image.LANCZOS)
    return np.array(m.convert('L'))


def _fundo_uniforme(rgb):
    """(cor do fundo, é uniforme?) olhando só o anel da borda."""
    h, w = rgb.shape[:2]
    e = max(2, int(min(h, w) * 0.015))
    anel = np.concatenate([rgb[:e].reshape(-1, 3), rgb[-e:].reshape(-1, 3),
                           rgb[:, :e].reshape(-1, 3), rgb[:, -e:].reshape(-1, 3)])
    bg = np.median(anel, axis=0)
    perto = (np.abs(anel.astype(np.int16) - bg).max(axis=1) <= 12).mean()
    return bg, bool(perto >= 0.92)


def _mascara_pelo_fundo(rgb, bg, tol=12):
    """Produto = tudo que não é fundo alcançável a partir da borda (preenche buracos, ex.: corpo transparente)."""
    diff = np.abs(rgb.astype(np.int16) - bg).max(axis=2)
    obj = (diff > tol).astype(np.uint8)
    obj = cv2.morphologyEx(obj, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    _, lab = cv2.connectedComponents((1 - obj).astype(np.uint8), connectivity=4)
    toca = set(np.unique(np.concatenate([lab[0], lab[-1], lab[:, 0], lab[:, -1]]))) - {0}
    return ~np.isin(lab, list(toca))


def remover_fundo(img):
    """Devolve a imagem RGBA com o fundo transparente.

    1. rembg gera a máscara. 2. Se a foto tem fundo uniforme, calcula-se também o que "não é fundo".
    Se o rembg apagou mais de 12% disso, ele comeu parte do produto: usa-se a máscara do fundo
    (1 px pra dentro e suavizada, sem halo branco) somada à do rembg."""
    rgb_img = img.convert('RGB')
    rgb = np.array(rgb_img)
    alpha = _mascara_rembg(rgb_img).astype(np.float32) / 255.0
    bg, uniforme = _fundo_uniforme(rgb)
    if uniforme:
        pelo_fundo = _mascara_pelo_fundo(rgb, bg)
        area = pelo_fundo.sum()
        if area > 0:
            perdido = 1 - ((alpha > 0.5) & pelo_fundo).sum() / area
            if perdido > 0.12:
                logging.info(f"rembg apagou {perdido:.0%} do produto; recuperando pelo fundo uniforme.")
                c = cv2.erode(pelo_fundo.astype(np.uint8), np.ones((3, 3), np.uint8))
                c = cv2.GaussianBlur(c.astype(np.float32), (0, 0), 0.8)
                alpha = np.maximum(alpha, c)
    saida = rgb_img.convert('RGBA')
    saida.putalpha(Image.fromarray((np.clip(alpha, 0, 1) * 255).astype(np.uint8)))
    return saida
