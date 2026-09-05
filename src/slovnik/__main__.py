"""Entry point: python -m slovnik [db_path]"""
import sys
from pathlib import Path

from .app import run

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "slovnik.db"


def main() -> int:
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    return run(db)


if __name__ == "__main__":
    sys.exit(main())