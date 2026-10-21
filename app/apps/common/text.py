"""Мелкие текстовые помощники, общие для приложений."""


def plural(number: int, one: str, few: str, many: str) -> str:
    """1 проект, 2 проекта, 5 проектов."""
    tail = number % 100
    if 11 <= tail <= 14:
        return many
    tail %= 10
    if tail == 1:
        return one
    if 2 <= tail <= 4:
        return few
    return many
