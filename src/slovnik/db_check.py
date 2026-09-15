"""Read-only audit of a vocabulary database (per-language).

Checks every entry of one language (``cs`` or ``en``) for spelling,
language rules (Czech se/si + prepositions, or English rules), field
mix-ups (Cyrillic in the headword), whitespace glitches and duplicates —
and REPORTS everything without changing a single row (no auto-correction
by design).

Usage:
    python -m slovnik.db_check [db_path] [--lang cs|en]
                                [--json out.json] [--no-spell] [--strict]

Exit codes: 0 = clean run, 1 = issues found (only with --strict),
2 = could not open the DB.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .analyzers import analyzer
from .czech import KIND_LABELS
from .db import iter_words
from .lang import get_language
from .spellcheck import SpellChecker

from .english import en_spellchecker

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "slovnik.db"

_CYRILLIC_RE = re.compile(r"[\u0400-\u04FF]")
_CZECH_DIACRITICS_RE = re.compile(r"[řěůŘĚŮ]")
_TEXT_FIELDS = ("cs", "uk", "ru", "pos", "note")  # Word attribute names
_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}


def check_db(db_path: str, checker: SpellChecker | None = None,
             lang: str = "cs") -> dict:
    """Audit every word of ``lang``; return a JSON-serializable report dict.

    Opens the DB read-only in spirit: only SELECTs are issued and the
    file is never created or modified (a missing file raises instead).
    """
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"База даних не знайдена: {path}")

    head = get_language(lang).csv_headword  # "cs" | "en" — user-facing name
    words = list(iter_words(db_path, lang=lang))

    # Duplicate detection (case-insensitive; the UNIQUE index normally
    # prevents these, but CSV imports may have bypassed it historically).
    seen: dict[str, list[int]] = {}
    for w in words:
        seen.setdefault(w.cs.strip().lower(), []).append(w.id)
    dup_ids = {wid for ids in seen.values() if len(ids) > 1 for wid in ids}

    analyze = analyzer(lang)  # Czech or English headword rules
    report_words: list[dict] = []
    counts = {"error": 0, "warn": 0, "info": 0}
    for w in words:
        issues: list[dict] = []

        def add(kind: str, severity: str, message: str) -> None:
            issues.append({"kind": kind, "severity": severity,
                           "message": message})
            counts[severity] += 1

        if not w.cs.strip():
            add("cs", "error", f"Порожнє поле «{head}».")
        else:
            if _CYRILLIC_RE.search(w.cs):
                add("alphabet", "error",
                    f"Кирилиця в полі «{head}» — схоже, переклад записано "
                    "не в тому полі.")
            if w.cs != w.cs.strip() or "  " in w.cs:
                add("whitespace", "warn", f"Зайві пробіли в полі «{head}».")
        for field in ("uk", "ru"):
            val = getattr(w, field)
            if val and _CZECH_DIACRITICS_RE.search(val):
                add("alphabet", "warn",
                    f"Чеські діакритики (ř/ě/ů) в полі «{field}».")
        for field in _TEXT_FIELDS:
            val = getattr(w, field)
            if val and (val != val.strip() or "  " in val):
                add("whitespace", "warn",
                    f"Зайві пробіли в полі «{field}».")
        if w.id in dup_ids:
            add("duplicate", "error",
                f"Дублікат «{w.cs}» (без урахування регістру).")
        if not w.uk.strip() and not w.ru.strip():
            add("translation", "info", "Немає перекладу (uk і ru порожні).")

        if w.cs.strip():
            for issue in analyze(w.cs, checker):
                add(issue.kind, issue.severity, issue.message)

        if issues:
            report_words.append({
                "id": w.id, "cs": w.cs,
                "issues": sorted(
                    issues, key=lambda i: _SEVERITY_ORDER.get(i["severity"], 9)
                ),
            })

    report_words.sort(key=lambda e: e["id"])
    return {
        "db": str(path),
        "lang": lang,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "words_checked": len(words),
        "counts": {**counts, "words_with_issues": len(report_words)},
        "words": report_words,
    }


def print_report(report: dict) -> None:
    counts = report["counts"]
    lang_note = {"cs": "", "en": " (англійський)"}.get(report.get("lang"), "")
    print(f"База: {report['db']}{lang_note}")
    print(f"Перевірено слів: {report['words_checked']}")
    if not report["words"]:
        print("✅ Проблем не знайдено.")
        return
    print()
    for entry in report["words"]:
        print(f"#{entry['id']}  «{entry['cs']}»")
        for issue in entry["issues"]:
            icon = {"error": "🔴", "warn": "🟡", "info": "🔵"}.get(
                issue["severity"], "•")
            label = KIND_LABELS.get(issue["kind"], issue["kind"])
            print(f"   {icon} [{label}] {issue['message']}")
    print()
    print(f"Разом: {counts['error']} errors, {counts['warn']} warnings, "
          f"{counts['info']} info — у {counts['words_with_issues']} словах")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="slovnik.db_check",
        description="Перевірити всю базу на помилки (лише читання, "
                    "без автоматичних виправлень).",
    )
    parser.add_argument("db", nargs="?", default=str(DEFAULT_DB),
                        help="шлях до SQLite-бази (типово data/slovnik.db)")
    parser.add_argument("--lang", default="cs", choices=["cs", "en"],
                        help="мова словаря: cs (типово) або en")
    parser.add_argument("--json", metavar="PATH",
                        help="додатково зберегти звіт у JSON")
    parser.add_argument("--no-spell", action="store_true",
                        help="пропустити перевірку орфографії (швидко, "
                             "без завантаження словника)")
    parser.add_argument("--strict", action="store_true",
                        help="повернути код 1, якщо є хоч якісь зауваження")
    args = parser.parse_args(argv)

    checker = None
    if not args.no_spell:
        try:
            checker = (en_spellchecker() if args.lang == "en"
                       else SpellChecker())
        except Exception as exc:  # dict missing/corrupt — keep auditing
            print(f"⚠ Словник недоступний ({exc}); орфографію пропущено.",
                  file=sys.stderr)

    try:
        report = check_db(args.db, checker, lang=args.lang)
    except FileNotFoundError as exc:
        print(f"Помилка: {exc}", file=sys.stderr)
        return 2

    print_report(report)

    if args.json:
        Path(args.json).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"JSON-звіт: {args.json}")

    counts = report["counts"]
    if args.strict and (counts["error"] or counts["warn"]):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
