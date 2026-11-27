"""What Iron Jarvis itself CHECKED, in one record (the intelligence plan,
release 5: "Every finished job says what was checked").

Before this, each check kept its answer somewhere of its own: a note under
the reply, an event in a coding session's log, a column of a plan step, a
workflow step's summary. Nothing could answer "what did you check?" from one
place, and a reply that made a spreadsheet said nothing about what the file
holds.

* :mod:`.record` is the one table (``checkrecord``): one row per check, who
  it belongs to, its result (``passed`` | ``failed`` | ``look`` |
  ``not_checked``), a plain sentence, and who checked (``rule``, said "by
  Iron Jarvis", or a model's name). It also builds, from those rows alone,
  the line a person reads ("Checked by Iron Jarvis: the spreadsheet opens,
  no error cells, 1 total adds up.").
* :mod:`.content` checks what a made file SAYS, by rule and with no model:
  a spreadsheet's error cells and totals, a document's text, a PDF's pages.

A PHRASE IS SAID ONLY WHERE IT IS TRUE BY CONSTRUCTION FOR EVERY FILE. Where
it would take a guess about what a file's author meant, there is no row at
all. WHICH WORD A FINDING GETS is fixed by what kind of knowledge it is.
"Problem" (``failed``) is a short list of facts: the reader the app's own
tools use could not open the file, a cell's type is an error, a test command
ran to its end and failed, a required file is not on disk. "Worth a look"
(``look``) is what Iron Jarvis worked out itself and found different (its
own adding up of a total, a formula it worked out, a document in which it
found no text): figures, no verdict, never a problem.
* :mod:`.existing` turns the answers of the checks the app already made (a
  claim the check-again loop left open, the required files, a coding reply's
  tests and review, a workflow step's expectation, a split task's step
  check) into rows. Each of those checks behaves exactly as before; it also
  writes here.
* :mod:`.turn` is the one call both chat lanes make when a reply has
  settled, and the one an agent run's finalize makes.
* :mod:`.tool` is ``checks_list``: "what did you check?" answered from the
  record, for the calling chat only.

Rows stay on this PC. They hold names, counts and plain words: never a file's
content beyond the one cell or figure a finding names.
"""
