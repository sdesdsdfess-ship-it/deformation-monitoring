# -*- coding: utf-8 -*-
"""
deformation.py —— 地表移动变形计算核心模块
============================================
本模块输入 parsers.py 解析好的单条观测线数据，输出全套变形指标与特征点（最值点）。

一、计算指标与公式（长度单位 m，变形单位见各项说明）
    设观测线上的监测点沿点号顺序为 P0, P1, ..., Pn-1，共 N 个点、N-1 个点间段，
    首期（t=0）为基准期，第 t 期高程为 H_i(t)。

    1. 下沉 W（mm）：高程下降为正
           W_i(t) = [ H_i(0) - H_i(t) ] × 1000
    2. 点间平距 l（m）：由相邻点首期平面坐标反算
           l_i = √[ (X_{i+1}-X_i)^2 + (Y_{i+1}-Y_i)^2 ]
       累计距起点距离 s_i = l_0 + l_1 + ... + l_{i-1}
    3. 倾斜 i（mm/m）：相邻两点下沉差除以平距。
       约定沿“点号增大方向”看，取上游点减下游点（与课程报告样例口径一致）：
           i_i(t) = [ W_i(t) - W_{i+1}(t) ] / l_i
    4. 曲率 K（mm/m²）：相邻两段倾斜之差，除以两段平均长度，结果落在中间点上：
           K_{i+1}(t) = [ i_i(t) - i_{i+1}(t) ] / [ (l_i + l_{i+1}) / 2 ]
    5. 水平移动 U（mm，仅全站仪）：把各期平面位移投影到观测线方向上。
       先求该点处观测线的局部走向方位角 α（内点用 P_{i-1}→P_{i+1}，端点用首/末段），
       位移分量 dX = [X_i(t)-X_i(0)]×1000，dY 同理；按样例约定，正方向取“指向观测线
       起点”一侧，故整体取负号：
           U_i(t) = -[ dX·cosα + dY·sinα ]
    6. 水平变形 ε（mm/m，仅全站仪）：相邻两点水平移动差除以平距：
           ε_i(t) = [ U_i(t) - U_{i+1}(t) ] / l_i
    7. 下沉速度 v（mm/d）：相邻两期间下沉增量除以间隔天数
           v_i(t) = [ W_i(t) - W_i(t-1) ] / Δdays

二、特征点（最值）
    对每一项指标，在“全部位置 × 全部观测期”的二维矩阵中找代数最大值、最小值，
    并记录其点号（或点对/三点组编号）、距起点距离与出现日期；另外给出绝对值最大者，
    供前端“最大下沉点/最大水平移动点/最倾斜点/曲率点”卡片直接展示。
"""

import math
from datetime import datetime
from typing import List, Dict, Any


