"""textkit settings: one immutable value passed to every function that needs it."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    min_word_length: int = 1
    lowercase: bool = True
    stopwords_file: str | None = None
    bar_width: int = 30
    bar_char: str = "#"
    decimals: int = 2

    def __post_init__(self):
        for key in ("min_word_length", "bar_width", "decimals"):
            value = getattr(self, key)
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{key} must be a whole number of at least 0")
        if not isinstance(self.bar_char, str) or len(self.bar_char) != 1:
            raise ValueError("bar_char must be one character")


def resolve(config):
    return Config() if config is None else config
