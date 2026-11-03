from pathlib import Path
import os,sys
ROOT = Path(sys._MEIPASS).resolve() if getattr(sys,'frozen',False) else Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('PAPER_DATA_DIR', str(ROOT / 'data'))).resolve()
HOST = os.environ.get('PAPER_BIND','127.0.0.1')
PORT = int(os.environ.get('PAPER_PORT', '8765'))
DEFAULT_GATEWAY = os.environ.get('PAPER_BASE_URL','https://api.x5m5x.com/v1')
for name in ('objects', 'previews', 'exports', 'backups', 'logs', 'tmp'):
    (DATA / name).mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'workbench.sqlite3'
# Paid requests are latency bound (one call answers in ~15-20s regardless of its
# size), so throughput scales with the number of requests in flight. The paid
# guard still caps a single task's in-flight set at this number and refuses the
# request that would exceed it.
MAX_PARALLEL_REQUESTS = 16
MAX_FILE = 120 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024
APP_VERSION = '0.10.0'
