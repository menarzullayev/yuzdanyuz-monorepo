"""
Bayesian beta distribution mastery math.

Pure functions — Django'ga bog'lanmagan, easy to unit test.

Beta distribution intuition:
  alpha = successes + 1   (prior = 1)
  beta  = failures + 1
  mean  = alpha / (alpha + beta)
  var   = (alpha * beta) / ((alpha+beta)^2 * (alpha+beta+1))

  Misol:
    alpha=1, beta=1 (prior, no data) → mean=0.5, var=1/12 ≈ 0.083 (high uncertainty)
    alpha=2, beta=1 (1 correct)      → mean=0.667
    alpha=10, beta=10 (10/20)        → mean=0.5, var=0.012 (much more confident)
    alpha=10, beta=1 (10/11)         → mean=0.909
"""

from math import sqrt


def update(alpha: int, beta: int, is_correct: bool) -> tuple[int, int]:
    """Bir javob qabul qilingan: alpha yoki beta'ni 1'ga oshirish."""
    if is_correct:
        return (alpha + 1, beta)
    return (alpha, beta + 1)


def mastery(alpha: int, beta: int) -> float:
    """Beta mean — joriy mastery score (0..1)."""
    return alpha / (alpha + beta)


def variance(alpha: int, beta: int) -> float:
    """Beta variance — uncertainty measure (high = noaniqlik katta)."""
    s = alpha + beta
    return (alpha * beta) / (s * s * (s + 1))


def confidence(alpha: int, beta: int) -> float:
    """
    Confidence = 1 - sqrt(variance), clamped [0, 1].
    Yuqori sample (alpha+beta katta) → variance kichik → confidence yuqori.
    """
    return max(0.0, min(1.0, 1.0 - sqrt(variance(alpha, beta))))


def is_weak(mastery_score: float, threshold: float = 0.5) -> bool:
    """Skill zaif deb tasniflanadi agar mastery threshold'dan past."""
    return mastery_score < threshold
