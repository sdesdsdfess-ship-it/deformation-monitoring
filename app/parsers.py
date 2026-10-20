# -*- coding: utf-8 -*-
"""
parsers.py —— 原始观测 CSV 解析模块
====================================
一、模块职责
    把外业观测得到的两种 CSV 文件解析成统一的 Python 字典结构，供 deformation.py 计算。
    本模块只做“读数据、辨格式、排错误”三件事，不做任何变形计算。

二、支持的两种文件格式
    1) 水准观测文件（如“水准.csv”）：
       每个监测点只有首期平面坐标 X、Y，之后各期只有高程 H。
       表头行形如： 0, x, Y, Z, Z, Z, ...（Z 的个数 = 观测期数）
    2) 全站仪观测文件（如“全站仪.csv”）：
       每一期都同时测量平面坐标 X、Y 和高程 H，共若干期。
       表头行形如： 221, X, Y, Z, X, Y, Z, ...（X/Y/Z 成组重复）

三、统一输出结构（字典）
    {
        "source_type": "leveling" / "total_station",  # 文件类型
        "line_name":   观测线名称,
        "dates":       [各观测期日期字符串],
        "points": [                      # 按 CSV 中的先后顺序排列，即沿观测线方向
            {"name": 点号, "x": X, "y": Y,
             "heights": [各期高程(m)],    # 水准与全站仪都有
             "plan_xy": [(各期X, 各期Y)]} # 仅全站仪逐期填充；水准只有首期
        ]
    }
"""

import csv
import io
import re
from typing import List, Dict, Any

# 日期正则：匹配 2019-2-14 / 2019/2/14 这类写法，提取年、月、日
_DATE_RE = re.compile(r"^\s*(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})\s*$")


def _normalize_date(text: str) -> str:
    """把各种写法的日期统一规范成“年-两位月-两位日”，例如 2019-2-14 → 2019-02-14。"""
    m = _DATE_RE.match(text or "")
    if not m:
        return text.strip()
    year, month, day = m.groups()
    return f"{int(year):04d}-{int(month):02d}-{int(day):02d}"


def _to_float(text: str):
    """把单元格安全地转成浮点数；空单元格或非数字返回 None，而不是直接抛异常中断解析。"""
    if text is None:
        return None
    text = text.strip()
    if text == "":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _read_rows(file_bytes: bytes) -> List[List[str]]:
    """把上传的字节流按 CSV 解析成二维列表；用 utf-8-sig 自动去掉可能存在的 BOM 头。"""
    # errors='ignore' 用来容忍个别异常字节，保证外业文件不会因为编码小问题整体读不出来
    text = file_bytes.decode("utf-8-sig", errors="ignore")
    # 用 StringIO 包一层，csv.reader 才能像读文件一样逐行读取
    reader = csv.reader(io.StringIO(text))
    return [row for row in reader]


