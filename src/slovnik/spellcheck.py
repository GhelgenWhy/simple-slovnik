"""Czech spellchecker.

Supports two backends:
- "hunspell" (preferred when pyhunspell is installed)
- "pure" (pure-Python: cs_CZ.dic + Levenshtein/difflib for suggestions)

The constructor picks a backend automatically unless one is forced.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

try:
    import hunspell as _hunspell  # type: ignore
    _HAS_HUNSPELL = True
except ImportError:
    _HAS_HUNSPELL = False

try:
    import Levenshtein as _lev  # type: ignore
    _HAS_LEVENSHTEIN = True
except ImportError:
    _HAS_LEVENSHTEIN = False

import unicodedata as _unicodedata


def _strip_diacritics(s: str) -> str:
    """Return ``s`` with combining diacritics removed (e.g. ``'kočka'`` → ``'kocka'``)."""
    nfkd = _unicodedata.normalize("NFKD", s)
    return "".join(c for c in nfkd if not _unicodedata.combining(c))

DEFAULT_DICT_DIR = Path(__file__).resolve().parent.parent.parent / "dicts" / "cs_CZ"


class SpellChecker:
    def __init__(
        self,
        dict_dir: Optional[Path] = None,
        backend: Literal["auto", "hunspell", "pure"] = "auto",
    ) -> None:
        self.dict_dir = Path(dict_dir) if dict_dir else DEFAULT_DICT_DIR
        self.aff_path = self.dict_dir / "cs_CZ.aff"
        self.dic_path = self.dict_dir / "cs_CZ.dic"

        if backend == "auto":
            if _HAS_HUNSPELL and self.aff_path.exists() and self.dic_path.exists():
                backend = "hunspell"
            elif self.dic_path.exists():
                backend = "pure"
            else:
                backend = "pure"  # will return no-op results

        self.backend = backend
        self._hunspell_obj = None
        self._pure_words: frozenset[str] = frozenset()
        self._pure_word_list: list[str] = []

        if self.backend == "hunspell":
            self._hunspell_obj = _hunspell.HunSpell(
                str(self.dic_path), str(self.aff_path)
            )
        elif self.backend == "pure":
            self._pure_words, self._pure_word_list = _load_pure_dict(self.dic_path)

    def is_correct(self, word: str) -> bool:
        word = (word or "").strip()
        if not word:
            return True
        if self.backend == "hunspell" and self._hunspell_obj is not None:
            return self._hunspell_obj.spell(word)
        if self.backend == "pure":
            return word in self._pure_words
        return True  # graceful no-op

    def suggest(self, word: str, limit: int = 8) -> list[str]:
        word = (word or "").strip()
        if not word:
            return []
        if self.backend == "hunspell" and self._hunspell_obj is not None:
            return list(self._hunspell_obj.suggest(word))[:limit]
        if self.backend == "pure":
            return _pure_suggest(word, self._pure_word_list, limit)
        return []


def _load_pure_dict(dic_path: Path) -> tuple[frozenset[str], list[str]]:
    """Read cs_CZ.dic; return (set, list) of base forms.

    Line 1 is the entry count; subsequent lines are base forms, sometimes
    followed by ``/FLAGS`` or ``/FLAGS affix_data``. Strip everything from
    the first ``/`` onward and skip blank lines.
    """
    if not dic_path.exists():
        return frozenset(), []
    words: list[str] = []
    with open(dic_path, "r", encoding="utf-8", errors="replace") as f:
        for i, line in enumerate(f):
            if i == 0:
                continue  # entry count
            line = line.strip()
            if not line:
                continue
            base = line.split("/", 1)[0].strip()
            if base:
                words.append(base)
    return frozenset(words), words


def _pure_suggest(word: str, vocab: list[str], limit: int) -> list[str]:
    """Suggest up to ``limit`` closest words by edit similarity.

    Scoring (lower is better):
      1. Levenshtein on the diacritic-stripped, lowercased strings — this
         rewards candidates that match the input once diacritics are
         ignored (e.g. ``'kocka'`` → ``'kočka'`` ranks first).
      2. Levenshtein on the original strings — final disambiguation.
      3. Alphabetical — deterministic tiebreaker.

    Pre-filters vocab by first character (diacritic-insensitive) and
    length (±1). With ~260k entries this reduces candidates to a few
    thousand, making even the ``difflib`` fallback path fast enough for
    interactive use.
    """
    if not vocab:
        return []
    target_norm = _strip_diacritics(word).lower()
    target_first = target_norm[:1] if target_norm else ""
    wlen = len(word)
    # Pre-filter: same first diacritic-insensitive char and length within ±1.
    cands = [
        w for w in vocab
        if _strip_diacritics(w)[:1].lower() == target_first
        and abs(len(w) - wlen) <= 1
    ]
    if _HAS_LEVENSHTEIN:
        scored = [
            (
                _lev.distance(target_norm, _strip_diacritics(w).lower()),
                _lev.distance(word, w),
                w,
            )
            for w in cands
        ]
    else:
        import difflib

        # difflib has no real edit-distance; approximate with 1-ratio so
        # larger ratio => smaller score (matching the Levenshtein order).
        scored = [
            (
                int(round((1.0 - difflib.SequenceMatcher(
                    None, target_norm, _strip_diacritics(w).lower()
                ).ratio()) * 10)),
                int(round((1.0 - difflib.SequenceMatcher(
                    None, word, w
                ).ratio()) * 10)),
                w,
            )
            for w in cands
        ]
    scored.sort(key=lambda x: (x[0], x[1], x[2]))
    return [w for _, _, w in scored[:limit]]
