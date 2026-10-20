# -*- coding: utf-8 -*-
"""
main.py —— FastAPI 后端入口
============================
一、提供的接口
    GET  /api/health  ：健康检查，返回后端版本号；
    GET  /api/sample  ：直接用后端自带的两份样例数据完成计算并返回 JSON
                        （对应“前端一键导入样例数据”，免去手动选文件）；
    POST /api/analyze ：接收前端上传的 1~N 个观测 CSV，解析计算后返回 JSON；
    POST /api/report  ：接收同样的 CSV，生成并下载 .docx 研究报告。
二、前后端联调方式
    开发模式：前端执行 npm run dev（Vite，端口 5173），通过代理把 /api 转发到本服务（8000）；
    生产模式：前端 npm run build 后，本程序会自动托管 frontend/dist 目录，
              浏览器直接访问 http://127.0.0.1:8000 即可打开完整系统。
启动命令：uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
"""

import os
from urllib.parse import quote
from typing import List

from fastapi import FastAPI, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pathlib import Path

from . import __app_version__
from .parsers import parse_observation_csv
from .deformation import analyze_all
from .report import build_report

app = FastAPI(
    title="地表移动变形监测分析系统 API",
    description="上传水准/全站仪观测 CSV，计算下沉、倾斜、曲率、水平移动、水平变形并生成研究报告",
    version=__app_version__,
)

# 允许前端开发服务器跨域访问；本作业为本地单机系统，直接放开所有来源
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# 关键目录：HF 部署为扁平结构，app/main.py 的上一级即工程根
# 根目录下直接放 sample_data/（样例数据）和 static/（前端构建产物）
BASE_DIR = Path(__file__).resolve().parent.parent          # 工程根
SAMPLE_DIR = BASE_DIR / "sample_data"                       # 自带样例数据
DIST_DIR = BASE_DIR / "static"                              # 前端构建产物


async def _parse_uploads(files: List[UploadFile]):
    """统一处理上传文件：逐个解析，返回 (parsed_lines, 错误信息列表)。"""
    parsed_lines, errors = [], []
    for f in files:
        content = await f.read()
        if not content:
            errors.append(f"文件【{f.filename}】内容为空，已跳过。")
            continue
        try:
            parsed_lines.append(parse_observation_csv(content, f.filename))
        except Exception as exc:  # 单个文件失败不影响其他文件
            errors.append(f"文件【{f.filename}】解析失败：{exc}")
    return parsed_lines, errors


@app.get("/api/health", tags=["系统"])
async def health():
    """健康检查接口：前端据此判断后端是否已启动。"""
    return {"code": 0, "version": __app_version__, "status": "running"}


@app.get("/api/sample", tags=["数据分析"])
async def analyze_sample():
    """读取后端 sample_data 下自带的两份样例 CSV 并完成全套计算。"""
    parsed_lines = []
    for name in ("水准.csv", "全站仪.csv"):
        path = SAMPLE_DIR / name
        if path.exists():
            parsed_lines.append(parse_observation_csv(path.read_bytes(), name))
    results = analyze_all(parsed_lines)
    return {"code": 0, "results": results, "errors": [], "sample": True}


@app.post("/api/analyze", tags=["数据分析"])
async def analyze(files: List[UploadFile] = File(..., description="水准/全站仪观测CSV，可多选")):
    """解析并计算上传的观测文件，返回绘图与特征点所需的全部 JSON 数据。"""
    parsed_lines, errors = await _parse_uploads(files)
    if not parsed_lines:
        return JSONResponse(status_code=400,
                            content={"code": 1, "msg": "没有可计算的有效文件。", "errors": errors})
    results = analyze_all(parsed_lines)
    return {"code": 0, "results": results, "errors": errors}


@app.post("/api/report", tags=["报告生成"])
async def make_report(files: List[UploadFile] = File(..., description="观测CSV，可多选"),
                      title: str = Form("地表移动观测站变形监测分析报告"),
                      author: str = Form("")):
    """根据上传文件生成 .docx 研究报告并以附件形式回传下载。"""
    parsed_lines, errors = await _parse_uploads(files)
    if not parsed_lines:
        return JSONResponse(status_code=400,
                            content={"code": 1, "msg": "没有可生成报告的有效文件。", "errors": errors})
    results = analyze_all(parsed_lines)
    docx_bytes = build_report(results, title=title, author=author)

    def iter_bytes():
        # 用生成器分块输出，避免大报告一次性占用过多内存
        yield docx_bytes

    filename = "变形监测分析报告.docx"
    # 中文文件名需要按 RFC5987 做百分号编码，响应头才允许传输、浏览器下载才不会乱码
    headers = {"Content-Disposition":
               f"attachment; filename=report.docx; filename*=UTF-8''{quote(filename)}"}
    return StreamingResponse(iter_bytes(),
                             media_type="application/vnd.openxmlformats-officedocument."
                                        "wordprocessingml.document",
                             headers=headers)


@app.get("/api/sample-report", tags=["报告生成"])
async def sample_report(author: str = ""):
    """用自带样例数据直接生成一份报告，方便快速预览报告效果。"""
    parsed_lines = []
    for name in ("水准.csv", "全站仪.csv"):
        path = SAMPLE_DIR / name
        if path.exists():
            parsed_lines.append(parse_observation_csv(path.read_bytes(), name))
    results = analyze_all(parsed_lines)
    docx_bytes = build_report(results, author=author)
    return StreamingResponse(iter([docx_bytes]),
                             media_type="application/vnd.openxmlformats-officedocument."
                                        "wordprocessingml.document",
                             headers={"Content-Disposition":
                                      "attachment; filename=report.docx; "
                                      f"filename*=UTF-8''{quote('样例数据变形监测分析报告.docx')}"})


# ---------- 生产模式：托管前端构建产物 dist（必须放在所有 /api 路由之后注册） ----------
if DIST_DIR.exists():
    # 静态资源目录（js/css/图片）
    app.mount("/assets",
              StaticFiles(directory=str(DIST_DIR / "assets")),
              name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str):
        # 单页应用回退：任何非 /api 路径都返回 index.html，由前端路由接管
        candidate = DIST_DIR / full_path
        if full_path and candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(DIST_DIR / "index.html"))
else:
    @app.get("/", include_in_schema=False)
    async def hint():
        # 前端尚未构建时给出友好提示
        return {"msg": "后端已启动。前端开发模式请在 frontend 目录执行 npm run dev；"
                       "或执行 npm run build 后由本服务托管页面。",
                "docs": "/docs"}