def parse_observation_csv(file_bytes: bytes,
                          file_name: str = "未命名文件") -> Dict[str, Any]:
    """
    解析一份观测 CSV，返回统一结构的字典。

    参数
    ----
    file_bytes : 上传文件的原始字节
    file_name  : 文件名，仅用于报错提示与默认观测线命名

    抛出
    ----
    ValueError : 当文件里找不到 X/Y/Z 表头、或数据行不足以构成计算时抛出，
                 由上层 main.py 捕获后转成友好的中文错误提示。
    """
    rows = _read_rows(file_bytes)

    # ---------- 第一步：找到 X/Y/Z 所在的表头行，并据此判别文件类型 ----------
    header_idx = -1
    header_row: List[str] = []
    for i, row in enumerate(rows):
        # 把该行所有单元格转成大写后，统计 X 出现的次数
        upper_cells = [c.strip().upper() for c in row]
        if "X" in upper_cells and "Y" in upper_cells and "Z" in upper_cells:
            header_idx = i
            header_row = upper_cells
            break
    if header_idx < 0:
        raise ValueError(f"文件【{file_name}】中未找到包含 X、Y、Z 的表头行，无法识别。")

    # 表头中 X 出现几次，就代表有几组成对坐标：
    #   水准文件只有首期一组 X、Y → X 只出现 1 次；
    #   全站仪文件每期都有 X、Y、Z → X 会重复出现多次。
    x_count = header_row.count("X")
    z_count = header_row.count("Z")
    if x_count >= 2:
        source_type = "total_station"
        # 全站仪：观测期数 = (X,Y,Z) 坐标组的组数，也就是 X 出现的次数
        std_epochs = x_count
    else:
        source_type = "leveling"
        # 水准：首期占一组 X、Y，之后每期一个 Z，故期数 = Z 出现的次数
        std_epochs = z_count

    # ---------- 第二步：从表头的上一行提取观测日期 ----------
    # 注意：全站仪文件右侧还附有“下沉/水平移动”等手算列，里面会重复出现日期，
    # 因此只取前 std_epochs 个日期，避免把表尾辅助列误当成新的观测期。
    dates: List[str] = []
    if header_idx - 1 >= 0:
        for cell in rows[header_idx - 1]:
            if _DATE_RE.match(cell or ""):
                dates.append(_normalize_date(cell))
            if len(dates) == std_epochs:
                break

    # ---------- 第三步：按类型逐行提取监测点数据 ----------
    points: List[Dict[str, Any]] = []

    if source_type == "leveling":
        # 水准格式：点号, X, Y, 第1期Z, 第2期Z, ...
        for row in rows[header_idx + 1:]:
            # 点号为空说明遇到空行或表尾的辅助内容，直接停止
            if not row or not row[0].strip():
                continue
            x = _to_float(row[1]) if len(row) > 1 else None
            y = _to_float(row[2]) if len(row) > 2 else None
            # X、Y 必须是数字，否则不是有效数据行
            if x is None or y is None:
                continue
            # 只取前 std_epochs 个高程，忽略表尾可能存在的其他辅助列
            heights = [_to_float(c) for c in row[3:3 + std_epochs]]
            if any(v is None for v in heights) or len(heights) < 2:
                # 高程缺项、或不足两期，都无法参与变形计算，跳过该行
                continue
            points.append({
                "name": row[0].strip(),
                "x": x, "y": y,
                "heights": heights,
                # 水准只有首期平面坐标，逐期平面坐标列表里只放一期
                "plan_xy": [(x, y)],
            })
        # 若日期行没读全，则用高程期数兜底，保证期数与数据对齐
        if len(dates) != len(heights):
            dates = dates[:len(heights)] if dates else []

    else:
        # 全站仪格式：点号, (X,Y,Z) × 期数
        for row in rows[header_idx + 1:]:
            if not row or not row[0].strip():
                # 全站仪表尾有大量空行和辅助列，遇到第一个空点号行即停止
                break
            # 只截取前 std_epochs 组坐标（每组 X、Y、Z 三个数），右侧手算列全部丢弃
            nums = [_to_float(c) for c in row[1:1 + 3 * std_epochs]]
            # 必须凑齐全部期数、每期 3 个坐标，否则视为无效行
            if len(nums) < 3 * std_epochs or any(v is None for v in nums):
                continue
            heights, plan_xy = [], []
            for k in range(std_epochs):
                xk, yk, zk = nums[3 * k], nums[3 * k + 1], nums[3 * k + 2]
                plan_xy.append((xk, yk))
                heights.append(zk)
            # 首期平面坐标作为该点的基准坐标
            points.append({
                "name": row[0].strip(),
                "x": plan_xy[0][0], "y": plan_xy[0][1],
                "heights": heights,
                "plan_xy": plan_xy,
            })

    if len(points) < 2:
        raise ValueError(f"文件【{file_name}】解析后有效监测点不足 2 个，无法计算沿观测线的变形。")

    # 二次保险：剔除期数不齐的个别点，保证后续“点 × 期”矩阵整齐
    points = [p for p in points if len(p["heights"]) == std_epochs]
    if len(points) < 2:
        raise ValueError(f"文件【{file_name}】期数齐全的有效监测点不足 2 个。")
    if len(dates) != std_epochs:
        # 日期缺失或与期数不一致时，用“第 k 期”占位，避免前端、报告列错位
        dates = (dates + [f"第{i + 1}期" for i in range(std_epochs)])[:std_epochs]

    # 观测线名称：表头之前形如“A线”“QX”的非数字标记；跳过“22411/221”这类纯数字编号，
    # 取最后一个有效名称（它紧挨表头行，最可靠）
    line_name = ""
    for row in rows[:header_idx]:
        cand = row[0].strip() if row else ""
        if cand and not cand.replace(".", "").isdigit():
            line_name = cand
    if not line_name:
        line_name = file_name.rsplit(".", 1)[0]

    return {
        "source_type": source_type,
        "line_name": line_name,
        "file_name": file_name,
        "dates": dates,
        "points": points,
    }
