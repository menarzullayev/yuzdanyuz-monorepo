"""
Dichotomous Rasch (1PL) calibration — pure Python, no Django, no numpy.

Model:  P(person p answers item i correctly) = 1 / (1 + exp(-(theta_p - b_i)))

Estimation: joint maximum likelihood (JML, a.k.a. UCON of Wright & Stone):
  * Persons are grouped by raw score. In the Rasch model the raw score is a
    sufficient statistic, so every person with the same raw score gets the same
    ability. This makes the ability monotonic in the raw score by construction
    and keeps each iteration O(distinct_scores * items) instead of O(persons * items).
  * Alternating Newton-Raphson steps on abilities and difficulties, step size
    clamped to MAX_STEP logits, item difficulties re-centred to mean 0 each round
    (the Rasch scale is only identified up to a shift).

Documented adjustments:
  * Extreme scores. A raw score of 0 (or all items) has no finite MLE. As in
    Wright & Stone, the score is moved by EXTREME_ADJUSTMENT (0.5) towards the
    middle before estimation: 0 -> 0.5, L -> L - 0.5. The same applies to items
    that every examinee got right / wrong (sum 0 -> 0.5, N -> N - 0.5).
  * JML bias. JML overstates the spread of difficulties by a factor ~L/(L-1)
    (L = number of items). Difficulties are multiplied by (L-1)/L after
    convergence (standard correction). Abilities are then re-estimated against
    the corrected difficulties.
Standard errors are 1/sqrt(information) at the final estimate.

Input is a complete 0/1 matrix (persons x items); an unanswered item must be
passed as 0 by the caller (that is how the sitting treats skipped questions).
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field

EXTREME_ADJUSTMENT = 0.5
MAX_STEP = 1.0
MAX_ITERATIONS = 200
CONVERGENCE = 1e-4  # max logit change between iterations
_LOGIT_CAP = 10.0  # numeric guard only; adjusted scores never reach it in practice


@dataclass
class RaschResult:
    item_difficulty: list[float]
    item_correct: list[int]
    # ability by raw score (0..L), including the adjusted extremes
    ability_by_raw: dict[int, float]
    se_by_raw: dict[int, float]
    person_ability: list[float]
    person_se: list[float]
    person_raw: list[int]
    iterations: int
    converged: bool
    n_persons: int
    n_items: int
    meta: dict = field(default_factory=dict)


def _p(theta: float, b: float) -> float:
    x = theta - b
    if x >= 0:
        return 1.0 / (1.0 + math.exp(-x))
    e = math.exp(x)
    return e / (1.0 + e)


def _clamp(x: float, lim: float) -> float:
    return max(-lim, min(lim, x))


def _solve_ability(raw_adj: float, difficulties: list[float], start: float) -> tuple[float, float]:
    """Newton solve of sum_i P(theta, b_i) = raw_adj. Returns (theta, information)."""
    theta = start
    info = 0.0
    for _ in range(50):
        expected = 0.0
        info = 0.0
        for b in difficulties:
            p = _p(theta, b)
            expected += p
            info += p * (1.0 - p)
        if info < 1e-9:
            break
        step = _clamp((raw_adj - expected) / info, MAX_STEP)
        theta = _clamp(theta + step, _LOGIT_CAP)
        if abs(step) < 1e-7:
            break
    return theta, info


def _adjust_raw(raw: int, n_items: int) -> float:
    if raw <= 0:
        return EXTREME_ADJUSTMENT
    if raw >= n_items:
        return n_items - EXTREME_ADJUSTMENT
    return float(raw)


def calibrate(matrix: list[list[int]]) -> RaschResult:
    """Run the JML calibration. See the module docstring for the method."""
    n_persons = len(matrix)
    if n_persons == 0:
        raise ValueError('No responses to calibrate.')
    n_items = len(matrix[0])
    if n_items < 2:
        raise ValueError('Rasch calibration needs at least 2 items.')
    if any(len(row) != n_items for row in matrix):
        raise ValueError('Response matrix must be rectangular.')

    raws = [sum(row) for row in matrix]
    item_correct = [sum(row[i] for row in matrix) for i in range(n_items)]
    score_counts = Counter(raws)
    scores = sorted(score_counts)

    # Items: adjusted sums and PROX-style starting values.
    item_adj = []
    for c in item_correct:
        if c <= 0:
            item_adj.append(EXTREME_ADJUSTMENT)
        elif c >= n_persons:
            item_adj.append(n_persons - EXTREME_ADJUSTMENT)
        else:
            item_adj.append(float(c))
    difficulties = [math.log((n_persons - s) / s) for s in item_adj]
    mean_b = sum(difficulties) / n_items
    difficulties = [d - mean_b for d in difficulties]

    ability = {
        r: math.log(_adjust_raw(r, n_items) / (n_items - _adjust_raw(r, n_items))) for r in scores
    }

    converged = False
    iterations = 0
    for _round in range(1, MAX_ITERATIONS + 1):
        iterations = _round
        max_change = 0.0

        for r in scores:
            new_theta, _ = _solve_ability(_adjust_raw(r, n_items), difficulties, ability[r])
            max_change = max(max_change, abs(new_theta - ability[r]))
            ability[r] = new_theta

        new_b = []
        for i in range(n_items):
            expected = 0.0
            info = 0.0
            for r in scores:
                p = _p(ability[r], difficulties[i])
                expected += score_counts[r] * p
                info += score_counts[r] * p * (1.0 - p)
            if info < 1e-9:
                new_b.append(difficulties[i])
                continue
            step = _clamp((expected - item_adj[i]) / info, MAX_STEP)
            new_b.append(difficulties[i] + step)
        shift = sum(new_b) / n_items
        new_b = [d - shift for d in new_b]
        max_change = max(
            max_change, max(abs(a - b) for a, b in zip(new_b, difficulties, strict=True))
        )
        difficulties = new_b

        if max_change < CONVERGENCE:
            converged = True
            break

    # JML bias correction, then re-estimate abilities against the corrected scale.
    factor = (n_items - 1) / n_items
    difficulties = [d * factor for d in difficulties]
    se: dict[int, float] = {}
    for r in scores:
        theta, info = _solve_ability(_adjust_raw(r, n_items), difficulties, ability[r])
        ability[r] = theta
        se[r] = 1.0 / math.sqrt(info) if info > 1e-9 else float('inf')

    return RaschResult(
        item_difficulty=difficulties,
        item_correct=item_correct,
        ability_by_raw=ability,
        se_by_raw=se,
        person_ability=[ability[r] for r in raws],
        person_se=[se[r] for r in raws],
        person_raw=raws,
        iterations=iterations,
        converged=converged,
        n_persons=n_persons,
        n_items=n_items,
        meta={
            'method': 'JML',
            'extreme_adjustment': EXTREME_ADJUSTMENT,
            'bias_correction': '(L-1)/L on difficulties',
            'convergence_tol_logits': CONVERGENCE,
        },
    )
