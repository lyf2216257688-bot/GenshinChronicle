"""Offline field/analyzer seam. Stored source text is never normalized."""
from ..evidence_io import require
from .schema import LEXICAL_FIELDS

ANALYZER_VERSION = "gc-han-analyzers-1"


def _han(char):
    code = ord(char)
    return (0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF
            or 0xF900 <= code <= 0xFAFF or 0x20000 <= code <= 0x323AF)


def _runs(text):
    current = ""
    previous = None
    for char in text:
        kind = "han" if _han(char) else "word" if char.isalnum() else None
        if kind != previous or kind is None:
            if current:
                yield previous, current
            current = ""
        if kind:
            current += char
        previous = kind
    if current:
        yield previous, current


def validate_analyzers(specification):
    require(specification.get("version") == ANALYZER_VERSION, "Unsupported analyzer version")
    names = specification.get("names", [])
    require(bool(names) and len(names) == len(set(names))
            and set(names) <= {"han_unigram", "han_bigram", "word"}, "Invalid analyzer selection")
    if "word" in names:
        binding = specification.get("segmenter", {})
        require(all(isinstance(binding.get(key), str) and binding[key]
                    for key in ("implementation", "version", "configuration_sha256", "dictionary_sha256")),
                "Word analyzer requires segmenter and dictionary bindings")


def analyze_fields(fields, specification, *, segmenter=None):
    """Keep field and analyzer TF/length statistics independently addressable."""
    validate_analyzers(specification)
    require(set(fields) == set(LEXICAL_FIELDS), "Unexpected lexical fields")
    if "word" in specification["names"]:
        require(callable(segmenter), "Word analyzer requires an explicit segmenter")
    result = {}
    for field, values in fields.items():
        require(isinstance(values, list) and all(isinstance(value, str) for value in values),
                "Expected multi-value text fields")
        analyzed = {name: [] for name in specification["names"]}
        for value in values:
            for kind, run in _runs(value):
                if "han_unigram" in analyzed:
                    analyzed["han_unigram"].extend(list(run) if kind == "han" else [run.casefold()])
                if "han_bigram" in analyzed:
                    terms = [run[i:i + 2] for i in range(len(run) - 1)] if kind == "han" else [run.casefold()]
                    analyzed["han_bigram"].extend(terms)
            if "word" in analyzed:
                terms = list(segmenter(value))
                require(all(isinstance(term, str) and term and not term.isspace() for term in terms),
                        "Invalid segmenter output")
                analyzed["word"].extend(terms)
        result[field] = analyzed
    return result
