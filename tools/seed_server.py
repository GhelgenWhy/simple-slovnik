"""Seed (or top up) a sync server from a local database — full upload.

This is the command-line equivalent of the «⬆⬆ Надіслати все» button: it
uploads every word of a language, including tombstones, keeping the
original timestamps and authorship. Use it for a brand-new or restored
server; for day-to-day sync use the app's Push/Pull buttons.

Safety rails:

* prints the pre-flight counts (words, tombstones, pending changes) and
  requires ``--yes`` before sending anything;
* refuses to talk to a server whose identity differs from the one stored
  in the local DB unless ``--force`` is given (same rule as the UI);
* verifies afterwards by pulling and comparing the live-word counts.

Usage::

    python tools/seed_server.py --db data/slovnik.db \
        --url https://HOST.ts.net:8443 --token-file /path/to/token.txt
    python tools/seed_server.py ... --langs cs,en --yes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "src"))

from slovnik import db                                  # noqa: E402
from slovnik.sync import SyncClient, SyncError          # noqa: E402


def _counts(db_path: str, langs: list[str]) -> dict[str, dict]:
    out = {}
    for lang in langs:
        live = db.count_words(db_path, lang=lang)
        tombstones = sum(1 for w in db.iter_words(db_path, include_deleted=True,
                                                  lang=lang) if w.deleted)
        out[lang] = {"live": live, "tombstones": tombstones,
                     "pending": db.count_dirty(db_path, lang)}
    return out


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(REPO / "data" / "slovnik.db"))
    parser.add_argument("--url", required=True)
    parser.add_argument("--token-file", help="file containing the token")
    parser.add_argument("--token", help="token (visible in the process list!)")
    parser.add_argument("--langs", default="cs,en")
    parser.add_argument("--yes", action="store_true",
                        help="skutečně odeslat (bez tohoto jen vypíše plán)")
    parser.add_argument("--force", action="store_true",
                        help="povolit i když se liší identita serveru")
    args = parser.parse_args(argv)

    token = args.token or ""
    if args.token_file:
        token = Path(args.token_file).read_text(encoding="utf-8").strip()
    if not token:
        print("CHYBA: chybí --token nebo --token-file", file=sys.stderr)
        return 2

    langs = [lang.strip() for lang in args.langs.split(",") if lang.strip()]
    db.init_db(args.db)

    print(f"DB:     {args.db}")
    print(f"Server: {args.url}")
    for lang, counts in _counts(args.db, langs).items():
        print(f"  [{lang}] ke slovům: {counts['live']}, "
              f"tombstonů: {counts['tombstones']}, "
              f"čeká na push: {counts['pending']}")
    if not args.yes:
        print("\nNic se neodeslalo — přidejte --yes pro skutečné nahrání.")
        return 0

    exit_code = 0
    for lang in langs:
        client = SyncClient(args.db, args.url, token, lang=lang)
        try:
            info = client.info()
        except SyncError as exc:
            print(f"[{lang}] CHYBA připojení: {exc}", file=sys.stderr)
            return 1
        stored = db.get_sync_state(args.db, "server_uuid")
        remote = info.get("server_uuid")
        if (stored and remote and stored != remote and not args.force
                and not info.get("legacy")):
            print(f"[{lang}] CHYBA: server má jinou identitu než uložená "
                  f"({stored[:8]}… vs {remote[:8]}…). "
                  "Použijte --force, pokud je to opravdu zamýšlený server.",
                  file=sys.stderr)
            exit_code = 1
            continue
        print(f"[{lang}] nahrávám…")
        result = client.full_upload()
        if not result.ok:
            print(f"[{lang}] CHYBA: {result.error}", file=sys.stderr)
            exit_code = 1
            continue
        print(f"[{lang}] odesláno {result.pushed} řádků, "
              f"čeká {result.pending_left}")
        verify = client.pull()
        if not verify.ok:
            print(f"[{lang}] CHYBA ověření: {verify.error}", file=sys.stderr)
            exit_code = 1
            continue
        local_live = db.count_words(args.db, lang=lang)
        print(f"[{lang}] ověřeno: lokálně {local_live} živých slov, "
              f"staženo {verify.pulled} řádků")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
