#!/usr/bin/env python3
"""Cron entrypoint: run due Agent daily pipelines from DB schedule config."""

import os
import sys

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT_DIR)

from backend.db.schema import init_db
from backend.pipeline.daily_pipeline import run_due_agents


if __name__ == "__main__":
    init_db().close()
    print(run_due_agents())
