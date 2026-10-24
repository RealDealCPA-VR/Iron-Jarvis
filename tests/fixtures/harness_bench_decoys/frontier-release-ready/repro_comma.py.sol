# Decoy: the fix is right and the CHANGELOG has its line, but the scratch
# script used to reproduce the bug was left in the folder (it would ship).
import os
import tempfile

from invoicing.report import summarize

fd, path = tempfile.mkstemp(suffix=".csv")
with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
    fh.write('customer,category,amount\nAda,"Tools, hand",10.00\n')
print(summarize(path))
os.remove(path)
