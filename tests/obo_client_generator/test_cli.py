# tests/test_generate_cli.py
from typer.testing import CliRunner

from obo_client_generator.cli import app  # the Typer instance


def test_generate_cli_dry():
    runner = CliRunner()
    result = runner.invoke(app, ["--dry"])
    # CLI should exit without error
    assert result.exit_code == 0, result.stdout
    # Dry-run banner should appear
    assert "DRY-RUN" in result.stdout
