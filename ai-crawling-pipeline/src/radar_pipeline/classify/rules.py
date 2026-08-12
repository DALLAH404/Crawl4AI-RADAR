"""News classification rules — verbatim port of codigo.gs classificar_().

Priority chain:
    1. Launch keyword + aftermarket-part term -> Lancamento / Alto
    2. Launch keyword only -> Institucional / Medio
    3. TEMAS_KW keywords (priority-ordered, first match wins)
    4. Fallback -> Atualizacao / Baixo

Out-of-scope filter (FORA_ESCOPO) runs first.
"""

from .keywords import (
    FORA_ESCOPO,
    KW_LANCAMENTO,
    KW_PECA_AUTOMOTIVA,
    TEMAS_KW,
)


def eh_fora_de_escopo(texto: str) -> bool:
    t = texto.lower()
    return any(k in t for k in FORA_ESCOPO)


def eh_aftermarket_real(texto: str) -> bool:
    t = texto.lower()
    return any(k in t for k in KW_PECA_AUTOMOTIVA)


def eh_lancamento(texto: str) -> bool:
    t = texto.lower()
    return any(k in t for k in KW_LANCAMENTO)


def classificar(texto: str) -> tuple[str, str]:
    t = texto.lower()

    if eh_lancamento(t):
        if eh_aftermarket_real(t):
            return ("Lancamento", "Alto")
        return ("Institucional", "Medio")

    for event_type, alert_level, kw_list in TEMAS_KW:
        if any(k in t for k in kw_list):
            return (event_type, alert_level)

    return ("Atualizacao", "Baixo")


def classify_article(title: str, description: str = "") -> tuple[str, str, bool]:
    text = f"{title} {description}"
    if eh_fora_de_escopo(text):
        return ("Atualizacao", "Baixo", False)
    event_type, alert_level = classificar(text)
    is_launch = event_type == "Lancamento"
    return (event_type, alert_level, is_launch)
