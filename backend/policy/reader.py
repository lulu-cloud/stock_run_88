"""读取缓存的宏观政策文件，提取产业政策信号。

数据源：data/policy_docs/ 下的发改委/工信部/财政部 MD 文件。
"""

import os
import re

from backend.config import DATA_DIR

POLICY_DIR = os.path.join(DATA_DIR, "policy_docs")

KEYWORD_INDUSTRY_MAP = {
    "新能源汽车": "汽车",
    "新能源": "电力设备",
    "光伏": "电力设备",
    "风电": "电力设备",
    "储能": "电力设备",
    "锂电池": "电力设备",
    "集成电路": "电子",
    "芯片": "电子",
    "半导体": "电子",
    "人工智能": "计算机",
    "AI": "计算机",
    "大模型": "计算机",
    "算力": "计算机",
    "数字经济": "计算机",
    "机器人": "机械设备",
    "智能制造": "机械设备",
    "军工": "国防军工",
    "航空航天": "国防军工",
    "创新药": "医药生物",
    "生物医药": "医药生物",
    "医疗器械": "医药生物",
    "中药": "医药生物",
    "消费": "食品饮料",
    "白酒": "食品饮料",
    "家电": "家用电器",
    "房地产": "房地产",
    "基建": "建筑装饰",
    "5G": "通信",
    "通信": "通信",
    "银行": "银行",
    "证券": "非银金融",
    "保险": "非银金融",
    "电力": "公用事业",
    "煤炭": "煤炭",
    "石油": "石油石化",
    "钢铁": "钢铁",
    "有色": "有色金属",
    "化工": "基础化工",
    "农业": "农林牧渔",
    "环保": "环保",
    "碳中和": "环保",
}

SOURCE_LABELS = {
    "发改委": "国家发展和改革委员会",
    "工信部": "工业和信息化部",
    "财政部": "中华人民共和国财政部",
}


def _find_policy_files() -> list[dict]:
    """扫描 data/policy_docs/ 下所有 MD 文件。"""
    if not os.path.exists(POLICY_DIR):
        return []

    files = []
    for source in os.listdir(POLICY_DIR):
        source_dir = os.path.join(POLICY_DIR, source)
        if not os.path.isdir(source_dir):
            continue
        source_label = SOURCE_LABELS.get(source, source)
        for fname in os.listdir(source_dir):
            if fname.endswith(".md"):
                files.append({
                    "source": source_label,
                    "source_dir": source,
                    "filepath": os.path.join(source_dir, fname),
                    "filename": fname,
                })
    files.sort(key=lambda f: f["filename"], reverse=True)
    return files


def read_recent_policies(limit: int = 20) -> list[dict]:
    """读取最近的政策文件列表（仅标题和来源，不读全文）。"""
    files = _find_policy_files()[:limit]
    result = []
    for f in files:
        with open(f["filepath"], "r", encoding="utf-8") as fh:
            first_line = fh.readline().strip().lstrip("#").strip()
        # 从文件名提取日期
        date_str = f["filename"][:8] if len(f["filename"]) >= 8 else ""
        result.append({
            "title": first_line or f["filename"].replace(".md", ""),
            "source": f["source"],
            "date": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if date_str.isdigit() else "",
        })
    return result


def extract_policy_signals(recency_days: int = 14) -> dict:
    """从政策文件中提取产业政策信号（仅分析近期文件）。

    对于 Agent 决策，只读取最近 recency_days 天内的政策文件，
    以确保信号时效性。

    Args:
        recency_days: 只分析最近 N 天内的文件，默认14天

    Returns:
        {
            "top_industries": [{industry, strength, keywords, doc_count}],
            "recent_policies": [{title, source, date}],
            "summary": "一句话总结"
        }
    """
    from datetime import datetime, timedelta

    all_files = _find_policy_files()
    if not all_files:
        return {"top_industries": [], "recent_policies": [], "summary": "暂无宏观政策数据"}

    cutoff = datetime.now() - timedelta(days=recency_days)

    # Filter to recent files only
    files = []
    for f in all_files:
        date_str = f["filename"][:8] if len(f["filename"]) >= 8 else ""
        if date_str.isdigit():
            try:
                file_date = datetime.strptime(date_str, "%Y%m%d")
                if file_date >= cutoff:
                    files.append(f)
            except ValueError:
                files.append(f)  # Include if date can't be parsed
        else:
            files.append(f)

    if not files:
        return {
            "top_industries": [],
            "recent_policies": [],
            "summary": f"最近{recency_days}天内暂无新的宏观政策数据",
        }

    industry_signals = {}
    recent_policies = []

    for f in files:
        try:
            with open(f["filepath"], "r", encoding="utf-8") as fh:
                content = fh.read()
        except Exception:
            continue

        lines = content.strip().split("\n")
        title = lines[0].lstrip("#").strip() if lines else f["filename"]

        date_str = f["filename"][:8] if len(f["filename"]) >= 8 else ""
        date = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}" if date_str.isdigit() else ""

        recent_policies.append({
            "title": title,
            "source": f["source"],
            "date": date,
        })

        for keyword, industry in KEYWORD_INDUSTRY_MAP.items():
            count = len(re.findall(re.escape(keyword), content, re.IGNORECASE))
            if count > 0:
                if industry not in industry_signals:
                    industry_signals[industry] = {
                        "strength": 0.0,
                        "keywords": [],
                        "doc_count": 0,
                        "documents": [],
                    }
                industry_signals[industry]["strength"] += count
                if keyword not in industry_signals[industry]["keywords"]:
                    industry_signals[industry]["keywords"].append(keyword)
                if title not in industry_signals[industry]["documents"]:
                    industry_signals[industry]["documents"].append(title)
                    industry_signals[industry]["doc_count"] += 1

    max_s = max((s["strength"] for s in industry_signals.values()), default=1)
    if max_s > 0:
        for s in industry_signals.values():
            s["strength"] = round(s["strength"] / max_s, 2)

    sorted_industries = sorted(
        industry_signals.items(),
        key=lambda x: x[1]["strength"],
        reverse=True,
    )
    top = [{"industry": ind, **data} for ind, data in sorted_industries[:10]]

    if top:
        top_names = [t["industry"] for t in top[:5]]
        summary = f"近期宏观政策重点关注: {'、'.join(top_names)}等板块，可能产生政策利好"
    else:
        summary = "近期暂无明确的产业政策信号"

    return {
        "top_industries": top,
        "recent_policies": recent_policies[:20],
        "summary": summary,
        "recency_days": recency_days,
        "analyzed_count": len(files),
    }
