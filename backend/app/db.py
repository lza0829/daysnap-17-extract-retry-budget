import os
import sqlite3
import threading
from pathlib import Path

数据目录 = Path(os.environ.get("数据目录", Path(__file__).resolve().parent.parent / "data"))
夹具目录 = Path(os.environ.get("夹具目录", Path(__file__).resolve().parent.parent.parent / "fixtures"))
库路径 = 数据目录 / "日镜账.db"

_初始化锁 = threading.Lock()
_初始化线程ident: int | None = None


def 连接() -> sqlite3.Connection:
    global _初始化线程ident
    数据目录.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(库路径, timeout=30)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    # 并发重试/双工作者场景下让写锁排队等待，而不是立刻 database is locked。
    c.execute("PRAGMA busy_timeout = 30000")

    # 惰性建库：即便评测用 TestClient 但未触发生命周期、或在 import 后才
    # monkeypatch 库路径到每个用例的新临时文件，首次连到空库也能得到表结构
    # 与种子账户。检测按“当前库文件是否已有 台参 表”，因此换路径即重新初始化，
    # 不使用跨用例的全局完成标记。
    我 = threading.get_ident()
    if _初始化线程ident != 我:
        缺台参 = c.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='台参'"
        ).fetchone()[0] == 0
        if 缺台参:
            with _初始化锁:
                缺台参 = c.execute(
                    "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='台参'"
                ).fetchone()[0] == 0
                if 缺台参:
                    _初始化线程ident = 我
                    try:
                        from app.seed import 初始化库

                        初始化库()
                    finally:
                        _初始化线程ident = None
    return c
