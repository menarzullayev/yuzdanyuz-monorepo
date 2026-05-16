"""Bayesian math unit tests — pure functions, DB kerakmas."""

import pytest

from apps.intelligence import bayesian


@pytest.mark.unit
class TestBayesian:
    def test_initial_state_is_uniform(self):
        # Prior alpha=1, beta=1 → mean=0.5
        assert bayesian.mastery(1, 1) == 0.5

    def test_one_correct_increases_mastery(self):
        a, b = bayesian.update(1, 1, is_correct=True)
        assert (a, b) == (2, 1)
        assert bayesian.mastery(a, b) == pytest.approx(2 / 3)

    def test_one_incorrect_decreases_mastery(self):
        a, b = bayesian.update(1, 1, is_correct=False)
        assert (a, b) == (1, 2)
        assert bayesian.mastery(a, b) == pytest.approx(1 / 3)

    def test_more_samples_increase_confidence(self):
        small_conf = bayesian.confidence(2, 2)  # 1/3 ratio, 2 samples
        large_conf = bayesian.confidence(20, 20)  # same ratio, 40 samples
        assert large_conf > small_conf

    def test_perfect_score_high_mastery(self):
        # 10 correct in a row
        a, b = 1, 1
        for _ in range(10):
            a, b = bayesian.update(a, b, True)
        # alpha=11, beta=1 → mean=11/12 ≈ 0.917
        assert bayesian.mastery(a, b) > 0.9

    def test_is_weak_threshold(self):
        assert bayesian.is_weak(0.3) is True
        assert bayesian.is_weak(0.5) is False  # threshold default 0.5
        assert bayesian.is_weak(0.7) is False

    def test_variance_decreases_with_samples(self):
        v1 = bayesian.variance(2, 2)
        v2 = bayesian.variance(20, 20)
        assert v2 < v1

    def test_confidence_clamped(self):
        # Very high samples → confidence approaches 1 but never exceeds
        c = bayesian.confidence(1000, 1000)
        assert 0.0 <= c <= 1.0
