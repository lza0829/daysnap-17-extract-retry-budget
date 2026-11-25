import json
import uuid
from typing import Any, Callable, Optional

from app.db import 连接
from app.services.时间 import 此刻, 标准化营业日


def _台参(conn) -> dict[str, str]:
    return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}


def _前日(营业日: str) -> str:
    y, m, d = map(int, 营业日.split("-"))
    from datetime import date, timedelta

    return (date(y, m, d) - timedelta(days=1)).isoformat()


def 抽取主体(
    conn,
    日: str,
    批: str,
    *,
    故障回调: Optional[Callable[[int], None]] = None,
    账户键们: Optional[set[str] | list[str]] = None,
) -> dict[str, Any]:
    """在调用方提供的连接/事务内执行抽取写操作。

    不自行 commit、不关闭连接、不写 抽取批次 汇总行（由预算编排统一落批）。
    每写出一行快照后调用 ``故障回调(累计写入数)``，便于注入“半截失败”。
    ``账户键们`` 非空时只抽该分片（分片重跑），预算仍按营业日共享。
    """
    # 防御：主体 INSERT 显式写 valid/批类/诊断批号 等预算模块迁移列；
    # 直接调用本主体（绕过预算编排）时也保证列存在。建表已做成不隐式提交，
    # 因此不会破坏调用方正持有的事务/保存点。
    from app.modules.抽取重试预算 import 建表 as _确保预算表

    _确保预算表(conn)

    参 = _台参(conn)
    重复 = 参.get("重复抽取", "盖写同日快照")
    缺前日 = 参.get("缺前日策略", "以零为开端")
    账户们 = [
        dict(r)
        for r in conn.execute("SELECT * FROM 账户 WHERE 状态='正常' ORDER BY 账户键")
    ]
    if 账户键们 is not None:
        过滤集 = {str(k) for k in 账户键们}
        账户们 = [户 for 户 in 账户们 if 户["账户键"] in 过滤集]
    写入 = 0
    盖写 = 0
    旁置 = 0
    断档数 = 0

    def _tick():
        if 故障回调 is not None:
            故障回调(写入 + 盖写)

    for 户 in 账户们:
        账户键 = 户["账户键"]
        开户日 = 户["开户营业日"]
        if 日 < 开户日:
            continue
        前 = _前日(日)
        前日快照 = conn.execute(
            "SELECT 余额 FROM 周期快照 WHERE 账户键=? AND 营业日=? AND 状态='有效'",
            (账户键, 前),
        ).fetchone()
        if 前日快照 is None:
            if 日 == 开户日 or 缺前日 == "以零为开端":
                开端 = 0
            else:
                conn.execute(
                    "INSERT INTO 断档记录(账户键, 缺营业日, 检出批次编号, 处置, 时刻) VALUES (?,?,?,?,?)",
                    (账户键, 前, 批, "记断档并继续", 此刻()),
                )
                断档数 += 1
                开端 = 0
        else:
            开端 = int(前日快照["余额"])
        净额行 = conn.execute(
            "SELECT COALESCE(SUM(变动额),0) AS s FROM 变动流水 "
            "WHERE 账户键=? AND 营业日=? AND 命运='已入账'",
            (账户键, 日),
        ).fetchone()
        净额 = int(净额行["s"])
        余额 = 开端 + 净额
        快照键 = f"{账户键}|{日}"
        旧 = conn.execute(
            "SELECT 快照键 FROM 周期快照 WHERE 账户键=? AND 营业日=?",
            (账户键, 日),
        ).fetchone()
        if 旧:
            if 重复 != "盖写同日快照":
                conn.execute(
                    "INSERT INTO 旁置簿(原因, 账户键, 营业日, 载荷, 时刻) VALUES (?,?,?,?,?)",
                    (
                        "同日已有快照",
                        账户键,
                        日,
                        json.dumps({"余额": 余额}, ensure_ascii=False),
                        此刻(),
                    ),
                )
                旁置 += 1
                continue
            conn.execute(
                "UPDATE 周期快照 SET 余额=?, 抽取批次编号=?, 来源='日终抽取', 状态='有效', "
                "valid=1, 批类='正式', 诊断批号='' "
                "WHERE 账户键=? AND 营业日=?",
                (余额, 批, 账户键, 日),
            )
            盖写 += 1
        else:
            conn.execute(
                "INSERT INTO 周期快照(快照键, 账户键, 营业日, 余额, 抽取批次编号, 来源, 状态, valid, 批类) "
                "VALUES (?,?,?,?,?,?, '有效', 1, '正式')",
                (快照键, 账户键, 日, 余额, 批, "日终抽取"),
            )
            写入 += 1
        _tick()
    return {
        "营业日": 日,
        "写入": 写入,
        "盖写": 盖写,
        "旁置": 旁置,
        "断档": 断档数,
        "账户扫描": len(账户们),
    }


def 日终抽取(营业日: str, 批次编号: str = "", **预算选项: Any) -> dict[str, Any]:
    """按营业日为全部正常账户写周期快照（经重试预算编排，同票事务）。

    余额=前日有效快照(或开户零)+当日流水净额。失败整票回滚并扣共享预算；
    预算耗尽后按全局口径（默认诊断批）处理。
    """
    # 延迟导入，避免 services.抽取 <-> modules.抽取重试预算 循环导入。
    # 用软入口：失败/耗尽返回带 失败/拒绝 标记的 dict（顺序调用不会被异常打断）；
    # HTTP 路由会据标记翻译成 4xx。需要异常风格可直接用 预算.执行尝试。
    from app.modules.抽取重试预算 import 安全执行尝试

    return 安全执行尝试(营业日, 批次编号, None, **预算选项)
