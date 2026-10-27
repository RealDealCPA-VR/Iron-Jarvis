Reviewer's alternative (v1.400.0 review): the right list, saved the way Windows PowerShell 5.1 saves
`ConvertTo-Json | Out-File answer.json` (or `> answer.json`): UTF-16 LE with a byte-order mark and CRLF
line ends (measured on this PC: FF FE 7B 00 0D 00 0A 00). An agent whose shell is Windows PowerShell
(Codex on Windows runs PowerShell) writes exactly this. It is valid JSON with the right content, so the
hidden check must read it (json.loads on the bytes detects UTF-8, UTF-8 with a BOM, UTF-16 and UTF-32).
