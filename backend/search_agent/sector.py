"""板块分类逻辑

根据公司业务描述分析所属板块。
使用 LLM 辅助分析，但核心逻辑由规则驱动。
"""

# A 股常见板块分类
SECTOR_CATEGORIES = [
    "半导体",
    "AI算力",
    "新能源",
    "光伏",
    "风电",
    "锂电池",
    "消费电子",
    "汽车零部件",
    "白酒",
    "食品饮料",
    "医药",
    "医疗器械",
    "金融",
    "银行",
    "券商",
    "保险",
    "地产",
    "基建",
    "建材",
    "钢铁",
    "有色",
    "化工",
    "煤炭",
    "电力",
    "通信",
    "计算机软件",
    "国防军工",
    "农林牧渔",
    "纺织服装",
    "家电",
    "传媒",
    "旅游",
    "交通运输",
    "物流",
    "环保",
    "教育",
    "精密制造",
    "机器人",
]

# 关键词 → 板块映射（快速匹配）
KEYWORD_SECTOR_MAP = {
    "算力": "AI算力",
    "GPU": "AI算力",
    "芯片": "半导体",
    "半导体": "半导体",
    "光伏": "光伏",
    "太阳能": "光伏",
    "锂电": "锂电池",
    "电池": "锂电池",
    "新能源车": "新能源",
    "汽车零部件": "汽车零部件",
    "白酒": "白酒",
    "医药": "医药",
    "制药": "医药",
    "药品": "医药",
    "抗肿瘤": "医药",
    "肿瘤": "医药",
    "创新药": "医药",
    "保险": "保险",
    "银行": "银行",
    "证券": "券商",
    "地产": "地产",
    "房地产": "地产",
    "煤炭": "煤炭",
    "电力": "电力",
    "钢铁": "钢铁",
    "化工": "化工",
    "军工": "国防军工",
    "机器人": "机器人",
    "软件": "计算机软件",
    "通信": "通信",
    "5G": "通信",
    "家电": "家电",
    "传媒": "传媒",
    "物流": "物流",
    "环保": "环保",
    "AI": "AI算力",
    "人工智能": "AI算力",
    "云计算": "AI算力",
    "数据中心": "AI算力",
    "金属结构件": "精密制造",
    "精密制造": "精密制造",
    "食品": "食品饮料",
    "饮料": "食品饮料",
    "纺织": "纺织服装",
    "服装": "纺织服装",
    "基建": "基建",
    "建筑": "基建",
    "建材": "建材",
    "农业": "农林牧渔",
    "养殖": "农林牧渔",
    "旅游": "旅游",
    "教育": "教育",
    "交通": "交通运输",
    "航空": "交通运输",
    "医疗器械": "医疗器械",
}


def match_sectors_from_text(business_text: str) -> list[str]:
    """从业务描述文本中匹配板块（关键词匹配）"""
    matched = set()
    text_lower = business_text.lower()

    for keyword, sector in KEYWORD_SECTOR_MAP.items():
        if sector in {"银行", "保险", "券商", "金融"} and not _is_core_finance_context(keyword, business_text):
            continue
        if keyword.lower() in text_lower:
            matched.add(sector)

    # 如果没有匹配的，标记为"其他"
    if not matched:
        matched.add("其他")

    return sorted(matched)


def extract_keywords(business_text: str) -> list[str]:
    """从业务描述中提取关键词"""
    keywords = []
    text_lower = business_text.lower()

    for kw in KEYWORD_SECTOR_MAP:
        sector = KEYWORD_SECTOR_MAP[kw]
        if sector in {"银行", "保险", "券商", "金融"} and not _is_core_finance_context(kw, business_text):
            continue
        if kw.lower() in text_lower:
            keywords.append(kw)

    return sorted(set(keywords))[:10]  # 最多10个关键词


def _is_core_finance_context(keyword: str, text: str) -> bool:
    """避免把“银行贷款/证券简称”等非主营业务词误判为金融板块。"""
    if keyword.lower() not in text.lower():
        return False
    finance_terms = (
        "商业银行", "银行业务", "存贷款", "信贷业务", "金融服务", "金融机构",
        "证券经纪", "投行业务", "资产管理", "券商", "保险业务", "保费", "承保",
    )
    return any(t in text for t in finance_terms)
