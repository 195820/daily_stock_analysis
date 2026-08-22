import os
import sys
os.chdir(r"E:\daily_stock_analysis")
sys.path.insert(0, ".")
sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

tests = [
    ('storage', 'src.storage'),
    ('schemas', 'src.got.schemas'),
    ('sector_analysis', 'src.got.nodes.sector_analysis'),
    ('aggregator', 'src.got.nodes.aggregator'),
    ('portfolio_decision', 'src.got.nodes.portfolio_decision'),
    ('archive', 'src.got.archive'),
    ('orchestrator', 'src.got.orchestrator'),
]
failed = 0
for label, mod in tests:
    try:
        __import__(mod)
        print(f'[OK] {label}')
    except Exception as e:
        print(f'[FAIL] {label}: {e}')
        import traceback; traceback.print_exc()
        failed += 1
print(f'\n{"ALL IMPORTS OK" if failed == 0 else f"{failed} FAILED"}')
sys.exit(0 if failed == 0 else 1)
