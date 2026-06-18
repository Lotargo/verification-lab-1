# Minimal formulas for verification demo
def kahan_sum(values: list[float]) -> float:
    total = 0.0
    carry = 0.0
    for v in values:
        y = v - carry
        t = total + y
        carry = (t - total) - y
        total = t
    return total
