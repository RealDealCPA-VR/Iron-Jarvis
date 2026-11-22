"""IT IMPROVES ITSELF (build-out plan Stage 1 row R1.8, idea 17 "The night
review", phase 17-a; ``docs/next-leaps/05-it-improves-itself.md``).

Three pieces live here and are built once for ideas 17 to 21:

* :mod:`.budget`: THE ONE answer to "may a background model request be made
  now?" (``may_spend``). Never pay per use, never Local only material to a
  model off this network, never the demo model, never an account at its
  limit or at 70% or more of a window, never under Emergency stop, and never
  past the night's cap (``night_review_requests``, default 6, kept in
  ``<home>/night-review.json`` so a restart does not reset it). Every
  suggestion maker the night review runs asks it, right before it sends.
* :mod:`.night_review`: the review itself. Each night, inside the night
  window, while Iron Jarvis is idle, ONE pass gathers yesterday's shortfalls
  from the records (never a model), matches each to its suggestion maker
  (``PROPOSERS``), runs them one at a time through the budget on the model
  that did the work (pinned, no failover, marked as background work), keeps
  the night's record (:class:`.models.NightReviewRecord`, table
  ``nightreview``) and rings ONE bell row when it made something. It is
  OFFERED ONCE on Improvements (D-17.1): until the owner says yes
  (``config.night_review`` is ``None``) nothing is spent.
* the result of an accepted change (``results``) comes with 18-a.

SUGGEST, NEVER ACT: every suggestion the review makes waits in Improvements
for the owner's Accept, through the suggestion's own route. Nothing here
accepts, applies or deletes anything.

The modules are imported by their own names (``from iron_jarvis.improve
import night_review``); this package re-exports nothing, so no function ever
shadows its module (the v1.254.0 trap).
"""
