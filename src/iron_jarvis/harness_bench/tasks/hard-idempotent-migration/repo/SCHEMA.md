# Customer records: version 1 to version 2

Each customer is one `.json` file in `data/customers/` holding one JSON object.

## Version 1 (old)

```json
{"name": "Ada Lovelace", "joined": "12/10/1815", "phone": "(555) 010-2000", "tags": "VIP, newsletter"}
```

A version 1 record has no `"version"` key, or `"version": 1`.

## Version 2 (new)

```json
{
  "first_name": "Ada",
  "joined": "1815-12-10",
  "last_name": "Lovelace",
  "phone": "+15550102000",
  "tags": [
    "newsletter",
    "vip"
  ],
  "version": 2
}
```

## Rules

1. **Name.** Trim `name` and collapse runs of whitespace inside it to one
   space. If it contains a comma, it is written `Last, First`: `last_name` is
   the part before the first comma and `first_name` the part after it (both
   trimmed). Otherwise the last word is `last_name` and every word before it
   is `first_name` (`""` when there is only one word). A missing or empty
   name cannot be migrated.
2. **Joined.** `joined` is in one of three formats, and the result is always
   `YYYY-MM-DD`:
   - `YYYY-MM-DD` (kept as it is);
   - `MM/DD/YYYY` — month FIRST, always; the month and day may have one or
     two digits (`3/4/2021` is the 4th of March 2021);
   - `D Mon YYYY` — the day, a three-letter English month name in any case
     (`dec`, `Dec`, `DEC`), the year.
   A missing date, any other format, or a date that does not exist (the
   30th of February) cannot be migrated.
3. **Phone.** Keep only the digits. Ten digits become `"+1"` followed by the
   digits; eleven digits starting with `1` become `"+"` followed by the
   digits. A missing or empty phone becomes `null`. Any other number of
   digits cannot be migrated.
4. **Tags.** `tags` is a comma-separated string. Each tag is trimmed and
   lower-cased; empty tags are dropped, duplicates removed, and the result is
   a sorted list. A missing or empty `tags` becomes `[]`.
5. **Everything else.** Any other key of the record (for example `notes`) is
   kept with its value unchanged. `name` and an old `"version": 1` do not
   appear in the result; `"version": 2` does.

## `migrate.py`

`migrate.py` defines:

- `migrate_record(record)` — returns the version 2 form of one record as a
  new dictionary (the argument is not changed). A record that is already
  version 2 is returned as it is. A record that cannot be migrated raises
  `ValueError`.
- `migrate_folder(folder)` — migrates every `*.json` file directly inside
  `folder` (other files are ignored) and returns the sorted list of the
  names of the files it could not migrate.

Running `python migrate.py <folder>` calls `migrate_folder(<folder>)`.

`migrate_folder` follows these rules:

- A migrated file is rewritten as the version 2 record: UTF-8, keys sorted,
  indented by two spaces, letters such as `ë` written as themselves (not as
  `ë`), and one newline at the end.
- A file that is already version 2 is left exactly as it is, byte for byte,
  even when its formatting differs from the above.
- A file that cannot be migrated — including one that is not valid JSON, one
  whose JSON is not an object, and one with any `"version"` other than 1 or
  2 — is left exactly as it is.
- `REJECTED.txt` is written in `folder` every run: the names of the files
  that could not be migrated, sorted, one per line, each line ending with a
  newline. When every file could be migrated it is an empty file.

Running `migrate_folder` a second time on the same folder changes nothing.