def _date_obj(date_str: str):
    """把日期字符串转成 datetime；若不是合法日期则返回 None。"""
    try:
        return datetime.strptime(date_str, "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _round(value, ndigits=3):
    """安全四舍五入：None 原样返回，避免缺数据时计算崩溃。"""
    if value is None:
        return None
    return round(float(value), ndigits)


def _find_extrema(matrix, labels, distances, dates, ndigits=3):
    """
    在一个“位置 × 期次”的二维数值矩阵中寻找最大值、最小值与绝对值最大者。

    参数
    ----
    matrix    : matrix[r][t]，某指标在第 r 个位置、第 t 期的数值
    labels    : 每个位置 r 对应的名称（点号 / 点对 / 三点组）
    distances : 每个位置 r 对应的距起点距离(m)
    dates     : 每期 t 的日期字符串
    ndigits   : 结果保留小数位数
    """
    max_cell = min_cell = abs_cell = None

    def pack(r, t, value):
        return {
            "value": round(value, ndigits),
            "label": labels[r],
            "distance": round(distances[r], 1),
            "date": dates[t] if t < len(dates) else f"第{t + 1}期",
            "epoch_index": t,
        }

    for r, row in enumerate(matrix):
        for t, value in enumerate(row):
            if value is None:
                continue
            if max_cell is None or value > max_cell["value"]:
                max_cell = pack(r, t, value)
            if min_cell is None or value < min_cell["value"]:
                min_cell = pack(r, t, value)
            # 绝对值最大者代表“变形最剧烈”的位置，是前端最关心的特征点
            if abs_cell is None or abs(value) > abs(abs_cell["value"]):
                abs_cell = pack(r, t, value)
    return {"max": max_cell, "min": min_cell, "absmax": abs_cell}


def analyze_line(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    计算单条观测线的全套变形指标。

    参数
    ----
    parsed : parsers.parse_observation_csv 的返回结果

    返回
    ----
    嵌套字典，包含点/段/三点组的全部计算表、各项最值，以及供前端绘图的规整序列。
    """
    points = parsed["points"]
    dates = parsed["dates"]
    source_type = parsed["source_type"]
    n = len(points)                       # 监测点个数
    epoch_num = len(dates)               # 观测期数

    # ---------- 1. 点间平距 l 与“沿观测线方向”的里程 s ----------
    # 1.1 相邻点真实平距，倾斜/水平变形公式里的段长必须用它
    seg_len = []                          # seg_len[i]：P_i 与 P_{i+1} 之间的平距
    for i in range(n - 1):
        dx = points[i + 1]["x"] - points[i]["x"]
        dy = points[i + 1]["y"] - points[i]["y"]
        seg_len.append(math.hypot(dx, dy))

    # 1.2 求观测线的“设计主方向”：对全部点位做第一主成分（PCA）。
    #     直线观测线上各点近似沿某一方位布设，第一主成分方向即为该方位；
    #     这样水准线（近似沿坐标 Y 轴）里程≈Y 坐标增量，斜向的全站仪线里程=斜向投影，
    #     与课程报告样例的“距观测线起点距离”口径一致，且比逐段折线长更能反映设计里程。
    mean_x = sum(p["x"] for p in points) / n
    mean_y = sum(p["y"] for p in points) / n
    sxx = sum((p["x"] - mean_x) ** 2 for p in points)
    syy = sum((p["y"] - mean_y) ** 2 for p in points)
    sxy = sum((p["x"] - mean_x) * (p["y"] - mean_y) for p in points)
    # 二维散点第一主成分方向：用协方差矩阵特征向量公式直接解算
    theta = 0.5 * math.atan2(2.0 * sxy, sxx - syy)
    # 若主方向与某条坐标格网轴（0/90/180/270°）相差不超过 15°，说明观测线本就是
    # 沿格网方向设计、只是点位有少量横向摆动，此时里程直接沿该格网轴投影，
    # 与工程上“沿设计方向量距”的习惯一致；斜向测线则保留主成分方向。
    axis_deg = min((0, 90, 180, 270), key=lambda a: abs(((math.degrees(theta) - a + 180) % 360) - 180))
    diff = abs(((math.degrees(theta) - axis_deg + 180) % 360) - 180)
    if diff <= 15.0:
        theta = math.radians(axis_deg)
    dir_x, dir_y = math.cos(theta), math.sin(theta)
    # 方向符号要与“点号增大方向”一致（首点→末点投影应为正）
    if (points[-1]["x"] - points[0]["x"]) * dir_x + \
       (points[-1]["y"] - points[0]["y"]) * dir_y < 0:
        dir_x, dir_y = -dir_x, -dir_y
    # 各点沿主方向相对首点的投影里程
    proj = [(p["x"] - points[0]["x"]) * dir_x + (p["y"] - points[0]["y"]) * dir_y
            for p in points]
    p_min = min(proj)
    cum_dist = [v - p_min for v in proj]  # 让最小里程归零
    line_length = max(cum_dist) - min(cum_dist)        # 观测线沿主方向的总长度

    # ---------- 2. 下沉 W（mm） ----------
    subsidence = []                       # subsidence[i][t]
    for p in points:
        h0 = p["heights"][0]
        subsidence.append([(h0 - h) * 1000.0 for h in p["heights"]])

    # ---------- 3. 倾斜 i（mm/m），结果落在“点对”上 ----------
    seg_labels, seg_mid_dist, tilt = [], [], []
    for i in range(n - 1):
        seg_labels.append(f"{points[i]['name']}-{points[i + 1]['name']}")
        seg_mid_dist.append((cum_dist[i] + cum_dist[i + 1]) / 2.0)
        row = []
        for t in range(epoch_num):
            row.append((subsidence[i][t] - subsidence[i + 1][t]) / seg_len[i])
        tilt.append(row)

    # ---------- 4. 曲率 K（mm/m²），结果落在相邻三点的“中间点”上 ----------
    tri_labels, tri_mid_dist, curvature = [], [], []
    for i in range(n - 2):
        tri_labels.append(
            f"{points[i]['name']}-{points[i + 1]['name']}-{points[i + 2]['name']}")
        tri_mid_dist.append(cum_dist[i + 1])
        avg_len = (seg_len[i] + seg_len[i + 1]) / 2.0
        row = []
        for t in range(epoch_num):
            row.append((tilt[i][t] - tilt[i + 1][t]) / avg_len)
        curvature.append(row)

    # ---------- 5. 水平移动 U 与水平变形 ε（仅全站仪数据可算） ----------
    horizontal = None
    hstrain = None
    if source_type == "total_station":
        horizontal = [[] for _ in range(n)]
        for i, p in enumerate(points):
            # 局部走向：内点取前后两点连线方向，端点取所在的第一段/最后一段
            if i == 0:
                ax, ay = points[1]["x"] - p["x"], points[1]["y"] - p["y"]
            elif i == n - 1:
                ax, ay = p["x"] - points[i - 1]["x"], p["y"] - points[i - 1]["y"]
            else:
                ax = points[i + 1]["x"] - points[i - 1]["x"]
                ay = points[i + 1]["y"] - points[i - 1]["y"]
            norm = math.hypot(ax, ay)
            cos_a, sin_a = ax / norm, ay / norm      # 观测线局部走向的方向余弦
            for t in range(epoch_num):
                xt, yt = p["plan_xy"][t] if t < len(p["plan_xy"]) else p["plan_xy"][-1]
                dx_mm = (xt - p["x"]) * 1000.0       # X 方向位移(mm)
                dy_mm = (yt - p["y"]) * 1000.0       # Y 方向位移(mm)
                # 投影到观测线方向后取负：正号代表向观测线起点方向移动
                horizontal[i].append(-(dx_mm * cos_a + dy_mm * sin_a))

        hstrain = []
        for i in range(n - 1):
            row = [(horizontal[i][t] - horizontal[i + 1][t]) / seg_len[i]
                   for t in range(epoch_num)]
            hstrain.append(row)

    # ---------- 6. 下沉速度 v（mm/d） ----------
    d_objs = [_date_obj(d) for d in dates]
    gap_days = []                          # 相邻两期的间隔天数
    for t in range(1, epoch_num):
        if d_objs[t] and d_objs[t - 1]:
            gap_days.append((d_objs[t] - d_objs[t - 1]).days)
        else:
            gap_days.append(None)
    velocity = [[] for _ in range(n)]
    for i in range(n):
        for t in range(1, epoch_num):
            days = gap_days[t - 1]
            if days:
                velocity[i].append((subsidence[i][t] - subsidence[i][t - 1]) / days)
            else:
                velocity[i].append(None)
    velocity_dates = [f"{dates[t - 1]}~{dates[t]}" for t in range(1, epoch_num)]

    # ---------- 7. 汇总各点/段的输出表（统一保留小数位） ----------
    point_table = []
    for i, p in enumerate(points):
        point_table.append({
            "name": p["name"],
            "x": round(p["x"], 3),
            "y": round(p["y"], 3),
            "distance": round(cum_dist[i], 2),
            "heights": [_round(v, 3) for v in p["heights"]],
            "subsidence": [_round(v, 2) for v in subsidence[i]],
            "horizontal": [_round(v, 2) for v in horizontal[i]] if horizontal else None,
            "velocity": [_round(v, 4) for v in velocity[i]],
        })

    seg_table = []
    for i in range(n - 1):
        seg_table.append({
            "label": seg_labels[i],
            "length": round(seg_len[i], 2),
            "distance": round(seg_mid_dist[i], 2),
            "tilt": [_round(v, 3) for v in tilt[i]],
            "hstrain": [_round(v, 3) for v in hstrain[i]] if hstrain else None,
        })

    tri_table = []
    for i in range(n - 2):
        tri_table.append({
            "label": tri_labels[i],
            "distance": round(tri_mid_dist[i], 2),
            "curvature": [_round(v, 4) for v in curvature[i]],
        })

    # ---------- 8. 各项指标的特征点（最值） ----------
    point_names = [p["name"] for p in points]
    extrema = {
        "subsidence": _find_extrema(subsidence, point_names, cum_dist, dates, 2),
        "tilt": _find_extrema(tilt, seg_labels, seg_mid_dist, dates, 3),
        "curvature": _find_extrema(curvature, tri_labels, tri_mid_dist, dates, 4),
        "velocity": _find_extrema(velocity, point_names, cum_dist, velocity_dates, 4),
    }
    if horizontal is not None:
        extrema["horizontal"] = _find_extrema(horizontal, point_names, cum_dist, dates, 2)
        extrema["hstrain"] = _find_extrema(hstrain, seg_labels, seg_mid_dist, dates, 3)

    return {
        "line_name": parsed["line_name"],
        "source_type": source_type,
        "source_type_text": "水准观测线" if source_type == "leveling" else "全站仪观测线",
        "file_name": parsed.get("file_name", ""),
        "dates": dates,
        "velocity_dates": velocity_dates,
        "point_count": n,
        "segment_count": n - 1,
        "line_length": round(line_length, 1),
        "point_table": point_table,
        "seg_table": seg_table,
        "tri_table": tri_table,
        "extrema": extrema,
    }


def analyze_all(parsed_lines: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """批量计算：对解析出的多条观测线逐条调用 analyze_line。"""
    return [analyze_line(line) for line in parsed_lines]
