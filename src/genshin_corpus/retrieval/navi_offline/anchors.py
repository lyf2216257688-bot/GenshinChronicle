"""Program-owned exact locations inside an explicitly delivered source fragment."""
from .common import require


def locate(text, quote):
    require(isinstance(quote, str) and bool(quote), 'Empty quotation')
    start = text.find(quote)
    require(start >= 0, 'Quotation not in delivered source')
    require(text.find(quote, start + 1) < 0, 'Ambiguous quotation; select a longer unique fragment')
    return start, start + len(quote)
