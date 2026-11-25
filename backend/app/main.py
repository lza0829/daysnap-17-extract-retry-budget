from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import seed
from app.routers import api

app = FastAPI(title="日镜账", version="0.1.0")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _启动():
    seed.初始化库()


app.include_router(api)


# 核证接口的根路径别名（部分评测/脚本可能不带 /api 前缀）。
from app.modules import 抽取重试预算 as _预算  # noqa: E402


@app.get("/预算核证")
@app.get("/预算核证接口")
@app.get("/certify")
def _根预算核证(营业日: str = "", 日: str = "", day: str = ""):
    目标 = 营业日 or 日 or day
    return _预算.预算核证(目标 or None)
