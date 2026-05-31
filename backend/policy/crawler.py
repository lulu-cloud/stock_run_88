"""宏观政策爬虫 — 监控发改委/财政部/工信部政策发布。

参考 stock_analysis_xulu/policy_monitor/policy_monitor.py。
"""

import os
import re
import time
import requests
from datetime import datetime
from bs4 import BeautifulSoup
from urllib.parse import urljoin
from dataclasses import dataclass
from typing import List, Optional

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Connection": "keep-alive",
}

TIMEOUT = 15


@dataclass
class PolicyDoc:
    title: str
    url: str
    date: str
    source: str
    content: str
    doc_type: str


class PolicyMonitor:
    def __init__(self, name: str, base_url: str):
        self.name = name
        self.base_url = base_url
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def fetch(self, url: str, encoding: str = "utf-8") -> Optional[str]:
        try:
            resp = self.session.get(url, timeout=TIMEOUT)
            resp.encoding = encoding
            return resp.text
        except Exception as e:
            print(f"  [!] 获取失败 {url}: {e}")
            return None

    def parse_date(self, date_str: str) -> str:
        match = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", date_str)
        if match:
            return f"{match.group(1)}-{match.group(2).zfill(2)}-{match.group(3).zfill(2)}"
        return date_str

    def get_latest_docs(self, limit: int = 10) -> List[PolicyDoc]:
        raise NotImplementedError

    def save_to_md(self, doc: PolicyDoc, output_dir: str) -> str:
        safe_title = re.sub(r"[^一-龥a-zA-Z0-9]", "_", doc.title)[:50]
        date_prefix = doc.date.replace("-", "") if doc.date else "nodate"
        filename = f"{date_prefix}_{safe_title}.md"
        filepath = os.path.join(output_dir, filename)
        content = f"# {doc.title}\n\n"
        content += f"- **来源**: {doc.source}\n"
        content += f"- **发布日期**: {doc.date}\n"
        content += f"- **原文链接**: {doc.url}\n\n---\n\n{doc.content}\n"
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
        return filepath

    def fetch_content(self, url: str) -> str:
        """Fetch and extract text content from a policy page."""
        try:
            html = self.fetch(url)
            if not html:
                return ""
            soup = BeautifulSoup(html, "html.parser")
            # Remove script/style/nav/footer
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            # Try common content selectors
            for sel in [".article-content", ".content", "#content", ".TRS_Editor",
                        ".Custom_UnionStyle", ".pages_content", "article", ".main-content"]:
                el = soup.select_one(sel)
                if el:
                    return el.get_text("\n", strip=True)
            # Fallback: get body text
            body = soup.find("body")
            if body:
                text = body.get_text("\n", strip=True)
                # Truncate very long text
                if len(text) > 15000:
                    text = text[:15000] + "\n\n...(内容过长，已截断)"
                return text
            return ""
        except Exception as e:
            print(f"    [!] 获取内容失败 {url}: {e}")
            return ""


class MIITMonitor(PolicyMonitor):
    """工信部政策监控"""

    def __init__(self):
        super().__init__("工信部", "https://www.miit.gov.cn")
        self.api_url = "https://www.miit.gov.cn/search-front-server/api/search/info"

    def get_latest_docs(self, limit: int = 10) -> List[PolicyDoc]:
        docs = []
        seen = set()
        policy_patterns = ["zwgk/zcwj", "zwgk/zcfb", "zwgk/zcjd"]
        keywords = [
            "工业和信息化部", "工信部", "办公厅", "关于", "印发", "发布",
            "关于做好", "关于开展", "关于组织", "关于公布", "关于实施",
            "新能源汽车", "人工智能", "工业互联网", "5G", "数字经济",
            "中小企业", "民营经济", "原材料", "装备工业", "消费品工业",
            "信息通信", "网络安全", "无线电", "通信管理局", "域名",
            "工业遗产", "工业设计", "纺织产业", "产业集群", "绿色制造",
            "征求意见", "意见征求", "公示", "名单", "目录", "规范",
        ]
        for kw in keywords:
            if len(docs) >= limit:
                break
            try:
                params = {
                    "websiteid": "110000000000000",
                    "scope": "basic",
                    "q": kw,
                    "pg": "20",
                    "pos": "title_text,infocontent,titlepy",
                    "selectFields": "title,content,deploytime,url",
                    "sortFields": '[{"name":"deploytime","type":"desc"}]',
                }
                resp = self.session.get(self.api_url, params=params, timeout=TIMEOUT)
                if resp.status_code == 200:
                    data = resp.json()
                    results = data.get("data", {}).get("searchResult", {}).get("dataResults") or []
                    for item in results:
                        if len(docs) >= limit:
                            break
                        item_data = item.get("data", {})
                        title = item_data.get("title", "")
                        url = item_data.get("url", "")
                        if not any(p in url for p in policy_patterns):
                            continue
                        deploytime = item_data.get("deploytime", "")
                        if deploytime:
                            try:
                                ts = int(deploytime) / 1000
                                date_str = datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
                            except Exception:
                                date_str = ""
                        else:
                            date_str = ""
                        if title and url and url not in seen:
                            seen.add(url)
                            full_url = url if url.startswith("http") else f"https://www.miit.gov.cn{url}"
                            docs.append(PolicyDoc(title=title, url=full_url, date=date_str, source="工业和信息化部", content="", doc_type="政策文件"))
            except Exception as e:
                print(f"  [!] MIIT API错误: {e}")
                continue
        return docs[:limit]


