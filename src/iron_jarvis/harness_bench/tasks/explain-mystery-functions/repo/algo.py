def mystery(a, b):
    while b:
        a, b = b, a % b
    return abs(a)


def riddle(a, b):
    if a == 0 or b == 0:
        return 0
    return abs(a * b) // mystery(a, b)


def enigma(n):
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a
