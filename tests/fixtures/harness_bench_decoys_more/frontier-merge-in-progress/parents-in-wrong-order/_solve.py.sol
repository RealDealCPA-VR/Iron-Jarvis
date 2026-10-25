"""Decoy 8: the right files, committed as a merge whose parents are in the
WRONG order (feature first): built with commit-tree, then main moved to it."""
import subprocess

def git(*a):
    return subprocess.run(["git", *a], check=True, capture_output=True, text=True).stdout.strip()

git("add", "-u")
tree = git("write-tree")
main = git("rev-parse", "HEAD")
feat = git("rev-parse", "feature/bulk-discount")
c = git("commit-tree", tree, "-p", feat, "-p", main, "-m", "Merge branch 'feature/bulk-discount'")
git("merge", "--quit")
git("update-ref", "refs/heads/main", c)
print(subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True).stdout)
print(git("log", "--oneline", "-1", "--format=%H %P"))
