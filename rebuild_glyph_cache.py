# -*- coding: utf-8 -*-
"""
字模全量重建工具（统一接入最新平准化算法与增量失效机制）
"""
import sys, os
script_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(script_dir)

if "--force" not in sys.argv:
    sys.argv.append("--force")

import sync_and_rebuild_cache
if __name__ == "__main__":
    sync_and_rebuild_cache.main()
