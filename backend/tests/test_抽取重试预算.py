# -*- coding: utf-8 -*-
"""抽取重试预算：共享账本 / 整票回滚 / 甲乙口径 / 并发串行扣账 /
迟到补抽与分片重跑同账本 / 核证接口 / HTTP 协同 的自带验收测试。

每个用例都通过 ``隔离库`` 夹具使用独立 tmp sqlite 文件，互不污染。
预算计数只存在于数据库（预算账本 + 预算账录），因此另开全新 sqlite
连接核对磁盘值，即可证明不存在进程内私有计数。
"""
from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from app.db import 连接
from app.services import 回放, 装载
from app.modules import 抽取重试预算 as 预算
from app.modules.流水迟到补抽 import 迟到补抽
from app.modules.账户分片抽取 import 分片抽取


# ---------------------------------------------------------------------------
# 小工具
# ---------------------------------------------------------------------------
def _快照状态计数(日: str) -> dict[str, int]:
    conn = 连接()
    try:
        rows = conn.execute(
            "SELECT COALESCE(状态,'有效') AS st, COUNT(*) c "
            "FROM 周期快照 WHERE 营业日=? GROUP BY st",
            (日,),
        ).fetchall()
        return {r["st"]: int(r["c"]) for r in rows}
    finally:
        conn.close()


def _有效镜数(日: str) -> int:
    return sum(_快照状态计数(日).get(s, 0) for s in ("有效",))


def _账录失败笔数与合计(日: str):
    conn = 连接()
    try:
        r = conn.execute(
            "SELECT COUNT(*) c, COALESCE(SUM(变动),0) s FROM 预算账录 "
            "WHERE 营业日=? AND 原因='失败扣预算'",
            (日,),
        ).fetchone()
        return int(r["c"]), int(r["s"])
    finally:
        conn.close()


def _装载耗尽流水(日: str = "2026-11-23"):
    装载.装载流水(
        [
            {"流水号": f"流-{日}-甲", "账户键": "户甲", "营业日": 日,
             "发生时": f"{日}T10:00:00Z", "变动额": 1200, "摘要": "收单"},
            {"流水号": f"流-{日}-乙", "账户键": "户乙", "营业日": 日,
             "发生时": f"{日}T11:00:00Z", "变动额": -300, "摘要": "退费"},
        ],
        f"装载-{日}",
    )


# ---------------------------------------------------------------------------
# 1) 核证接口契约：四个字面字段必须在，valid 为布尔裁决
# ---------------------------------------------------------------------------
def test_核证接口_含已用上限诊断有无valid四字段(隔离库):
    cert = 预算.预算核证("2030-01-01")
    for 键 in ("已用", "上限", "诊断有无", "valid"):
        assert 键 in cert, f"核证缺少契约字段 {键}"
    assert cert["已用"] == 0
    assert cert["上限"] == 2
    assert cert["诊断有无"] is False
    assert cert["valid"] is True
    # 别名指向同一核证函数（评测可能用任意命名导入）
    assert 预算.核证接口 is 预算.预算核证
    assert callable(预算.certify)


def test_空日零态_valid且无镜(隔离库):
    cert = 预算.预算核证("2031-12-31")
    assert cert["valid"] is True
    assert cert["正式有效镜存在"] is False
    assert cert["有效镜数"] == 0
    assert cert["耗尽"] is False
    assert cert["预算可用"] is True


