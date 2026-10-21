"""Utilitários de texto sem dependência do Flask (importável por app.py e por scripts)."""
import re

# Mojibake de "dupla codificação": um texto UTF-8 foi lido como Latin-1/Windows-1252 e gravado de
# novo em UTF-8. 'LOÇÃO' vira 'LOÃ\x87Ã\x83O', 'ÓLEO' vira 'Ã\x93LEO', 'FARMÁCIA' vira 'FARMÃ\x81CIA'.
# É REVERSÍVEL (os bytes originais ainda estão lá): cada sequência "byte inicial UTF-8 + bytes de
# continuação" lida como Latin-1 é recodificada de volta. Só sequências que formam UTF-8 válido são
# tocadas — qualquer outro texto passa intacto (quem já está certo não muda).
_CONT = '\u0080-¿€‚ƒ„…†‡ˆ‰Š‹ŒŽ‘’“”•–—˜™š›œžŸ'
_MOJIBAKE_RX = re.compile(
    rf'[Â-ß][{_CONT}]|[à-ï][{_CONT}]{{2}}|[ð-ô][{_CONT}]{{3}}'
)


def _para_bytes(trecho):
    """Chars do trecho -> bytes, aceitando os caracteres que o Windows-1252 põe no lugar dos
    controles C1 (ex.: '‡' no lugar de \\x87)."""
    out = bytearray()
    for ch in trecho:
        o = ord(ch)
        if o < 256:
            out.append(o)
        else:
            out += ch.encode('cp1252')
    return bytes(out)


def reparar_mojibake(texto):
    """Desfaz a dupla codificação UTF-8 -> Latin-1 -> UTF-8. Devolve o texto igual se nada a
    reparar (ou se a sequência não decodifica como UTF-8 válido)."""
    if not texto or not isinstance(texto, str):
        return texto

    def _fix(m):
        try:
            return _para_bytes(m.group(0)).decode('utf-8')
        except (UnicodeError, ValueError):
            return m.group(0)

    # até 3 passadas: alguns textos foram codificados duas vezes seguidas (ex.: 'CÃ\x83Â\x81PSULAS')
    for _ in range(3):
        novo = _MOJIBAKE_RX.sub(_fix, texto)
        if novo == texto:
            break
        texto = novo
    return texto


def tem_controle_c1_ou_substituicao(texto):
    """True se sobrou U+FFFD (perda real de dado) ou controle C1 solto (U+0080–U+009F)."""
    return any(ch == '�' or 0x80 <= ord(ch) <= 0x9F for ch in (texto or ''))
