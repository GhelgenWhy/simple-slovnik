"""Entry point: python -m slovnik [db_path]"""
from pathlib import Path
import sys

DEFAULT_DB = Path(__file__).resolve().parent.parent.parent / "data" / "slovnik.db"


def main() -> int:
    """MVP stub — full GUI arrives in Tasks 6–7."""
    db = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DB
    print(f"Slovník MVP — DB would be at: {db}")
    print("(GUI not yet implemented; this is the Task 1 scaffolding stub.)")
    return 0


if __name__ == "__main__":
    sys.exit(main())