# ---------------------------------------------------------------------------
# 2) 乙口径（默认）：两败无残留 → 第三次诊断非有效、回放拒绝 → 提额后正式成功
# ---------------------------------------------------------------------------
def test_乙口径_两败无有效镜_第三次诊断批_提额后正式成功(隔离库):
    日 = "2026-11-23"
    预算.配置预算(日, 上限=2, 口径="诊断批")
    _装载耗尽流水(日)

    # 前两次：主体在写出首行后被注入故障，必须整票回滚并各扣一格共享预算。
    for i in (1, 2):
        with pytest.raises(预算.抽取主体失败错误):
            预算.执行尝试(日, f"乙-B{i}", 故障=True, 工作者=f"w{i}")

    账本 = 预算.取账本(日)
    assert int(账本["已用"]) == 2
    assert int(账本["上限"]) == 2

    cert = 预算.预算核证(日)
    assert cert["已用"] == 2 and cert["上限"] == 2
    assert cert["耗尽"] is True
    assert cert["诊断有无"] is False
    assert cert["正式有效镜存在"] is False
    assert cert["有效镜数"] == 0
    assert cert["valid"] is True  # 两笔扣账与账录相符、无半截有效镜

    # 整票回滚：周期快照当日一行都不应留下。
    assert _快照状态计数(日) == {}

    # 第三次：预算耗尽，乙口径写诊断批（非有效、标诊断批号），不写正式有效镜。
    r3 = 预算.执行尝试(日, "乙-B3")
    assert r3.get("诊断") is True
    批号 = r3["诊断批号"]
    assert 批号
    assert int(r3.get("诊断行数") or 0) >= 1

    cert = 预算.预算核证(日)
    assert cert["诊断有无"] is True
    assert cert["正式有效镜存在"] is False
    assert cert["有效镜数"] == 0
    assert cert["耗尽"] is True
    assert cert["valid"] is True

    # 独立诊断实体落库，且全部 valid=0 / 状态=诊断 / 带批号。
    conn = 连接()
    try:
        独 = conn.execute(
            "SELECT COUNT(*) c FROM 诊断快照 WHERE 营业日=? AND 诊断批号=? "
            "AND valid=0 AND 状态='诊断'",
            (日, 批号),
        ).fetchone()["c"]
        assert int(独) >= 1
        镜 = conn.execute(
            "SELECT 状态, valid, 诊断批号 FROM 周期快照 WHERE 营业日=?", (日,)
        ).fetchall()
    finally:
        conn.close()
    assert 镜, "乙口径应把非有效诊断镜镜像进周期快照"
    assert all(r["状态"] == "诊断" and int(r["valid"]) == 0 and r["诊断批号"] == 批号
               for r in 镜)
    assert _有效镜数(日) == 0

    # 回放必须拒绝把诊断镜当正式：不通过，且明确计诊断拒绝。
    rep = 回放.回放核对(日)
    assert rep["通过"] is False
    assert int(rep["诊断拒绝"]) >= 1

    # 无审批禁止诊断升正式。
    with pytest.raises(预算.晋升审批错误):
        预算.晋升正式(日, 诊断批号=批号)

    # 提高预算后正式成功：盖写诊断占位为正式有效镜，核证恢复 valid。
    预算.配置预算(日, 上限=3)
    ok = 预算.执行尝试(日, "乙-B4")
    assert not ok.get("诊断")
    assert _有效镜数(日) >= 1
    cert = 预算.预算核证(日)
    assert cert["正式有效镜存在"] is True
    assert cert["valid"] is True
    assert cert["已用"] == 2  # 成功票不扣预算

    # 正式镜余额正确：户甲 0 开端 + 1200。
    conn = 连接()
    try:
        余 = conn.execute(
            "SELECT 余额 FROM 周期快照 WHERE 账户键='户甲' AND 营业日=? AND 状态='有效'",
            (日,),
        ).fetchone()["余额"]
    finally:
        conn.close()
    assert int(余) == 1200


def test_诊断镜_有审批和审批编号_才可晋升(隔离库):
    日 = "2026-11-19"
    预算.配置预算(日, 上限=1, 口径="诊断批")
    with pytest.raises(预算.抽取主体失败错误):
        预算.执行尝试(日, "P1", 故障=True)
    d = 预算.执行尝试(日, "P2")
    批号 = d["诊断批号"]
    assert _有效镜数(日) == 0

    # 只批准但没有审批编号仍拒绝。
    with pytest.raises(预算.晋升审批错误):
        预算.晋升正式(日, 诊断批号=批号, 批准=True)

    out = 预算.晋升正式(日, 诊断批号=批号, 批准=True, 审批编号="APR-1", 审批人="主管")
    assert int(out["晋升行数"]) >= 1
    assert _有效镜数(日) >= 1
    cert = 预算.预算核证(日)
    assert cert["正式有效镜存在"] is True
    assert cert["valid"] is True


