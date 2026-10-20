# 地表移动变形监测分析系统

Vue3 + FastAPI 前后端分离的地表移动变形监测数据分析平台。上传水准/全站仪观测 CSV，自动计算下沉 W、倾斜 i、曲率 K、水平移动 U、水平变形 ε，ECharts 交互式绘图，一键生成 .docx 报告。

## 本地运行

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 10000
```

前端构建产物已打包在 `static/` 目录，由 FastAPI 直接托管。

## 目录结构

```
├── Dockerfile          # 容器构建
├── requirements.txt    # Python 依赖
├── app/                # FastAPI 后端
│   ├── main.py
│   ├── parsers.py
│   ├── deformation.py
│   └── report.py
├── sample_data/        # 内置样例 CSV
└── static/             # 前端构建产物（Vue3 + ECharts）
```
