import logging
import sys
from pathlib import Path

# Resolve the log directory regardless of whether we're frozen (EXE) or running from source.
# When frozen by PyInstaller, __file__ points inside the temp _MEIPASS extraction folder
# which is deleted on exit — so we must use sys.executable's parent instead.
if getattr(sys, 'frozen', False):
    _base_dir = Path(sys.executable).parent
else:
    _base_dir = Path(__file__).parent.parent.resolve()

log_dir = _base_dir / "logs"
log_dir.mkdir(exist_ok=True)

logger = logging.getLogger("Alchemica")
logger.setLevel(logging.DEBUG)

# Create handlers
c_handler = logging.StreamHandler()
f_handler = logging.FileHandler(log_dir / "app.log", encoding="utf-8")
c_handler.setLevel(logging.INFO)
f_handler.setLevel(logging.DEBUG)

# Create formatters and add it to handlers
c_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
c_handler.setFormatter(c_format)
f_handler.setFormatter(f_format)

# Add handlers to the logger
logger.addHandler(c_handler)
logger.addHandler(f_handler)

def get_logger():
    return logger