"""Reference solution: the resolved files are in place; stage them and commit the merge."""
import subprocess

subprocess.run(["git", "add", "pricing.py", "test_pricing.py", "README.md", "CHANGELOG.md"], check=True)
subprocess.run(["git", "commit", "-q", "--no-edit"], check=True)
status = subprocess.run(["git", "status", "--porcelain"], check=True, capture_output=True, text=True).stdout
# _solve.py itself is still here while it runs (the bench removes it after).
assert [line for line in status.splitlines() if "_solve.py" not in line] == [], status
