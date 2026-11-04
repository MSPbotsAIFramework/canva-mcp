"""Non-credential configuration. Credentials are never read from here -- see
middleware.py, which is the only place a Canva access token enters the process,
and only for the lifetime of a single request (SOP SS3.1)."""

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # extra="ignore": the runtime environment injects variables this project
    # never declared. Without this, an unknown env var crashes startup (SOP SS1.2).
    model_config = SettingsConfigDict(env_prefix="", extra="ignore")

    mcp_http_host: str = "0.0.0.0"
    mcp_http_port: int = 8080

    # Overridable only for local testing against a mock Canva server. Not a
    # credential, not secret, not read from a Header -- just an endpoint.
    canva_api_base_url: str = "https://api.canva.com/rest/v1"


def get_settings() -> Settings:
    return Settings()
