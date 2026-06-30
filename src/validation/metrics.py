"""Metrics for WS3 validation runs."""

from __future__ import annotations

from dataclasses import dataclass
from math import e, sqrt
from statistics import NormalDist, fmean, pvariance


EULER_MASCHERONI = 0.5772156649015329


@dataclass(frozen=True)
class DeflatedSharpeDetails:
    """Detailed DSR components for one selected strategy."""

    observed_sharpe: float
    sample_length: int
    skewness: float
    kurtosis: float
    sharpe_standard_error: float
    candidate_batch_size: int
    candidate_sharpe_variance: float
    benchmark_sharpe: float
    z_score: float
    probability: float


def compute_sharpe_ratio(returns: list[float]) -> float:
    """Return a simple sample Sharpe ratio for a sequence of returns."""

    sample_size = len(returns)
    if sample_size < 2:
        return 0.0

    mean_return = fmean(returns)
    variance = sum((value - mean_return) ** 2 for value in returns) / (sample_size - 1)
    if variance <= 0.0:
        return 0.0

    return mean_return / sqrt(variance) * sqrt(sample_size)


def _skewness(returns: list[float]) -> float:
    sample_size = len(returns)
    if sample_size < 3:
        return 0.0

    mean_return = fmean(returns)
    centered = [value - mean_return for value in returns]
    m2 = sum(value**2 for value in centered) / sample_size
    if m2 == 0.0:
        return 0.0

    m3 = sum(value**3 for value in centered) / sample_size
    return m3 / (m2 ** 1.5)


def _kurtosis(returns: list[float]) -> float:
    sample_size = len(returns)
    if sample_size < 4:
        return 3.0

    mean_return = fmean(returns)
    centered = [value - mean_return for value in returns]
    m2 = sum(value**2 for value in centered) / sample_size
    if m2 == 0.0:
        return 3.0

    m4 = sum(value**4 for value in centered) / sample_size
    return m4 / (m2**2)


def compute_deflated_sharpe_details(
    returns: list[float],
    candidate_sharpes: list[float],
) -> DeflatedSharpeDetails:
    """
    Compute the Deflated Sharpe Ratio using an explicit candidate batch.

    This follows Bailey and Lopez de Prado's DSR framing:
    - the selected strategy contributes its own observed Sharpe and return moments
    - the candidate batch contributes N and the variance across tried Sharpes
    """

    sample_length = len(returns)
    observed_sharpe = compute_sharpe_ratio(returns)
    if sample_length < 2:
        return DeflatedSharpeDetails(
            observed_sharpe=observed_sharpe,
            sample_length=sample_length,
            skewness=0.0,
            kurtosis=3.0,
            sharpe_standard_error=0.0,
            candidate_batch_size=len(candidate_sharpes),
            candidate_sharpe_variance=0.0,
            benchmark_sharpe=0.0,
            z_score=0.0,
            probability=0.0,
        )

    if not candidate_sharpes:
        raise ValueError("candidate_sharpes must contain at least one batch candidate Sharpe ratio")

    skewness = _skewness(returns)
    kurtosis = _kurtosis(returns)
    denominator = 1.0 - (skewness * observed_sharpe) + (((kurtosis - 1.0) / 4.0) * observed_sharpe**2)
    if denominator <= 0.0:
        return DeflatedSharpeDetails(
            observed_sharpe=observed_sharpe,
            sample_length=sample_length,
            skewness=skewness,
            kurtosis=kurtosis,
            sharpe_standard_error=0.0,
            candidate_batch_size=len(candidate_sharpes),
            candidate_sharpe_variance=0.0,
            benchmark_sharpe=0.0,
            z_score=0.0,
            probability=0.0,
        )

    sharpe_standard_error = sqrt(denominator / (sample_length - 1))
    candidate_batch_size = len(candidate_sharpes)
    candidate_sharpe_variance = pvariance(candidate_sharpes) if candidate_batch_size > 1 else 0.0

    if candidate_batch_size <= 1 or candidate_sharpe_variance <= 0.0:
        benchmark_sharpe = 0.0
    else:
        distribution = NormalDist()
        expected_max_standard_normal = (
            ((1.0 - EULER_MASCHERONI) * distribution.inv_cdf(1.0 - (1.0 / candidate_batch_size)))
            + (
                EULER_MASCHERONI
                * distribution.inv_cdf(1.0 - (1.0 / (candidate_batch_size * e)))
            )
        )
        benchmark_sharpe = sqrt(candidate_sharpe_variance) * expected_max_standard_normal

    if sharpe_standard_error == 0.0:
        probability = 1.0 if observed_sharpe > benchmark_sharpe else 0.0
        z_score = float("inf") if probability == 1.0 else float("-inf")
    else:
        z_score = (observed_sharpe - benchmark_sharpe) / sharpe_standard_error
        probability = NormalDist().cdf(z_score)

    return DeflatedSharpeDetails(
        observed_sharpe=observed_sharpe,
        sample_length=sample_length,
        skewness=skewness,
        kurtosis=kurtosis,
        sharpe_standard_error=sharpe_standard_error,
        candidate_batch_size=candidate_batch_size,
        candidate_sharpe_variance=candidate_sharpe_variance,
        benchmark_sharpe=benchmark_sharpe,
        z_score=z_score,
        probability=probability,
    )


def compute_deflated_sharpe_ratio(returns: list[float], candidate_sharpes: list[float]) -> float:
    """Return only the DSR probability when callers do not need the internals."""

    return compute_deflated_sharpe_details(
        returns=returns,
        candidate_sharpes=candidate_sharpes,
    ).probability
