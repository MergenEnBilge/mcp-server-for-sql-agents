"""Build the SQLite copy of the sample database from schema.sql + seed_data.sql.

    python db/sample_data/build_sqlite.py            # writes db/sample_data/sample.sqlite
    python db/sample_data/build_sqlite.py some.db    # or a path of your choice

Uses only the standard library. Any existing file at the target path is replaced,
so the result always matches the SQL files exactly.
"""

import sqlite3
import sys
from pathlib import Path

HERE = Path(__file__).parent
DEFAULT_TARGET = HERE / "sample.sqlite"


def build(target: Path) -> None:
    target.unlink(missing_ok=True)
    conn = sqlite3.connect(target)
    try:
        # SQLite ignores foreign keys unless asked; turn them on so bad seed data fails loudly.
        conn.execute("PRAGMA foreign_keys = ON")
        for name in ("schema.sql", "seed_data.sql"):
            conn.executescript((HERE / name).read_text(encoding="utf-8"))
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise RuntimeError(f"foreign key violations in seed data: {violations[:5]}")
    finally:
        conn.close()


if __name__ == "__main__":
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_TARGET
    build(path)
    print(f"built {path}")
