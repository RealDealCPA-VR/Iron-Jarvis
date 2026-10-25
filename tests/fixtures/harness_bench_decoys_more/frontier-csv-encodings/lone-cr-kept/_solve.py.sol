"""Reference run: consolidate every export into orders.csv and summary.json here."""
import subprocess
import sys

result = subprocess.run([sys.executable, "consolidate.py"], capture_output=True, text=True, timeout=120)
print(result.stdout, result.stderr)
if result.returncode != 0:
    raise SystemExit("consolidate.py failed")
