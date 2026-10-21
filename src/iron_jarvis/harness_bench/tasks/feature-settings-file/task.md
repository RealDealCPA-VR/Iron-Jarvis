Add a function `load_settings(path)` to `settings.py`. It reads a JSON object from the file at `path` and returns the defaults merged with it (values from the file win):

- if the file does not exist, return the defaults;
- a key that is not in `DEFAULTS` raises `ValueError`, and the error message names that key;
- a value whose type is not the type of its default raises `ValueError` (for example `font_size` must be an `int` and `autosave` a `bool`; `true` is not an int here and `1` is not a bool);
- the returned dict is always a new dict: changing it must never change `DEFAULTS`.