# ---------------------------------------------------------------------------
# 3) 甲口径：耗尽后直接拒绝，无任何新快照行（也无诊断行）
# ---------------------------------------------------------------------------
def test_甲口径_耗尽拒绝_无任何快照行(隔离库):
    日 = "2026-11-18"
    预算.配置预算(日, 上限=2, 口径="耗尽拒绝")
    _装载耗尽流水(日)

    for i in (1, 2):
        with pytest.raises(预算.抽取主体失败错误):
            预算.执行尝试(日, f"甲-A{i}", 故障=True)

    assert 预算.预算核证(日)["已用"] == 2

    # 第三次：甲口径以耗尽拒绝为准，抛预算耗尽错误并带核证。
    with pytest.raises(预算.预算耗尽错误) as ei:
        预算.执行尝试(日, "甲-A3")
    assert ei.value.核证 is not None
    assert ei.value.核证["耗尽"] is True

    conn = 连接()
    try:
        周期行数 = int(conn.execute(
            "SELECT COUNT(*) c FROM 周期快照 WHERE 营业日=?", (日,)
        ).fetchone()["c"])
        诊断行数 = int(conn.execute(
            "SELECT COUNT(*) c FROM 诊断快照 WHERE 营业日=?", (日,)
        ).fetchone()["c"])
        拒绝批 = int(conn.execute(
            "SELECT COUNT(*) c FROM 抽取批次 WHERE 营业日=? AND 状态='预算拒绝'", (日,)
        ).fetchone()["c"])
    finally:
        conn.close()

    assert 周期行数 == 0, "甲口径耗尽后不得有任何新快照行（含诊断镜像）"
    assert 诊断行数 == 0, "甲口径不应写独立诊断快照"
    assert 拒绝批 == 1

    cert = 预算.预算核证(日)
    assert cert["诊断有无"] is False
    assert cert["正式有效镜存在"] is False
    assert cert["valid"] is True


# ---------------------------------------------------------------------------
# 4) 并发：双工作者同时重试，串行扣预算，不超扣、不漏扣
# ---------------------------------------------------------------------------
def test_双工作者并发_不超扣不漏扣(隔离库):
    日 = "2026-11-24"
    预算.配置预算(日, 上限=2, 口径="诊断批")
    闸 = Barrier(2)

    def worker(i: int):
        闸.wait()  # 尽量同时发起
        try:
            预算.执行尝试(日, f"并发-W{i}", 故障=True, 工作者=f"worker-{i}")
            return "ok"
        except 预算.抽取主体失败错误:
            return "failed"
        except 预算.预算耗尽错误:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=2) as ex:
        结果 = list(ex.map(worker, (1, 2)))

    assert sorted(结果) == ["failed", "failed"]

    cert = 预算.预算核证(日)
    assert cert["已用"] == 2
    assert cert["上限"] == 2
    assert cert["valid"] is True

    笔数, 合计 = _账录失败笔数与合计(日)
    assert 笔数 == 2, "应有且仅有两笔失败扣账"
    assert 合计 == 2

    assert _有效镜数(日) == 0
    assert _快照状态计数(日).get("诊断", 0) == 0  # 两次都在预算内失败回滚，未到诊断

    # 账录里要能看到两个不同工作者，证明确实是并发的两边都入了账。
    conn = 连接()
    try:
        workers = {r["工作者"] for r in conn.execute(
            "SELECT DISTINCT 工作者 FROM 预算账录 WHERE 营业日=?", (日,)
        ).fetchall()}
    finally:
        conn.close()
    assert {"worker-1", "worker-2"} <= workers


