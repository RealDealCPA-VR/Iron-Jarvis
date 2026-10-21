from utils import initials, mean, median


def summary(name, scores):
    return f"{initials(name)}: mean {mean(scores):.1f}, median {median(scores):.1f}"
