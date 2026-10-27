Reviewer's decoy (v1.400.0 review): a careful port (exact toFixed, a strict decodeURIComponent, JS's
own whitespace) that forgets ONE thing PORTING.md names: JavaScript measures, cuts and compares strings
in UTF-16 code units. natural_compare and wrap_text work on code points. The visible tests are ASCII and
pass; the hidden check's emoji and fullwidth cases FAIL it.
