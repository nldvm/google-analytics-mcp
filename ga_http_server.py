"""HTTP + Google OAuth wrapper rond de officiële analytics-mcp tools (v0.8+)."""
import importlib
import os
import sys

# --- 1. Config controleren (fail closed) -------------------------------------
CLIENT_ID = os.environ.get("GA_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GA_OAUTH_CLIENT_SECRET")
BASE_URL = os.environ.get("GA_BASE_URL")
PORT = int(os.environ.get("PORT", "8080"))

if not (CLIENT_ID and CLIENT_SECRET and BASE_URL):
    sys.exit("GA_OAUTH_CLIENT_ID, GA_OAUTH_CLIENT_SECRET en GA_BASE_URL zijn verplicht.")

# --- 2. google.auth.default vervangen VOOR Google's code laadt ---------------
import google.auth
import google.auth._default
from google.auth.exceptions import DefaultCredentialsError
from google.oauth2.credentials import Credentials
from fastmcp.server.dependencies import get_access_token


def per_user_default(*args, **kwargs):
    """Geeft de credentials van de ingelogde gebruiker i.p.v. ADC."""
    try:
        token = get_access_token()
    except Exception:  # noqa: BLE001
        token = None
    if token is None:
        raise DefaultCredentialsError("Geen ingelogde gebruiker voor dit verzoek.")
    return Credentials(token=token.token), None


google.auth.default = per_user_default
google.auth._default.default = per_user_default
print("google.auth.default vervangen door per-user login", flush=True)

# --- 3. Read-only tools uit Google's coordinator halen -----------------------
coordinator = importlib.import_module("analytics_mcp.coordinator")


def resolve_func(name):
    return (getattr(coordinator, f"{name}_with_description", None)
            or getattr(coordinator, name, None))


entries = {}

rt = getattr(coordinator, "read_only_tools", None)
if callable(rt) and not isinstance(rt, (list, tuple)):
    try:
        rt = rt()
    except TypeError:
        rt = None

for item in (rt or []):
    if isinstance(item, str):
        name, func, desc = item, resolve_func(item), None
    else:
        func = getattr(item, "func", None)
        name = getattr(item, "name", None) or getattr(func, "__name__", None)
        if func is None and name:
            func = resolve_func(name)
        desc = getattr(item, "description", None)
    if not name or not callable(func):
        continue
    name = name.removesuffix("_with_description")
    entries.setdefault(name, (func, desc))

# Terugvaloptie: de lijst met read-only toolnamen.
if not entries:
    for name in getattr(coordinator, "_READ_ONLY_TOOL_NAMES", None) or []:
        func = resolve_func(name)
        if callable(func):
            entries.setdefault(name, (func, None))

if not entries:
    print("Type read_only_tools:", type(getattr(coordinator, "read_only_tools", None)), flush=True)
    print("Voorbeeld:", repr((rt or [None])[0])[:300] if rt else None, flush=True)
    print("_READ_ONLY_TOOL_NAMES:", getattr(coordinator, "_READ_ONLY_TOOL_NAMES", None), flush=True)
    sys.exit("Geen tools gevonden. Server start niet.")

# --- 4. Nieuwe server met Google-login en de tools van Google ----------------
from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.auth.providers.google import GoogleProvider  # noqa: E402
from fastmcp.tools import Tool  # noqa: E402

auth = GoogleProvider(
    client_id=CLIENT_ID,
    client_secret=CLIENT_SECRET,
    base_url=BASE_URL,
    required_scopes=[
        "openid",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/analytics.readonly",
    ],
)

app = FastMCP("Google Analytics", auth=auth)

for name, (func, desc) in entries.items():
    if not desc:
        extra = getattr(coordinator, f"_{name}_description", None)
        desc = extra if isinstance(extra, str) else (func.__doc__ or "").strip() or None
    app.add_tool(Tool.from_function(func, name=name, description=desc))
    print(f"Tool geladen: {name}", flush=True)

if __name__ == "__main__":
    app.run(transport="http", host="0.0.0.0", port=PORT)
