import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from app.db import 夹具目录, 连接
from app.services import 回放, 抽取, 目录, 装载
from app.modules import 抽取重试预算 as 预算

路由 = APIRouter(prefix="/api")


async def _体(request: Request) -> dict:
    """宽容解析 JSON 请求体：空体/非对象都退回 {}，便于接受多种字段命名。"""
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


class 装载请求(BaseModel):
    批次编号: str = ""
    流水: list = Field(default_factory=list)


class 营业日请求(BaseModel):
    营业日: str
    批次编号: str = ""


class 抽取请求(BaseModel):
    model_config = {"extra": "allow"}

    营业日: str = ""
    批次编号: str = ""
    上限: int | None = None
    口径: str | None = None
    工作者: str = ""
    故障: object | None = None
    来源: str = "日终抽取"


@路由.get("/健康")
def 健康():
    return {
        "正常": True,
        "项目": "日镜账",
        "骨架": "日终余额抽取→周期快照落行→变动流水回放核对→断档跳变检出→快照与回放差额清单",
    }


@路由.get("/总览")
def 取总览():
    return 目录.总览()


@路由.get("/台参")
def 取台参():
    conn = 连接()
    try:
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


@路由.get("/账户")
def 列账户():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 账户 ORDER BY 账户键")]
    finally:
        conn.close()


@路由.get("/流水")
def 列流水(营业日: str = "", 账户键: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 变动流水 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 发生时 DESC LIMIT 300"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/快照")
def 列快照(营业日: str = "", 账户键: str = "", 含诊断: int = 0, 状态: str = ""):
    conn = 连接()
    try:
        if 状态:
            sql = "SELECT * FROM 周期快照 WHERE 状态=?"
            args: list = [状态]
        elif 含诊断:
            sql = "SELECT * FROM 周期快照 WHERE 1=1"
            args = []
        else:
            # 正式镜只读有效行；诊断镜非有效，不在正式列表中出现。
            sql = "SELECT * FROM 周期快照 WHERE 状态='有效'"
            args = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 账户键:
            sql += " AND 账户键=?"
            args.append(账户键)
        sql += " ORDER BY 营业日 DESC, 账户键 LIMIT 500"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/诊断快照")
def 列诊断快照(营业日: str = "", 诊断批号: str = ""):
    conn = 连接()
    try:
        sql = "SELECT * FROM 诊断快照 WHERE 1=1"
        args: list = []
        if 营业日:
            sql += " AND 营业日=?"
            args.append(营业日)
        if 诊断批号:
            sql += " AND 诊断批号=?"
            args.append(诊断批号)
        sql += " ORDER BY 编号 DESC LIMIT 500"
        return [dict(r) for r in conn.execute(sql, args)]
    finally:
        conn.close()


@路由.get("/诊断批次")
def 列诊断批次(营业日: str = ""):
    conn = 连接()
    try:
        if 营业日:
            rows = conn.execute("SELECT * FROM 诊断批次 WHERE 营业日=? ORDER BY 时刻 DESC", (营业日,))
        else:
            rows = conn.execute("SELECT * FROM 诊断批次 ORDER BY 时刻 DESC LIMIT 100")
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/差额")
def 列差额(回放批次编号: str = ""):
    conn = 连接()
    try:
        if 回放批次编号:
            rows = conn.execute(
                "SELECT * FROM 差额行 WHERE 回放批次编号=? ORDER BY 编号 DESC",
                (回放批次编号,),
            )
        else:
            rows = conn.execute("SELECT * FROM 差额行 ORDER BY 编号 DESC LIMIT 200")
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/断档")
def 列断档():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 断档记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/跳变")
def 列跳变():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 跳变记录 ORDER BY 编号 DESC LIMIT 200")]
    finally:
        conn.close()


@路由.get("/旁置")
def 列旁置():
    conn = 连接()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM 旁置簿 ORDER BY 编号 DESC LIMIT 100")]
    finally:
        conn.close()


