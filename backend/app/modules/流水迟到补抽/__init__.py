"""流水迟到补抽。

迟到流水到达后，对**同一营业日**发起的补抽/重跑与普通日终抽取共用
``抽取重试预算.预算账本``（主键即营业日），因此补抽失败同样扣减同一共享预算，
不存在独立的“迟到预算”或内存计数。
"""

from __future__ import annotations

from typing import Any, Optional

启用 = True


def 说明():
    return "流水迟到补抽：同日补抽失败扣同一本重试预算账"


def 迟到补抽(
    营业日: str,
    批次编号: str = "",
    流水: Optional[list[dict[str, Any]]] = None,
    *,
    上限: Optional[int] = None,
    工作者: str = "迟到补抽",
    **预算选项: Any,
) -> dict[str, Any]:
    """可选先装载迟到流水，再走同一预算编排执行同日补抽。

    补抽就是来源标记为 ``迟到补抽`` 的一次抽取尝试：预算账本行仍按营业日共享。
    """
    from app.services import 装载
    # 软入口：补抽失败返回带 失败 标记的 dict，但预算扣减/整票回滚已落库。
    from app.modules.抽取重试预算 import 安全执行尝试 as 执行尝试

    装载报告 = None
    if 流水:
        装载报告 = 装载.装载流水(流水, 批次编号 and f"装载-{批次编号}" or "")

    选项 = dict(预算选项)
    if 上限 is not None:
        选项["上限"] = 上限
    结果 = 执行尝试(
        营业日,
        批次编号,
        None,
        工作者=工作者,
        来源="迟到补抽",
        **选项,
    )
    if 装载报告 is not None and isinstance(结果, dict):
        结果["迟到流水装载"] = {k: v for k, v in 装载报告.items() if k != "批次编号"}
    return 结果


# 常见命名别名
补抽 = 迟到补抽
流水迟到补抽 = 迟到补抽


def 补抽同日(营业日: str, **kw):
    return 迟到补抽(营业日, **kw)
