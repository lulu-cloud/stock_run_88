"""Stock sector/industry tag helpers."""

import os
import pandas as pd

from backend.config import DATA_DIR
from backend.search_agent.sector import match_sectors_from_text


def _clean_industry(value: str) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return "其他"
    # Baostock cache commonly prefixes industry codes, e.g. J66货币金融服务.
    return text[3:] if len(text) > 3 and text[0].isalpha() and text[1:3].isdigit() else text


def load_tag_map() -> dict[str, dict]:
    """Load engineering tags from industry cache and company business cache."""
    result: dict[str, dict] = {}
    industry_path = os.path.join(DATA_DIR, "stock_industry_cache.csv")
    if os.path.exists(industry_path):
        df = pd.read_csv(industry_path, encoding="utf-8-sig")
        for _, row in df.iterrows():
            ts_code = row.get("ts_code")
            if not ts_code:
                continue
            industry = _clean_industry(row.get("industry", ""))
            result[str(ts_code)] = {
                "industry_tag": industry,
                "sector_tag": industry if industry != "其他" else "其他",
            }

    business_dir = os.path.join(DATA_DIR, "company_business")
    if os.path.isdir(business_dir):
        for filename in os.listdir(business_dir):
            if not filename.endswith(".md"):
                continue
            ts_code = filename.split("_")[0].replace(".md", "")
            path = os.path.join(business_dir, filename)
            try:
                with open(path, "r", encoding="utf-8") as f:
                    sectors = match_sectors_from_text(f.read())
                if sectors:
                    item = result.setdefault(ts_code, {"industry_tag": "其他", "sector_tag": "其他"})
                    item["sector_tag"] = sectors[0]
            except Exception:
                continue
    return result
