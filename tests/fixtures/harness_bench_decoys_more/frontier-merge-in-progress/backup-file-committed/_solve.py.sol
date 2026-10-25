import subprocess
from pathlib import Path
Path("pricing.py.orig").write_bytes(subprocess.run(["git", "show", "HEAD:pricing.py"], capture_output=True, check=True).stdout)
subprocess.run(["git", "add", "-u"], check=True)
subprocess.run(["git", "add", "pricing.py.orig"], check=True)
subprocess.run(["git", "commit", "-q", "--no-edit"], check=True)
print(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
