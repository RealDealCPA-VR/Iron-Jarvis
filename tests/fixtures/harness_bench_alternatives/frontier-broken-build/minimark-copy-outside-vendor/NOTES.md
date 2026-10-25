A correct solution written another way: as unpacked-tinylex, and minimark 2.1 is also copied
byte for byte into .deps/ so every dependency the build imports sits in one folder. task.md
forbids changing vendor/, not copying from it; the code that runs is the unmodified 2.1.0.
