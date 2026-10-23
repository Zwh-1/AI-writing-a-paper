from pathlib import Path
import os
ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get('PAPER_DATA_DIR', str(ROOT / 'data'))).resolve()
HOST = '127.0.0.1'
PORT = int(os.environ.get('PAPER_PORT', '8765'))
DEFAULT_GATEWAY = os.environ.get('PAPER_BASE_URL','https://api.x5m5x.com/v1')
for name in ('objects', 'previews', 'exports', 'backups', 'logs', 'tmp'):
    (DATA / name).mkdir(parents=True, exist_ok=True)
DB_PATH = DATA / 'workbench.sqlite3'
MAX_FILE = 120 * 1024 * 1024
MAX_UNPACKED = 600 * 1024 * 1024
APP_VERSION = '0.4.1'