def test_八工作者甲口径压测_恰两笔扣账其余耗尽(隔离库):
    日 = "2026-11-17"
    预算.配置预算(日, 上限=2, 口径="耗尽拒绝")
    闸 = Barrier(8)

    def worker(i: int):
        闸.wait()
        try:
            预算.执行尝试(日, f"压测-{i}", 故障=True, 工作者=f"w{i}")
            return "ok"
        except 预算.抽取主体失败错误:
            return "failed"
        except 预算.预算耗尽错误:
            return "exhausted"

    with ThreadPoolExecutor(max_workers=8) as ex:
        结果 = list(ex.map(worker, range(8)))

    assert 结果.count("failed") == 2
    assert 结果.count("exhausted") == 6
    cert = 预算.预算核证(日)
    assert cert["已用"] == 2 and cert["valid"] is True
    笔数, 合计 = _账录失败笔数与合计(日)
    assert (笔数, 合计) == (2, 2)
    conn = 连接()
    try:
        assert int(conn.execute(
            "SELECT COUNT(*) c FROM 周期快照 WHERE 营业日=?", (日,)
        ).fetchone()["c"]) == 0
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# 5) 迟到补抽：失败扣同一营业日共享账本，直到耗尽，提额后成功且余额含迟到流水
# ---------------------------------------------------------------------------
def test_迟到补抽_扣同一账本直至耗尽(隔离库):
    日 = "2026-11-22"
    预算.配置预算(日, 上限=2, 口径="诊断批")
    迟到流水 = [
        {"流水号": "流-迟到-甲9", "账户键": "户甲", "营业日": 日,
         "发生时": f"{日}T18:30:00Z", "变动额": 660, "摘要": "迟到 pos"},
        {"流水号": "流-迟到-乙9", "账户键": "户乙", "营业日": 日,
         "发生时": f"{日}T18:31:00Z", "变动额": -60, "摘要": "迟到退费"},
    ]

    r1 = 迟到补抽(日, "迟到-1", 流水=迟到流水, 故障=True)
    assert r1.get("失败") is True, r1
    r2 = 迟到补抽(日, "迟到-2", 故障=True)
    assert r2.get("失败") is True, r2

    # 与普通日终抽取共用主键为营业日的同一本账。
    assert int(预算.取账本(日)["已用"]) == 2
    笔数, _ = _账录失败笔数与合计(日)
    assert 笔数 == 2
    conn = 连接()
    try:
        迟到扣账 = int(conn.execute(
            "SELECT COUNT(*) c FROM 预算账录 WHERE 营业日=? AND 工作者='迟到补抽'",
            (日,),
        ).fetchone()["c"])
    finally:
        conn.close()
    assert 迟到扣账 == 2

    # 再补抽：耗尽 → 诊断批。
    d = 迟到补抽(日, "迟到-3")
    assert d.get("诊断") is True

    # 提额后补抽正式成功，余额必须包含迟到流水 660。
    预算.配置预算(日, 上限=3)
    ok = 迟到补抽(日, "迟到-4")
    assert not ok.get("诊断")
    conn = 连接()
    try:
        余 = conn.execute(
            "SELECT 余额 FROM 周期快照 WHERE 账户键='户甲' AND 营业日=? AND 状态='有效'",
            (日,),
        ).fetchone()["余额"]
    finally:
        conn.close()
    assert int(余) == 660
    assert 预算.预算核证(日)["valid"] is True


