"""Утилиты нормализации текста."""


def normalize_name(name: str) -> str:
    """Нормализация ФИО для сравнения.

    - strip + lowercase
    - ё → е (и Ё → Е, но после lower уже не нужно)
    """
    return name.strip().lower().replace("ё", "е")


def pluralize_years(n: int) -> str:
    """Русское склонение: 1 год, 2 года, 5 лет."""
    if 11 <= n % 100 <= 19:
        return f"{n} лет"
    mod10 = n % 10
    if mod10 == 1:
        return f"{n} год"
    if 2 <= mod10 <= 4:
        return f"{n} года"
    return f"{n} лет"
