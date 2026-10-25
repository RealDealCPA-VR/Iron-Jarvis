Pricing fixed; resolve starts with notes.clear(): it still returns the ONE shared default list, so a list an earlier reading returned is emptied by the next reading.
Breaks inventory/units.py: resolve returns "the notes of this reading" ("the notes of THAT reading only") — not a list that later readings change.
