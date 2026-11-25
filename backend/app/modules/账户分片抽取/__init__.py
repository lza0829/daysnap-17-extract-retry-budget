"""账户分片抽取 / 分片重跑。

分片重跑若作为同日重试，同样走 ``抽取重试预算`` 的按营业日账本，
失败扣同一格预算，不另设私有计数。
"""

from __future__ import annotations

from typing import Any, Iterable, Optional

启用 = True


def 说明():
    return "账户分片抽取：分片重跑与日终抽取共享同日重试预算账本"


def 分片抽取(
    营业日: str,
    分片: Optional[Iterable[str]] = None,
    批次编号: str = "",
    *,
    分片编号: str = "",
    上限: Optional[int] = None,
    工作者: str = "",
    **预算选项: Any,
) -> dict[str, Any]:
    键们 = list(分片) if 分片 is not None else []
    工作者 = 工作者 or (f"分片-{分片编号}" if 分片编号 else "账户分片")

    def _分片动作(conn, ctx):
        from app.services.抽取 import 抽取主体

        return 抽取主体(conn, 营业日, ctx["批次编号"], 账户键们=set(键们) if 键们 else None)

    from app.modules.抽取重试预算 import 安全执行尝试 as 执行尝试

    选项 = dict(预算选项)
    if 上限 is not None:
        选项["上限"] = 上限
    return 执行尝试(
        营业日,
        批次编号,
        _分片动作 if 键们 else None,
        工作者=工作者,
        来源="账户分片抽取",
        **选项,
    )


分片重跑 = 分片抽取
重跑分片 = 分片抽取
