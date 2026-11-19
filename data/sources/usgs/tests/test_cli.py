import pytest

import pipeline
from conftest import NOW


def test_naive_timestamp_is_rejected(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        pipeline.main(
            [
                "--destination",
                "duckdb",
                "--since",
                "2026-10-16T00:00:00",
                "--until",
                "2026-10-16T01:00:00Z",
            ],
            NOW,
        )

    assert exit_info.value.code == 2
    assert "has no UTC offset" in capsys.readouterr().err


def test_destination_is_required(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        pipeline.main([], NOW)

    assert exit_info.value.code == 2
    assert "--destination" in capsys.readouterr().err
