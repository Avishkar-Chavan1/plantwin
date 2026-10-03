from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from packages.benchmarks.tennessee_eastman import (
    TEP_VARIABLE_COUNT,
    calibrate_tep_surrogate,
    evaluate_tep_surrogate,
    load_tep_dataset,
)


def _write_matrix(path: Path, matrix: np.ndarray) -> None:
    path.write_text(
        "\n".join(" ".join(f"{item:.7g}" for item in row) for row in matrix) + "\n",
        encoding="utf-8",
    )


def _trajectory(samples: int = 30) -> np.ndarray:
    time = np.arange(samples, dtype=float)
    matrix = np.column_stack(
        [time * (index + 1) * 0.01 + index for index in range(TEP_VARIABLE_COUNT)]
    )
    # XMEAS(9) has a learnable next-step relation, rather than a constant signal.
    matrix[:, 8] = 100.0 + 0.5 * time + 0.1 * matrix[:, 0]
    return matrix


def test_ingestion_accepts_time_by_variable_and_variable_by_time_layouts(tmp_path: Path) -> None:
    matrix = _trajectory()
    rows_path = tmp_path / "rows.dat"
    columns_path = tmp_path / "columns.dat"
    _write_matrix(rows_path, matrix)
    _write_matrix(columns_path, matrix.T)

    rows = load_tep_dataset(rows_path, name="rows", source_url="https://example.test/rows")
    columns = load_tep_dataset(
        columns_path, name="columns", source_url="https://example.test/columns"
    )

    assert rows.values.shape == (30, TEP_VARIABLE_COUNT)
    assert np.allclose(rows.values, columns.values)
    assert rows.elapsed_seconds[-1] == pytest.approx(29 * 180)


def test_calibration_keeps_a_chronological_holdout_and_gates_extrapolation(tmp_path: Path) -> None:
    calibration_path = tmp_path / "calibration.dat"
    challenge_path = tmp_path / "challenge.dat"
    calibration_matrix = _trajectory(60)
    challenge_matrix = _trajectory(30) + 10_000.0
    _write_matrix(calibration_path, calibration_matrix.T)
    _write_matrix(challenge_path, challenge_matrix)
    calibration_dataset = load_tep_dataset(
        calibration_path, name="calibration", source_url="https://example.test/calibration"
    )
    challenge_dataset = load_tep_dataset(
        challenge_path, name="challenge", source_url="https://example.test/challenge"
    )

    result = calibrate_tep_surrogate(calibration_dataset)
    evaluation = evaluate_tep_surrogate(result.model, challenge_dataset)

    assert result.split.train_end == int(59 * 0.6)
    assert result.split.validation_end == int(59 * 0.6) + int(59 * 0.2)
    assert result.held_out_test.observation_count == 59 - result.split.validation_end
    assert evaluation.outside_envelope_count == evaluation.observation_count
    assert evaluation.deployment_disposition == "ABSTAIN_OUTSIDE_CALIBRATION_ENVELOPE"
    assert evaluation.drift.status == "MODEL_DRIFT_DETECTED"
