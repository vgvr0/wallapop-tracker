from typer.testing import CliRunner

from wallapop_tracker.cli import app


def test_market_value_cli_missing_listing(monkeypatch):
    result = CliRunner().invoke(app, ["market-value", "999999", "--json"])
    assert result.exit_code != 0
    assert "Unknown listing" in result.output


def test_market_value_cli_help():
    result = CliRunner().invoke(app, ["market-value", "--help"])
    assert result.exit_code == 0
    assert "Estimate observed market value" in result.output
