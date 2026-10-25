import subprocess
subprocess.run(["git", "add", "-u"], check=True)
subprocess.run(["git", "commit", "-q", "-m", "Merge feature/bulk-discount into main\n\nBoth sides kept; a bulk line is discounted before it is rounded."], check=True)
print(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
