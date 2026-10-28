"""Bounded offline lexical freshness screening; never a semantic classifier."""
from collections import Counter, defaultdict, deque
from difflib import SequenceMatcher
import re


def normalize(text):
    return re.sub(r'\s+', '', text)


def passages(record, source_id):
    """Inspect every rich-text unit, including content not delivered to a model."""
    for section in record['sections']:
        for unit in section['units']:
            text = unit.get('value', {}).get('normalized_text', '')
            if unit['kind'] == 'rich_text' and text:
                yield {'source_id': source_id, 'section': section['ordinal'],
                       'unit': unit['ordinal'], 'section_name': section['source_metadata'].get('name', ''),
                       'text': text}


def paragraphs(passage):
    offset = 0
    for line in passage['text'].splitlines(keepends=True):
        text = line.rstrip('\r\n')
        if text:
            yield {k: v for k, v in passage.items() if k != 'text'} | {
                'start': offset, 'end': offset+len(text), 'text': text,
                'normalized': normalize(text)}
        offset += len(line)


class Matcher:
    """Aho-Corasick exact matching with duplicate-pattern occurrence accounting."""
    def __init__(self, patterns):
        self.edges, self.fail, self.output = [{}], [0], [[]]
        for pattern in sorted(set(patterns)):
            if not pattern:
                raise ValueError('Empty pattern')
            node = 0
            for c in pattern:
                if c not in self.edges[node]:
                    self.edges[node][c] = len(self.edges)
                    self.edges.append({}); self.fail.append(0); self.output.append([])
                node = self.edges[node][c]
            self.output[node].append(pattern)
        queue = deque(self.edges[0].values())
        while queue:
            parent = queue.popleft()
            for c, child in self.edges[parent].items():
                queue.append(child)
                fallback = self.fail[parent]
                while fallback and c not in self.edges[fallback]:
                    fallback = self.fail[fallback]
                self.fail[child] = self.edges[fallback].get(c, 0)
                self.output[child].extend(self.output[self.fail[child]])

    def find(self, text):
        node = 0
        for end, c in enumerate(text, 1):
            while node and c not in self.edges[node]:
                node = self.fail[node]
            node = self.edges[node].get(c, 0)
            for pattern in self.output[node]:
                yield pattern, end-len(pattern), end


def grams(text):
    return {text[i:i+5] for i in range(max(0, len(text)-4))}


def screen(candidate_records, historical_records):
    """Exact >=12-character paragraphs and bounded near comparisons, with locators.

    Whitespace normalization is for discovery only. Match offsets are explicitly
    normalized offsets, not citation offsets. Canonical quoting is a separate task.
    A no-hit result cannot prove semantic/story-family independence.
    """
    new = [p for cid, record in candidate_records.items() for u in passages(record, cid)
           for p in paragraphs(u) if len(p['normalized']) >= 12]
    old_units = [u for cid, record in historical_records.items() for u in passages(record, cid)]
    pattern_rows = defaultdict(list)
    for p in new:
        pattern_rows[p['normalized']].append(p)
    matcher = Matcher(pattern_rows)
    exact = []
    for u in old_units:
        text = normalize(u['text'])
        for pattern, start, end in matcher.find(text):
            exact.append({'pattern': pattern, 'new_occurrences': pattern_rows[pattern],
                          'old_occurrence': {k: v for k, v in u.items() if k != 'text'},
                          'old_normalized_start': start, 'old_normalized_end': end})
    old = [p for u in old_units for p in paragraphs(u) if len(p['normalized']) >= 40]
    index = defaultdict(list)
    for i, p in enumerate(old):
        for g in grams(p['normalized']):
            index[g].append(i)
    near, compared = [], 0
    for p in new:
        text = p['normalized']
        if len(text) < 40:
            continue
        counts = Counter(i for g in grams(text) for i in index.get(g, []))
        for i, _ in counts.most_common(12):
            q = old[i]
            if text == q['normalized']:
                continue
            compared += 1
            sm = SequenceMatcher(None, text, q['normalized'], autojunk=False)
            ratio, longest = sm.ratio(), sm.find_longest_match().size
            if (min(len(text), len(q['normalized'])) >= 60 and ratio >= .80) or longest >= 90:
                near.append({'new': p, 'old': q, 'ratio': ratio, 'longest_contiguous': longest})
    return {'scope': 'ALL_RICH_TEXT_IN_FULL_CANONICAL_NOT_JUST_DELIVERY',
            'candidate_sources': len(candidate_records), 'historical_sources': len(historical_records),
            'candidate_paragraphs_ge12': len(new), 'historical_paragraphs_ge40': len(old),
            'exact_matches': exact, 'near_matches': near, 'near_comparisons': compared,
            'method': 'Exact whitespace-normalized full lines >=12; near: char5gram top12, min60 ratio>=.80 or contiguous>=90. No embedding.',
            'limits': 'Shorter lines, cross-line substrings not themselves a candidate line, and semantic paraphrases are not proven absent. Family judgments required.'}
