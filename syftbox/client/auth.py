from typing import Optional

import httpx
import typer
from rich import print as rprint
from rich.prompt import Prompt

from syftbox.lib.client_config import SyftClientConfig
from syftbox.lib.http import HEADER_SYFTBOX_VERSION
from syftbox import __version__


def has_valid_access_token(conf: SyftClientConfig, auth_client: httpx.Client) -> bool:
    """Returns True if conf has a valid access token that matches the email in the config."""
    if not conf.access_token:
        return False
    response = auth_client.post("/auth/whoami", headers={"Authorization": f"Bearer {conf.access_token}", HEADER_SYFTBOX_VERSION: __version__})
    if response.status_code == 401:
        rprint("[red]Invalid access token, re-authenticating.[/red]")
        return False
    elif response.status_code >= 400:
        rprint(f"[red]An unexpected error occurred: {response.text}, re-authenticating.[/red]")
        return False

    authed_email = response.json().get("email", None)
    is_valid = authed_email == conf.email
    if not is_valid:
        rprint(
            f"[red]Invalid access token for {conf.email}, this token is for {authed_email}. re-authenticating.[/red]"
        )
    return is_valid


def request_email_token(auth_client: httpx.Client, conf: SyftClientConfig) -> Optional[str]:
    """
    if auth is enabled, send an email token to the user's email address.
    if auth is disabled, the token will be returned directly in the response instead.

    Args:
        auth_client (httpx.Client): httpx client
        conf (SyftClientConfig): client config

    Returns:
        Optional[str]: email token if auth is disabled, None if auth is enabled
    """
    response = auth_client.post("/auth/request_email_token", json={"email": conf.email}, headers={HEADER_SYFTBOX_VERSION: __version__})
    response.raise_for_status()
    return response.json().get("email_token", None)


def get_access_token(
    conf: SyftClientConfig,
    auth_client: httpx.Client,
    email_token: Optional[str] = None,
) -> str:
    """
    Validate the email token and return the access token.

    Args:
        auth_client (httpx.Client): httpx client
        email_token (Optional[str]): Optional email token. If not provided,
            the user will be prompted to input it.

    Returns:
        str: access token
    """
    if not email_token:
        email_token = Prompt.ask(
            f"[yellow]Please enter the token sent to {conf.email}. Also check your spam folder[/yellow]"
        )

    response = auth_client.post(
        "/auth/validate_email_token",
        headers={"Authorization": f"Bearer {email_token}", HEADER_SYFTBOX_VERSION: __version__},
        params={"email": conf.email},
    )

    if response.status_code == 200:
        return response.json()["access_token"]
    elif response.status_code == 401:
        rprint("[red]Invalid token, please copy the full token from your email[/red]")
        return get_access_token(conf, auth_client)
    else:
        rprint(f"[red]An unexpected error occurred: {response.text}[/red]")
        raise typer.Exit(1)


def authenticate_user(conf: SyftClientConfig, login_client: httpx.Client) -> Optional[str]:
    if has_valid_access_token(conf, login_client):
        return conf.access_token

    email_token = request_email_token(login_client, conf)
    return get_access_token(conf, login_client, email_token)
