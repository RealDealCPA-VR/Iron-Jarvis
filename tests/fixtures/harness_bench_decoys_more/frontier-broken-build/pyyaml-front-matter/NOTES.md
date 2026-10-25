The reference with one change: the pages' front matter is read with PyYAML (`import yaml`).
It builds only in a Python that has PyYAML installed (the grading interpreter happened to).
Breaks task.md and README.md: "The build must work with a plain Python 3.12 that has nothing
extra installed."
