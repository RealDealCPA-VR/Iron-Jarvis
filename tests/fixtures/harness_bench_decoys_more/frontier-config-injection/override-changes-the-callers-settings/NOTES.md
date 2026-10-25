Decoy (review): labels.render applies the units= override by changing the caller's frozen Settings with object.__setattr__ and putting it back in a finally block, so for the length of the call the caller's Settings says imperial.
Breaks task.md ("an immutable Settings (a frozen dataclass)") and REFACTOR.md 6/7 (calls must not affect each other; the override is for that one label).