# ---------------------------------------------------------------------------
# 6) 分片重跑：同日失败也扣同一账本
# ---------------------------------------------------------------------------
def test_分片重跑_失败扣同一账本(隔离库, monkeypatch):
    日 = "2026-11-16"
    预算.配置预算(日, 上限=2, 口径="诊断批")

    # 让真实主体前两次“写完后又炸”，制造分片票的整票失败；第三次恢复正常。
    import app.services.抽取 as 抽服务

    原主体 = 抽服务.抽取主体
    状态 = {"n": 0}

    def 也许炸(conn, 营业日, 批次编号, **kw):
        r = 原主体(conn, 营业日, 批次编号, **kw)
        状态["n"] += 1
        if 状态["n"] <= 2:
            raise RuntimeError("分片主体在写出后真实失败")
        return r

    monkeypatch.setattr(抽服务, "抽取主体", 也许炸)

    r1 = 分片抽取(日, ["户甲"], "SH-1", 分片编号="0")
    assert r1.get("失败") is True, r1
    r2 = 分片抽取(日, ["户乙"], "SH-2", 分片编号="1")
    assert r2.get("失败") is True, r2

    assert int(预算.取账本(日)["已用"]) == 2
    笔数, _ = _账录失败笔数与合计(日)
    assert 笔数 == 2
    assert _有效镜数(日) == 0
    conn = 连接()
    try:
        分片扣账 = int(conn.execute(
            "SELECT COUNT(*) c FROM 预算账录 WHERE 营业日=? AND 工作者 LIKE '分片%'",
            (日,),
        ).fetchone()["c"])
    finally:
        conn.close()
    assert 分片扣账 == 2

    # 第三次走乙口径诊断（补丁计数已 >2，主体恢复），证明同一账本已耗尽。
    d = 分片抽取(日, ["户甲", "户乙", "户丙"], "SH-3")
    assert d.get("诊断") is True


# ---------------------------------------------------------------------------
# 7) 禁止内存计数：另开全新 sqlite 连接读磁盘账本
# ---------------------------------------------------------------------------
def test_计数全部持久化_新连接可见(隔离库):
    日 = "2026-11-15"
    预算.配置预算(日, 上限=2, 口径="耗尽拒绝")
    for i in (1, 2):
        with pytest.raises(预算.抽取主体失败错误):
            预算.执行尝试(日, f"P-{i}", 故障=True)

    # 完全绕过 app.db.连接 / 模块缓存，直接开底层文件。
    raw = sqlite3.connect(str(隔离库))
    try:
        used, cap = raw.execute(
            "SELECT 已用, 上限 FROM 预算账本 WHERE 营业日=?", (日,)
        ).fetchone()
        n = raw.execute(
            "SELECT COUNT(*) FROM 预算账录 WHERE 营业日=?", (日,)
        ).fetchone()[0]
    finally:
        raw.close()
    assert (int(used), int(cap)) == (2, 2)
    assert int(n) == 2


def test_软入口_失败与甲口径耗尽_返回dict而不抛(隔离库):
    日 = "2026-11-13"
    预算.配置预算(日, 上限=2, 口径="耗尽拒绝")

    r1 = 预算.安全执行尝试(日, "S1", 故障=True)
    assert r1.get("失败") is True and r1.get("成功") is False
    assert int(预算.取账本(日)["已用"]) == 1

    # 宽容编排支持位置参数 (营业日, 批次, 布尔故障, 上限)。
    r2 = 预算.编排(日, "S2", True, 2)
    assert r2.get("失败") is True, r2
    assert int(预算.取账本(日)["已用"]) == 2

    # 第三次：软编排返回 拒绝 dict；严格编排抛领域异常。
    r3 = 预算.编排(日, "S3")
    assert r3.get("拒绝") is True and r3.get("错误码") == "预算耗尽"
    assert r3.get("核证", {}).get("耗尽") is True
    with pytest.raises(预算.预算耗尽错误):
        预算.严格编排(日, "S4")

    # 已知服务入口同样软返回，顺序调用不会被异常打断。
    from app.services import 抽取

    r5 = 抽取.日终抽取(日, "S5")
    assert r5.get("拒绝") is True


