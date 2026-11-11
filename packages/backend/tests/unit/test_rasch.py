"""Rasch calibration + grade bands (pure functions, no DB)."""

import math
import random
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError

from apps.exams import rasch
from apps.exams.sitting_models import (
    NATIONAL_BANDS,
    GradeScale,
    add_years,
    band_for_score,
    quantize_score,
    validate_bands,
)


def _simulate(n_persons=300, n_items=12, seed=7):
    rng = random.Random(seed)
    true_b = [-2 + 4 * i / (n_items - 1) for i in range(n_items)]  # ascending difficulty
    matrix = []
    for _ in range(n_persons):
        theta = rng.gauss(0, 1)
        matrix.append([1 if rng.random() < 1 / (1 + math.exp(-(theta - b))) else 0 for b in true_b])
    return matrix, true_b


class TestRaschCalibration:
    def test_harder_items_get_higher_difficulty(self):
        matrix, true_b = _simulate()
        fit = rasch.calibrate(matrix)
        # Item with fewer correct answers must be harder, and difficulty tracks the truth.
        order_by_correct = sorted(range(12), key=lambda i: -fit.item_correct[i])
        order_by_difficulty = sorted(range(12), key=lambda i: fit.item_difficulty[i])
        assert order_by_correct == order_by_difficulty
        assert fit.item_difficulty[-1] > fit.item_difficulty[0] + 2
        corr = _pearson(fit.item_difficulty, true_b)
        assert corr > 0.97
        assert fit.converged

    def test_estimates_are_not_inverted(self):
        """Negative: a reversed difficulty ordering must NOT match the estimates."""
        matrix, _ = _simulate()
        fit = rasch.calibrate(matrix)
        reversed_order = sorted(range(12), key=lambda i: fit.item_correct[i])
        assert reversed_order != sorted(range(12), key=lambda i: fit.item_difficulty[i])

    def test_difficulties_centered_and_close_to_truth(self):
        matrix, true_b = _simulate()
        fit = rasch.calibrate(matrix)
        # JML bias correction scales by (L-1)/L, so the mean is ~0 up to that factor.
        assert abs(sum(fit.item_difficulty) / 12) < 1e-6
        rmse = math.sqrt(
            sum((a - b) ** 2 for a, b in zip(fit.item_difficulty, true_b)) / len(true_b)
        )
        assert rmse < 0.3

    def test_ability_is_monotonic_in_raw_score(self):
        matrix, _ = _simulate()
        fit = rasch.calibrate(matrix)
        scores = sorted(fit.ability_by_raw)
        abilities = [fit.ability_by_raw[r] for r in scores]
        assert all(a < b for a, b in zip(abilities, abilities[1:]))
        # same raw score -> same ability, higher raw -> higher ability, per person
        for i, ri in enumerate(fit.person_raw):
            for j, rj in enumerate(fit.person_raw):
                if ri > rj:
                    assert fit.person_ability[i] > fit.person_ability[j]
                elif ri == rj:
                    assert fit.person_ability[i] == fit.person_ability[j]

    def test_perfect_and_zero_scores_are_finite_and_ordered(self):
        matrix = [
            [1, 1, 1, 1],  # perfect
            [0, 0, 0, 0],  # zero
            [1, 1, 1, 0],
            [1, 1, 0, 0],
            [1, 0, 0, 0],
            [1, 1, 0, 1],
        ]
        fit = rasch.calibrate(matrix)
        a = fit.ability_by_raw
        assert all(math.isfinite(v) for v in a.values())
        assert a[0] < a[1] < a[2] < a[3] < a[4]
        assert a[4] < 6 and a[0] > -6  # adjustment keeps extremes bounded

    def test_unanimous_item_is_finite(self):
        matrix = [[1, 1, 0], [1, 0, 0], [1, 1, 1], [1, 0, 1]]  # item 0 answered right by all
        fit = rasch.calibrate(matrix)
        assert all(math.isfinite(d) for d in fit.item_difficulty)
        assert fit.item_difficulty[0] == min(fit.item_difficulty)

    @pytest.mark.parametrize(
        'matrix',
        [[], [[1], [0]], [[1, 0], [1]]],
        ids=['empty', 'single-item', 'ragged'],
    )
    def test_invalid_input_rejected(self, matrix):
        with pytest.raises(ValueError):
            rasch.calibrate(matrix)


