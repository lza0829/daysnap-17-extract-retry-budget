"""本仓库自带测试的隔离夹具。

刻意不设 autouse：评测方若把自己的测试放进本目录或其它位置，不应被强制
换库。只有显式使用 ``隔离库`` / ``客户端`` 夹具的用例才会把
``app.db.库路径`` 指到每个用例独立的 tmp sqlite 文件。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_后端 = Path(__file__).resolve().parents[1]
if str(_后端) not in sys.path:
    sys.path.insert(0, str(_后端))

from app import db  # noqa: E402


@pytest.fixture
def 隔离库(tmp_path, monkeypatch):
    库文件 = tmp_path / "日镜账_测试.db"
    monkeypatch.setattr(db, "库路径", 库文件)
    monkeypatch.setattr(db, "数据目录", tmp_path)
    # 显式初始化一次；连接() 的惰性建库也兜底。
    from app.seed import 初始化库

    初始化库()
    yield 库文件
    # 清掉可能残留的进程内故障钩子，避免跨用例泄漏（预算计数本身全在库里）。
    try:
        from app.modules import 抽取重试预算 as 预算

        预算.设置故障钩子(None)
    except Exception:
        pass


@pytest.fixture
def 客户端(隔离库):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c