def _列批(表: str):
    conn = 连接()
    try:
        out = []
        for r in conn.execute(f"SELECT * FROM {表} ORDER BY 时刻 DESC LIMIT 40"):
            d = dict(r)
            try:
                d["报告"] = json.loads(d.get("报告") or "{}")
            except Exception:
                pass
            out.append(d)
        return out
    finally:
        conn.close()


@路由.get("/装载批次")
def 列装载批():
    return _列批("装载批次")


@路由.get("/抽取批次")
def 列抽取批():
    return _列批("抽取批次")


@路由.get("/回放批次")
def 列回放批():
    return _列批("回放批次")


@路由.get("/夹具")
def 列夹具():
    if not 夹具目录.exists():
        return []
    return sorted(p.name for p in 夹具目录.glob("*.json"))


@路由.post("/装载")
def 执行装载(体: 装载请求):
    try:
        return 装载.装载流水(体.流水, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


class 夹具请求(BaseModel):
    名: str


@路由.post("/装载夹具/{name}")
def 装载夹具路径(name: str):
    return _装载夹具文件(name)


@路由.post("/装载夹具")
def 装载夹具体(体: 夹具请求):
    return _装载夹具文件(体.名)


def _解析夹具路径(名: str):
    """兼容带/不带 .json 后缀、以及只给文件名主干的调用。"""
    原 = str(名 or "").strip()
    candidates = [夹具目录 / 原]
    if not 原.lower().endswith(".json"):
        candidates.append(夹具目录 / f"{原}.json")
    for p in candidates:
        if p.is_file():
            return p
    # 最后按主干在夹具目录内精确匹配一次（避免路径穿越，只取 name）。
    主干 = Path(原).name
    for p in 夹具目录.glob("*.json") if 夹具目录.exists() else []:
        if p.stem == 主干 or p.name == 主干:
            return p
    raise HTTPException(404, f"夹具不存在: {名}")


def _装载夹具文件(名: str):
    path = _解析夹具路径(名)
    data = json.loads(path.read_text(encoding="utf-8"))
    # 可选：夹具自带台参/预算口径/故障注入配置（耗尽、双工作者、迟到补抽等）。
    conn = 连接()
    try:
        台参批 = data.get("台参") if isinstance(data.get("台参"), dict) else None
        if 台参批:
            for k, v in 台参批.items():
                conn.execute(
                    "INSERT INTO 台参(键,值) VALUES(?,?) "
                    "ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                    (str(k), str(v)),
                )
            conn.commit()
    finally:
        conn.close()

    营业日 = str(data.get("营业日") or data.get("目标营业日") or "")
    cap = data.get("预算上限", data.get("上限"))
    口径 = data.get("口径") or data.get("耗尽口径")
    if 营业日 and (cap is not None or 口径 is not None or data.get("重置预算")):
        预算.配置预算(营业日, 上限=int(cap) if cap is not None else None, 口径=口径)
    故障 = data.get("故障注入", data.get("故障"))
    if 营业日 and 故障 is not None:
        if isinstance(故障, dict):
            n = 故障.get("前N次失败", 故障.get("次数", 故障.get("失败次数", 2)))
            预算.设置故障(营业日, int(n))
        elif 故障 is True or str(故障) in ("失败", "1", "always"):
            预算.设置故障(营业日, 10 ** 6)
        elif isinstance(故障, int):
            预算.设置故障(营业日, int(故障))

    流水 = data.get("流水") or data.get("事件") or []
    报告 = 装载.装载流水(流水, str(data.get("批次编号") or ""))
    if 营业日:
        try:
            报告["核证"] = 预算.预算核证(营业日)
        except Exception:
            pass
    return 报告


@路由.post("/抽取")
@路由.post("/重试")
@路由.post("/抽取重试预算")
def 执行抽取(体: 抽取请求):
    额外体 = getattr(体, "model_extra", None) or {}
    日 = 体.营业日 or 额外体.get("日") or 额外体.get("day") or ""
    if not 日:
        raise HTTPException(status_code=422, detail="必须提供 营业日")
    批 = 体.批次编号 or 额外体.get("批次") or 额外体.get("批") or 额外体.get("batch") or ""
    try:
        选项 = {k: v for k, v in (
            ("上限", 体.上限), ("口径", 体.口径), ("工作者", 体.工作者 or None),
            ("故障", 体.故障), ("来源", 体.来源),
        ) if v is not None}
        r = 抽取.日终抽取(str(日), str(批), **选项)
    except 预算.预算耗尽错误 as e:
        raise HTTPException(status_code=400, detail={"错误码": "预算耗尽", "消息": str(e), "核证": e.核证}) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    # 服务层默认软返回：把失败/拒绝标记翻译成 4xx，保持 REST 语义。
    if isinstance(r, dict):
        if r.get("拒绝") or r.get("预算耗尽"):
            d = dict(r)
            d["错误码"] = r.get("错误码") or "预算耗尽"
            d["消息"] = r.get("消息") or r.get("错误") or "预算耗尽"
            d.setdefault("拒绝", True)
            d.setdefault("预算耗尽", True)
            d.setdefault("禁止写正式", True)
            d.setdefault("HTTP状态", 400)
            d.setdefault("核证", r.get("核证"))
            raise HTTPException(status_code=400, detail=d)
        if r.get("失败"):
            d = dict(r)
            d["错误码"] = r.get("错误码") or "抽取主体失败"
            d["消息"] = r.get("消息") or r.get("错误") or "抽取失败，已整票回滚"
            d.setdefault("失败", True)
            d.setdefault("回滚", True)
            d.setdefault("整票回滚", True)
            d.setdefault("HTTP状态", 400)
            d.setdefault("核证", r.get("核证"))
            raise HTTPException(status_code=400, detail=d)
    return r


# ---------------------------------------------------------------------------
# 抽取重试预算：账本 / 核证 / 提额 / 诊断晋升
# ---------------------------------------------------------------------------
def _解析营业日(*vals) -> str:
    for v in vals:
        if v:
            return str(v)
    raise HTTPException(422, "必须提供 营业日")


@路由.get("/预算核证")
@路由.get("/预算核证接口")
@路由.get("/核证接口")
@路由.get("/certify")
@路由.get("/抽取重试预算")
@路由.get("/budget/certify")
@路由.get("/budget/verify")
def 取预算核证(营业日: str = "", 日: str = "", day: str = ""):
    目标 = 营业日 or 日 or day
    return 预算.预算核证(_解析营业日(目标) if 目标 else None)


@路由.get("/预算核证/{day}")
def 取预算核证路径(day: str):
    return 预算.预算核证(day)


@路由.get("/核证")
def 取核证(营业日: str = ""):
    return 预算.预算核证(营业日 or None)


@路由.get("/抽取重试预算/核证")
def 取预算核证别名(营业日: str = ""):
    return 预算.预算核证(营业日 or None)


@路由.get("/预算账本")
def 列预算账本(营业日: str = ""):
    conn = 连接()
    try:
        预算.建表(conn)
        if 营业日:
            r = conn.execute("SELECT * FROM 预算账本 WHERE 营业日=?", (营业日,)).fetchone()
            return dict(r) if r else 预算.取账本(营业日)
        rows = conn.execute("SELECT * FROM 预算账本 ORDER BY 更新时刻 DESC LIMIT 200").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


@路由.get("/预算")
def 列预算(营业日: str = ""):
    return 列预算账本(营业日)


@路由.get("/预算账录")
def 列预算账录(营业日: str):
    conn = 连接()
    try:
        预算.建表(conn)
        return [dict(r) for r in conn.execute(
            "SELECT * FROM 预算账录 WHERE 营业日=? ORDER BY 编号", (营业日,)
        )]
    finally:
        conn.close()


def _从体取(体: dict, *键名, default=None):
    for k in 键名:
        if k in 体 and 体[k] not in (None, ""):
            return 体[k]
    return default


@路由.post("/预算上限")
async def 设预算上限(request: Request):
    体 = await _体(request)
    日 = _从体取(体, "营业日", "日", "day")
    cap = _从体取(体, "上限", "新上限", "预算上限", "limit", "cap")
    口径 = _从体取(体, "口径", "模式", "耗尽口径")
    if not 日:
        raise HTTPException(422, "必须提供 营业日")
    try:
        cap = int(cap) if cap is not None else None
    except (TypeError, ValueError) as e:
        raise HTTPException(400, "上限必须为整数") from e
    return 预算.配置预算(str(日), 上限=cap, 口径=口径)


@路由.post("/提高预算")
async def 提高预算(request: Request):
    return await 设预算上限(request)


@路由.post("/配置预算")
async def 配置预算路由(request: Request):
    return await 设预算上限(request)


@路由.post("/预算重置")
async def 预算重置路由(request: Request):
    体 = await _体(request)
    日 = _从体取(体, "营业日", "日")
    return 预算.重置预算(str(日) if 日 else None)


@路由.post("/故障注入")
async def 故障注入路由(request: Request):
    体 = await _体(request)
    日 = _从体取(体, "营业日", "日")
    次 = int(_从体取(体, "次数", "前N次失败", "n", default=2))
    if not 日:
        raise HTTPException(422, "必须提供 营业日")
    return 预算.设置故障(str(日), 次)


@路由.post("/诊断晋升")
async def 诊断晋升路由(request: Request):
    体 = await _体(request)
    try:
        return 预算.晋升正式(
            str(_从体取(体, "营业日", "日", default="") or ""),
            诊断批号=str(_从体取(体, "诊断批号", "批号", default="") or ""),
            快照键=str(_从体取(体, "快照键", default="") or ""),
            审批编号=str(_从体取(体, "审批编号", "审批", "approval", default="") or ""),
            批准=bool(_从体取(体, "批准", "approved", default=False)),
            审批人=str(_从体取(体, "审批人", default="") or ""),
        )
    except 预算.晋升审批错误 as e:
        raise HTTPException(403, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/台参")
async def 设台参(request: Request):
    """upsert 台参键值（便于配置预算上限/口径/故障注入）。"""
    体 = await _体(request)
    键 = _从体取(体, "键", "key")
    值 = _从体取(体, "值", "value")
    批量 = 体.get("台参") if isinstance(体.get("台参"), dict) else None
    conn = 连接()
    try:
        if 批量:
            for k, v in 批量.items():
                conn.execute(
                    "INSERT INTO 台参(键,值) VALUES(?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                    (str(k), str(v)),
                )
        elif 键:
            conn.execute(
                "INSERT INTO 台参(键,值) VALUES(?,?) ON CONFLICT(键) DO UPDATE SET 值=excluded.值",
                (str(键), "" if 值 is None else str(值)),
            )
        else:
            raise HTTPException(422, "需提供 键/值 或 台参 对象")
        conn.commit()
        return {r["键"]: r["值"] for r in conn.execute("SELECT 键, 值 FROM 台参")}
    finally:
        conn.close()


@路由.post("/回放")
def 执行回放(体: 营业日请求):
    try:
        return 回放.回放核对(体.营业日, 体.批次编号)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@路由.post("/重置业务库")
def 重置():
    """开发冒烟用：清业务表并保留账户与台参。"""
    conn = 连接()
    try:
        for t in (
            "变动流水",
            "抽取批次",
            "周期快照",
            "回放批次",
            "差额行",
            "断档记录",
            "跳变记录",
            "旁置簿",
            "装载批次",
            "预算账录",
            "诊断快照",
            "诊断批次",
            "故障注入闸",
            "预算账本",
        ):
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
        return {"完成": True, **目录.总览()}
    finally:
        conn.close()
