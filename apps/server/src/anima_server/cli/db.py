"""Quick CLI to query the encrypted SQLCipher database.

Usage:
    python -m anima_server.cli.db "SELECT * FROM soul_owners"
    python -m anima_server.cli.db tables
"""

from __future__ import annotations

import sys

from anima_server.db.session import SessionLocal


def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python -m anima_server.cli.db <sql|tables>")
        sys.exit(1)

    query = " ".join(sys.argv[1:])

    if query.strip().lower() == "tables":
        query = "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"

    with SessionLocal() as soul:
        conn = soul.connection()
        result = conn.exec_driver_sql(query)
        cols = list(result.keys())
        rows = result.fetchall()

        if not rows:
            print("(no rows)")
            return

        # Calculate column widths
        widths = [len(c) for c in cols]
        str_rows = []
        for row in rows:
            str_row = [str(v) if v is not None else "NULL" for v in row]
            for i, v in enumerate(str_row):
                widths[i] = max(widths[i], min(len(v), 60))
            str_rows.append(str_row)

        # Print header
        header = " | ".join(c.ljust(widths[i]) for i, c in enumerate(cols))
        print(header)
        print("-+-".join("-" * w for w in widths))

        # Print rows
        for str_row in str_rows:
            line = " | ".join(v[:60].ljust(widths[i]) for i, v in enumerate(str_row))
            print(line)

        print(f"\n({len(rows)} row{'s' if len(rows) != 1 else ''})")


if __name__ == "__main__":
    main()