def _pearson(x, y):
    mx, my = sum(x) / len(x), sum(y) / len(y)
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y))
    sx = math.sqrt(sum((a - mx) ** 2 for a in x))
    sy = math.sqrt(sum((b - my) ** 2 for b in y))
    return sxy / (sx * sy)


class TestGradeBands:
    @pytest.mark.parametrize(
        ('score', 'grade', 'cert'),
        [
            ('75', 'A+', True),
            ('70.00', 'A+', True),
            ('69.99', 'A', True),
            ('65', 'A', True),
            ('64.99', 'B+', True),
            ('60', 'B+', True),
            ('59.99', 'B', True),
            ('55', 'B', True),
            ('54.99', 'C+', True),
            ('50', 'C+', True),
            ('49.99', 'C', True),
            ('46', 'C', True),
            ('45.99', '', False),
            ('0', '', False),
        ],
    )
    def test_boundaries(self, score, grade, cert):
        band = band_for_score(NATIONAL_BANDS, Decimal(score))
        assert (band['grade'], band['certificate']) == (grade, cert)

    def test_boundary_is_not_off_by_one(self):
        """Negative: 69.99 must not be A+ and 45.99 must not earn a certificate."""
        assert band_for_score(NATIONAL_BANDS, Decimal('69.99'))['grade'] != 'A+'
        assert band_for_score(NATIONAL_BANDS, Decimal('45.99'))['certificate'] is False

    def test_scaling_rounds_before_grading(self):
        scale = GradeScale(max_score=75, midpoint_ability=0.0, points_per_logit=9.375)
        assert scale.scale_ability(0.0) == Decimal('37.50')
        assert scale.scale_ability(99) == Decimal('75.00')  # clamped
        assert scale.scale_ability(-99) == Decimal('0.00')
        # 69.996 -> 70.00 (half-up) -> A+, consistent with the displayed score
        ability = (69.996 - 37.5) / 9.375
        assert scale.scale_ability(ability) == Decimal('70.00')
        assert quantize_score('69.994') == Decimal('69.99')

    def test_scaling_is_monotonic(self):
        scale = GradeScale(max_score=75, midpoint_ability=0.0, points_per_logit=9.375)
        vals = [scale.scale_ability(x / 10) for x in range(-50, 51)]
        assert vals == sorted(vals)

    @pytest.mark.parametrize(
        'bad',
        [
            [],
            'x',
            [{'grade': 'A', 'min': 10, 'certificate': True}],  # no min=0 band
            [{'grade': 'A', 'min': 0}],  # missing certificate
            [
                {'grade': 'A', 'min': 0, 'certificate': True},
                {'grade': 'B', 'min': 0, 'certificate': True},
            ],  # duplicate mins
            [{'grade': 'A', 'min': -1, 'certificate': True}],
            [{'grade': 'A', 'min': 0, 'certificate': 'yes'}],
        ],
    )
    def test_invalid_bands_rejected(self, bad):
        with pytest.raises(ValidationError):
            validate_bands(bad)

    def test_national_bands_are_valid(self):
        validate_bands(NATIONAL_BANDS)


class TestAddYears:
    def test_three_years(self):
        from datetime import datetime

        assert add_years(datetime(2026, 11, 12, 10, 0), 3) == datetime(2029, 11, 12, 10, 0)

    def test_leap_day(self):
        from datetime import datetime

        assert add_years(datetime(2028, 2, 29), 3) == datetime(2031, 2, 28)
