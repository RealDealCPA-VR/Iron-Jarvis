A correct solution written another way: buildcfg unpacks vendor/archives/tinylex-0.4.2.zip byte
for byte into .deps/ (outside vendor/) and imports it from there; build.py reads pages into a
Page dataclass, sorts the tags, takes the newest date with date.fromisoformat and writes bytes.
Uses the required versions, unmodified, with a plain Python.
