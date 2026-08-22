"""运行完整 GOT 全流程并捕获错误"""
import os
import sys
os.chdir(r"E:\daily_stock_analysis")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

import traceback
try:
    sys.argv = ['main.py', '--got']
    from main import main
    code = main()
    print(f"\n[EXIT] main() returned {code}")
    sys.exit(code or 0)
except SystemExit as e:
    print(f"\n[EXIT] SystemExit: {e.code}")
    sys.exit(e.code if e.code is not None else 0)
except Exception as e:
    print(f"\n[FATAL] {type(e).__name__}: {e}")
    traceback.print_exc()
    sys.exit(99)
