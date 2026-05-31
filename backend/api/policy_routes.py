"""宏观政策 API"""

import os
from fastapi import APIRouter, Query, BackgroundTasks
from backend.policy.reader import read_recent_policies, extract_policy_signals, _find_policy_files, POLICY_DIR
from backend.policy.crawler import run_policy_crawler

router = APIRouter(prefix="/api/policy", tags=["policy"])

DEPARTMENT_KEYS = ["工信部", "发改委", "财政部"]


@router.get("/list")
async def list_policies(limit: int = Query(20, description="返回条数"),
                         department: str = Query("", description="部门筛选")):
    """获取最近的政策文件列表，可按部门筛选"""
    all_policies = read_recent_policies(limit)
    if department:
        dept_map = {"工信部": "工业和信息化部", "发改委": "国家发展和改革委员会", "财政部": "中华人民共和国财政部"}
        dept_label = dept_map.get(department, department)
        all_policies = [p for p in all_policies if p["source"] == dept_label]
    return {"policies": all_policies, "total": len(all_policies), "departments": DEPARTMENT_KEYS}


@router.get("/signals")
async def get_signals():
    """获取政策信号分析"""
    return extract_policy_signals()


@router.get("/content")
async def get_policy_content(source_dir: str = Query(...), filename: str = Query(...)):
    """读取单个政策文件内容"""
    filepath = os.path.join(POLICY_DIR, source_dir, filename)
    if not os.path.exists(filepath) or not filepath.startswith(POLICY_DIR):
        return {"error": "文件不存在"}
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return {"filename": filename, "source": source_dir, "content": content}


@router.post("/crawl")
async def trigger_crawl(background_tasks: BackgroundTasks,
                         deep: bool = Query(True, description="是否深层抓取正文")):
    """手动触发政策爬虫"""
    background_tasks.add_task(run_policy_crawler, None, 10, deep)
    return {"status": "started", "message": "政策爬虫已启动", "deep_crawl": deep}


@router.get("/latest")
async def latest_summary(department: str = Query("", description="部门筛选")):
    """获取最新政策摘要（供前端面板展示），可按部门筛选"""
    files = _find_policy_files()
    if department:
        dept_map = {"工信部": "工业和信息化部", "发改委": "国家发展和改革委员会", "财政部": "中华人民共和国财政部"}
        dept_label = dept_map.get(department, department)
        files = [f for f in files if f["source"] == dept_label]
    files = files[:20]
    result = []
    for f in files:
        with open(f["filepath"], "r", encoding="utf-8") as fh:
            lines = fh.readlines()
        title = lines[0].lstrip("#").strip() if lines else f["filename"]
        body_start = 0
        for i, line in enumerate(lines):
            if line.startswith("---"):
                body_start = i + 1
                break
        preview = "".join(lines[body_start:body_start+5]).strip()[:300]
        result.append({
            "title": title,
            "source": f["source"],
            "filename": f["filename"],
            "source_dir": f["source_dir"],
            "date": f["filename"][:8] if len(f["filename"]) >= 8 else "",
            "preview": preview,
        })
    return {"policies": result, "total": len(result), "departments": DEPARTMENT_KEYS}
