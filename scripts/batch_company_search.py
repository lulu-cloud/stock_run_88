#!/usr/bin/env python3
"""批量公司业务搜索 — 一次性工程

遍历所有主板股票，使用 LLM 生成业务描述 → 解析板块 → 保存 MD。
仅处理尚未缓存或缓存已过期的股票。支持中断续跑。

使用: cd /home/xulu/stock_run_88 && .venv/bin/python3 scripts/batch_company_search.py
"""

import os
import sys
import time
import json
import argparse
import pandas as pd
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.config import DATA_DIR, COMPANY_BUSINESS_DIR
from backend.search_agent.searcher import is_cached, get_freshness, save_with_date
from backend.search_agent.sector import match_sectors_from_text, extract_keywords
from backend.search_agent.minimax_search import MiniMaxCompanyResearchAgent

BATCH_SIZE = 50       # 每批处理数量，处理完一批后打印进度
DELAY_SECONDS = 1.5   # API 调用间隔
CACHE_FRESH_DAYS = 30  # 缓存有效期
RESEARCH_AGENT = MiniMaxCompanyResearchAgent()


def load_stocks() -> pd.DataFrame:
    """加载所有A股股票（主板+创业板+科创板+中小板，排除指数/ETF/基金等非个股）

    板块热度统计需要全市场覆盖，不受 Agent 交易的主板限制。
    """
    path = os.path.join(DATA_DIR, "stock_basic_cache.csv")
    df = pd.read_csv(path)
    # 按 market 精确过滤：只要个股，排除"其他"（指数/ETF/基金/B股等）
    valid_markets = {"主板", "中小板", "创业板", "科创板"}
    stocks = df[df["market"].isin(valid_markets)].copy()
    return stocks.reset_index(drop=True)


def list_pending_stocks(stocks: pd.DataFrame, force: bool = False) -> list[dict]:
    """筛选出需要处理的股票（无缓存或缓存过期）"""
    pending = []
    for _, row in stocks.iterrows():
        ts_code = row["ts_code"]
        name = row["name"]
        if force:
            pending.append({"ts_code": ts_code, "name": name})
            continue
        freshness = get_freshness(ts_code)
        if freshness is None or not freshness.get("is_fresh"):
            pending.append({"ts_code": ts_code, "name": name})
    return pending


def process_one(ts_code: str, name: str) -> bool:
    """处理单只股票：MiniMax 搜索 → MiniMax Agent 总结 → 解析板块 → 保存 MD"""
    result = RESEARCH_AGENT.run(ts_code, name)
    if not result.get("ok"):
        print(f"  MiniMax 搜索失败: {result.get('error') or result}")
        return False
    llm_response = result["content"]

    if not llm_response or len(llm_response.strip()) < 20:
        print(f"  LLM 返回内容过短")
        return False

    sectors = match_sectors_from_text(llm_response)
    keywords = extract_keywords(llm_response)

    try:
        filepath = save_with_date(ts_code, name, llm_response, sectors, keywords)
        print(f"  板块: {', '.join(sectors[:5])} | 关键词: {', '.join(keywords[:5])} | -> {os.path.basename(filepath)}")
        return True
    except Exception as e:
        print(f"  保存失败: {e}")
        return False


def main():
    parser = argparse.ArgumentParser(description="批量公司业务搜索")
    parser.add_argument("--force", action="store_true", help="强制重新生成所有缓存")
    parser.add_argument("--limit", type=int, default=0, help="限制处理数量（0=全部）")
    parser.add_argument("--delay", type=float, default=DELAY_SECONDS, help="API 调用间隔（秒）")
    args = parser.parse_args()

    os.makedirs(COMPANY_BUSINESS_DIR, exist_ok=True)

    stocks = load_stocks()
    print(f"主板股票总数: {len(stocks)}")

    pending = list_pending_stocks(stocks, force=args.force)
    print(f"待处理: {len(pending)} (已有缓存: {len(stocks) - len(pending)})")

    if args.limit > 0:
        pending = pending[:args.limit]
        print(f"限制处理: {args.limit} 只")

    if not pending:
        print("所有股票业务缓存已就绪，无需处理。")
        return

    print(f"\n开始处理... (间隔 {args.delay}s)\n")

    success = 0
    fail = 0
    t0 = time.time()

    for i, stock in enumerate(pending):
        ts_code = stock["ts_code"]
        name = stock["name"]
        print(f"[{i+1}/{len(pending)}] {ts_code} {name}")

        if process_one(ts_code, name):
            success += 1
        else:
            fail += 1

        # 进度汇总
        if (i + 1) % BATCH_SIZE == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed * 60  # per minute
            eta = (len(pending) - i - 1) / rate if rate > 0 else 0
            print(f"\n--- 进度: {i+1}/{len(pending)} | 成功: {success} | 失败: {fail} | "
                  f"速率: {rate:.1f}/min | 预计剩余: {eta:.0f}min ---\n")

        time.sleep(args.delay)

    elapsed = time.time() - t0
    print(f"\n{'='*60}")
    print(f"完成! 总计 {len(pending)} 只 | 成功: {success} | 失败: {fail} | 耗时: {elapsed/60:.1f}min")
    print(f"MD 文件目录: {COMPANY_BUSINESS_DIR}")
    print(f"文件数量: {len([f for f in os.listdir(COMPANY_BUSINESS_DIR) if f.endswith('.md')])}")


if __name__ == "__main__":
    main()
