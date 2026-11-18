"""supabase/config.toml: every function called without a signed-in user keeps the gateway JWT check off. If a redeploy
put it back, every confirmation and unsubscribe link would answer 401 from the gateway."""

import tomllib
from pathlib import Path

import pytest

SUPABASE = Path(__file__).resolve().parents[1] / "supabase"


@pytest.mark.parametrize(
    "name", ["confirm-email", "unsubscribe", "report-site", "delete-account", "mcp", "contact", "admin-account"]
)
def test_the_function_does_not_ask_the_gateway_for_a_jwt(name: str) -> None:
    config = tomllib.loads((SUPABASE / "config.toml").read_text(encoding="utf-8"))
    assert config["functions"][name]["verify_jwt"] is False
    assert (SUPABASE / "functions" / name / "index.ts").is_file()


def test_the_email_settings_function_asks_the_gateway_for_a_jwt() -> None:
    """Turning the email on or off needs a signed-in user: the gateway checks the access token (step 6.2)."""
    config = tomllib.loads((SUPABASE / "config.toml").read_text(encoding="utf-8"))
    assert config["functions"]["email-settings"]["verify_jwt"] is True
    assert (SUPABASE / "functions" / "email-settings" / "index.ts").is_file()
