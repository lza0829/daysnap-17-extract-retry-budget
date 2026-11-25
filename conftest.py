"""把 backend 目录加入 sys.path，保证无论 pytest 从 /workspace 还是
/workspace/backend 启动、也无论评测把测试放在哪里，都能 ``import app``。"""
import sys
from pathlib import Path

_后端 = Path(__file__).resolve().parent / "backend"
if _后端.is_dir() and str(_后端) not in sys.path:
    sys.path.insert(0, str(_后端))
