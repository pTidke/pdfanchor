"""Keyword search (BM25) over a list of texts, used to rank chunks against a question."""

import math
import re
from collections import Counter


def tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class BM25:
    def __init__(self, docs: list[str], k1: float = 1.2, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.counts = [Counter(tokens(doc)) for doc in docs]
        self.lengths = [sum(c.values()) for c in self.counts]
        self.average = sum(self.lengths) / len(docs) if docs else 0.0
        frequency: Counter[str] = Counter()
        for c in self.counts:
            frequency.update(c.keys())
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in frequency.items()}

    def score(self, index: int, query: list[str]) -> float:
        counts = self.counts[index]
        norm = 1 - self.b + self.b * (self.lengths[index] / self.average if self.average else 0.0)
        total = 0.0
        for term in query:
            tf = counts.get(term, 0)
            if tf:
                total += self.idf[term] * tf * (self.k1 + 1) / (tf + self.k1 * norm)
        return total

    def rank(self, query: str) -> list[int]:
        words = tokens(query)
        scores = [self.score(i, words) for i in range(len(self.counts))]
        return sorted(range(len(scores)), key=lambda i: (-scores[i], i))
