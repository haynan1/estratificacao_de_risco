"""Helpers puros de parsing, formatacao e paginacao (sem Flask, sem banco)."""

import math
import re
from datetime import datetime


def only_digits(value):
    return re.sub(r"\D", "", value or "")


def cpf_valido(value):
    return len(only_digits(value)) == 11


def format_cpf(value):
    digits = only_digits(value)[:11]
    if len(digits) != 11:
        return value or ""
    return f"{digits[:3]}.{digits[3:6]}.{digits[6:9]}-{digits[9:]}"


def date_br(value):
    return value.strftime("%d/%m/%Y") if value else ""


def parse_date(value):
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def parse_int(value):
    if value in ("", None):
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def parse_float(value):
    if value in ("", None):
        return None
    try:
        return float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None


class Page:
    """Paginacao para listas em memoria, com a mesma interface do Flask-SQLAlchemy."""

    def __init__(self, items, page, per_page, total):
        self.items = items
        self.page = page
        self.per_page = per_page
        self.total = total
        self.pages = max(1, math.ceil(total / per_page)) if per_page else 1
        self.has_prev = page > 1
        self.has_next = page < self.pages
        self.prev_num = page - 1
        self.next_num = page + 1


def paginate_list(items, page, per_page):
    total = len(items)
    page = max(1, page)
    start = (page - 1) * per_page
    return Page(items[start:start + per_page], page, per_page, total)
