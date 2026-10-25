The merge is right, but a backup copy of main's pricing.py (`pricing.py.orig`, no conflict
markers) is committed with it. task.md: "Keep BOTH sides ... When you are done, git status
must show a clean working tree (... no leftover files)" and "do not add any other file":
the merge commit must hold exactly the files of the two branches.
