"""确保 /workspace/backend 在 sys.path 上，使 `import app...` 可用。"""
import sys
from pathlib import Path

_本目录 = Path(__file__).resolve().parent
if str(_本目录) not in sys.path:
    sys.path.insert(0, str(_本目录))
