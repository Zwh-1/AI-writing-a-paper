"""Check (read only), install or remove the optional AI response-cache indexes."""
import argparse
from contextlib import closing
import importlib.util
import json
from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('ai_cache_indexes', ROOT / 'backend/app/ai_cache_indexes.py')
indexes = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(indexes)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, default=ROOT / 'data/workbench.sqlite3')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--check', action='store_true', help='Read-only query plans (default)')
    action.add_argument('--install', action='store_true', help='Idle database only')
    action.add_argument('--remove', action='store_true', help='Idle database only; keep every run and schema version')
    args = parser.parse_args()
    writable = args.install or args.remove
    with closing(sqlite3.connect(args.database.resolve().as_uri() + ('?mode=rw' if writable else '?mode=ro'), uri=True, timeout=5)) as connection:
        if not writable:
            connection.execute('PRAGMA query_only=ON')
        try:
            report = indexes.manage(connection, remove=args.remove) if writable else indexes.inspect(connection)
        except (RuntimeError, sqlite3.Error) as error:
            parser.exit(1, str(error) + '\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