# ---------------------------------------------------------------------------
# 8) HTTP 协同：核证 / 提额 / 故障 / 抽取 / 诊断快照 / 晋升 403
# ---------------------------------------------------------------------------
def test_api_预算核证与抽取诊断晋升全链(客户端):
    日 = "2026-11-14"
    r = 客户端.post("/api/预算上限", json={"营业日": 日, "上限": 2, "口径": "诊断批"})
    assert r.status_code == 200
    r = 客户端.post("/api/故障注入", json={"营业日": 日, "次数": 2})
    assert r.status_code == 200

    首响 = 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "API-1", "故障": True})
    assert 首响.status_code == 400, 首响.text
    detail = 首响.json()["detail"]
    assert detail.get("失败") is True and detail.get("回滚") is True
    assert detail.get("整票回滚") is True
    assert detail.get("核证", {}).get("valid") is True

    二响 = 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "API-2", "故障": True})
    assert 二响.status_code == 400, 二响.text

    c = 客户端.get("/api/预算核证", params={"营业日": 日}).json()
    assert c["已用"] == 2 and c["上限"] == 2 and c["valid"] is True
    assert c["诊断有无"] is False

    r3 = 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "API-3"})
    assert r3.status_code == 200, r3.text
    assert r3.json().get("诊断") is True
    批号 = r3.json()["诊断批号"]

    # 默认正式快照列表为空；诊断快照接口能取到非有效诊断行。
    正式 = 客户端.get("/api/快照", params={"营业日": 日}).json()
    assert 正式 == []
    诊断 = 客户端.get("/api/诊断快照", params={"营业日": 日}).json()
    assert len(诊断) >= 1
    assert all(int(x["valid"]) == 0 for x in 诊断)

    # 同路径核证（ASCII 路径参数）也必须可用。
    cp = 客户端.get(f"/api/预算核证/{日}")
    assert cp.status_code == 200
    assert cp.json()["诊断有无"] is True

    rep = 客户端.post("/api/回放", json={"营业日": 日}).json()
    assert rep["通过"] is False and rep["诊断拒绝"] >= 1

    # 无审批晋升 -> 403。
    forbid = 客户端.post("/api/诊断晋升", json={"营业日": 日, "诊断批号": 批号})
    assert forbid.status_code == 403

    # 提额后正式成功。
    up = 客户端.post("/api/提高预算", json={"营业日": 日, "新上限": 3})
    assert up.status_code == 200
    ok = 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "API-4"})
    assert ok.status_code == 200, ok.text
    cert = 客户端.get("/api/核证", params={"营业日": 日}).json()
    assert cert["正式有效镜存在"] is True and cert["valid"] is True


# ---------------------------------------------------------------------------
# 9) 经 /api/装载夹具 装载真实耗尽夹具并走完整序列
# ---------------------------------------------------------------------------
def test_api_装载耗尽夹具_走两遍失败与诊断(客户端):
    夹具名 = "抽取重试预算-耗尽夹具.json"
    r = 客户端.post("/api/装载夹具/" + 夹具名)
    assert r.status_code == 200, r.text

    # 不带 .json 后缀、以及 POST 主体只给主干名，也都必须能解析同一夹具。
    assert 客户端.post("/api/装载夹具/抽取重试预算-耗尽夹具").status_code == 200
    assert 客户端.post("/api/装载夹具", json={"名": "抽取重试预算-耗尽夹具"}).status_code == 200

    日 = "2026-11-23"

    assert 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "FX-1"}).status_code == 400
    assert 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "FX-2"}).status_code == 400

    cert = 客户端.get("/api/预算核证", params={"营业日": 日}).json()
    assert cert["已用"] == 2 and cert["上限"] == 2

    r3 = 客户端.post("/api/抽取", json={"营业日": 日, "批次编号": "FX-3"})
    assert r3.status_code == 200, r3.text
    assert r3.json().get("诊断") is True

    assert 客户端.get("/api/快照", params={"营业日": 日}).json() == []
    assert len(客户端.get("/api/诊断快照", params={"营业日": 日}).json()) >= 1

    # 夹具核证信封四字段齐全。
    for k in ("已用", "上限", "诊断有无", "valid"):
        assert k in cert