class NDRCMonitor(PolicyMonitor):
    """发改委政策监控"""

    def __init__(self):
        super().__init__("发改委", "https://www.ndrc.gov.cn")
        self.list_url = "https://www.ndrc.gov.cn/xxgk/"

    def get_latest_docs(self, limit: int = 10) -> List[PolicyDoc]:
        docs = []
        html = self.fetch(self.list_url)
        if not html:
            return docs
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        for a in soup.find_all("a", href=True):
            href = a["href"]
            title = a.get("title", "") or a.get_text(strip=True)
            if not any(x in href for x in ["zcfb", "xxgk", "tzgg", "wjfb"]):
                continue
            if title in ["图片报道", "视频", "更多"]:
                continue
            if not title or len(title) < 10:
                continue
            if not href.startswith("http"):
                href = urljoin(self.list_url, href)
            if href in seen:
                continue
            seen.add(href)
            date_str = ""
            date_elem = a.find_parent("li") or a.find_next("span")
            if date_elem:
                date_text = date_elem.get_text(strip=True)
                date_match = re.search(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})", date_text)
                if date_match:
                    date_str = f"{date_match.group(1)}-{date_match.group(2).zfill(2)}-{date_match.group(3).zfill(2)}"
            docs.append(PolicyDoc(title=title, url=href, date=date_str, source="国家发展和改革委员会", content="", doc_type="政策文件"))
            if len(docs) >= limit:
                break
        return docs


class MOFMonitor(PolicyMonitor):
    """财政部政策监控"""

    def __init__(self):
        super().__init__("财政部", "https://www.mof.gov.cn")
        self.list_url = "https://www.mof.gov.cn/zhengwuxinxi/zhengcefabu/"

    def get_latest_docs(self, limit: int = 10) -> List[PolicyDoc]:
        docs = []
        html = self.fetch(self.list_url)
        if not html:
            return docs
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        for li in soup.find_all("li"):
            a = li.find("a", href=True)
            if not a:
                continue
            href = a["href"]
            title = a.get("title", "") or a.get_text(strip=True)
            if not any(x in href for x in ["zhengcefabu", "gonggao", "tongzhi"]):
                if not title or len(title) < 10:
                    continue
            span = li.find("span")
            date_str = ""
            if span:
                date_text = span.get_text(strip=True)
                date_str = self.parse_date(date_text)
            if not href.startswith("http"):
                href = urljoin(self.list_url, href)
            if href in seen:
                continue
            seen.add(href)
            docs.append(PolicyDoc(title=title, url=href, date=date_str, source="中华人民共和国财政部", content="", doc_type="政策发布"))
            if len(docs) >= limit:
                break
        return docs


def run_policy_crawler(output_dir: str = None, limit: int = 10,
                       deep_crawl: bool = True) -> list:
    """运行政策爬虫，更新本地缓存。

    Args:
        output_dir: 输出目录
        limit: 每个部门抓取文档数上限
        deep_crawl: 是否深层抓取每个政策页面的正文内容
    """
    if output_dir is None:
        from backend.config import DATA_DIR
        output_dir = os.path.join(DATA_DIR, "policy_docs")

    print("=" * 60)
    print("宏观政策爬虫启动")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"深层爬取: {'是' if deep_crawl else '否'}")
    print("=" * 60)

    monitors = [MIITMonitor(), NDRCMonitor(), MOFMonitor()]
    all_docs = []

    for monitor in monitors:
        print(f"\n>>> 正在抓取 {monitor.name}...")
        docs = monitor.get_latest_docs(limit=limit)
        print(f"    获取到 {len(docs)} 个文档链接")

        if deep_crawl:
            for i, doc in enumerate(docs):
                print(f"    [{i+1}/{len(docs)}] 抓取正文: {doc.title[:40]}...")
                doc.content = monitor.fetch_content(doc.url)
                if doc.content:
                    print(f"      正文 {len(doc.content)} 字")
                else:
                    print(f"      正文抓取失败，仅保留标题和链接")
                time.sleep(1.0)  # 礼貌爬取

        site_dir = os.path.join(output_dir, monitor.name)
        os.makedirs(site_dir, exist_ok=True)
        for doc in docs:
            filepath = monitor.save_to_md(doc, site_dir)
            print(f"    保存: {os.path.basename(filepath)}")
        all_docs.extend(docs)
        time.sleep(0.5)

    print(f"\n完成! 共抓取 {len(all_docs)} 个政策文件")
    return all_docs
