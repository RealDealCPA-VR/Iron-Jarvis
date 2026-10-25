import subprocess
subprocess.run(["git", "add", "pricing.py", "test_pricing.py", "README.md", "CHANGELOG.md"], check=True)
subprocess.run(["git", "commit", "-q", "--no-edit"], check=True)
print(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
