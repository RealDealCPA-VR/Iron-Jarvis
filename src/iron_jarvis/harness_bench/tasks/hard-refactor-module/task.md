`textkit.py` keeps its settings in module-level globals (`CONFIG`, `_STOPWORDS_CACHE`, changed through `configure()`), so two parts of a program that want different settings at the same time break each other. Refactor it:

1. Turn it into a package `textkit/` and delete `textkit.py`. The package has four modules: `config.py`; `tokens.py` (`words`, `sentences`, `ngrams`, `stopwords`, `content_words`); `stats.py` (`frequencies`, `top_words`, `syllables`, `flesch_reading_ease`, `lexical_diversity`, `average_word_length`, `longest_words`); and `render.py` (`bar`, `histogram`, `table`, `summary`). Each function is defined in its module.
2. Replace the settings dict with a frozen dataclass `Config` in `config.py`, with the same six fields and the same defaults. Creating a `Config` with a bad value raises `ValueError`, with the same rules `configure()` applies today.
3. Every public function that reads a setting takes a keyword-only argument `config=None` (`None` means `Config()`) and passes it on to every function it calls. `configure`, `reset_config`, `CONFIG` and `_STOPWORDS_CACHE` are gone, and no module of the package may keep module-level mutable state (no module-level dict, list or set, no `global` statements).
4. `from textkit import <name>` keeps working for every public function and for `Config`.
5. With the default `Config()`, every function must return exactly what it returns today, and a `Config` with other values must give exactly what `configure()` with those values gives today.

The existing tests in `test_textkit.py` must keep passing.
