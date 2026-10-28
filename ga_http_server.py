"""HTTP + Google OAuth wrapper rond de officiële analytics-mcp tools."""
import asyncio
import importlib
import os
import pkgutil
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
    token = None
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

# --- 3. Google's package en alle modules laden -------------------------------
import analytics_mcp  # noqa: E402

for info in pkgutil.walk_packages(analytics_mcp.__path__, "analytics_mcp."):
    try:
        importlib.import_module(info.name)
    except Exception as e:  # noqa: BLE001
        print(f"Overgeslagen: {info.name} ({e})", flush=True)

# --- 4. Server-objecten en tools verzamelen ----------------------------------
from fastmcp import FastMCP  # noqa: E402
from fastmcp.server.auth.providers.google import GoogleProvider  # noqa: E402
from fastmcp.tools import Tool  # noqa: E402

module_names = sorted(
    (n for n in list(sys.modules) if n == "analytics_mcp" or n.startswith("analytics_mcp.")),
    key=lambda n: (not n.startswith("analytics_mcp.tools"), n),
)
servers = []
for name in module_names:
    for obj in vars(sys.modules[name]).values():
        if type(obj).__name__ == "FastMCP" and all(obj is not s for s in servers):
            servers.append(obj)


async def tools_of(server):
    tm = getattr(server, "_tool_manager", None)
    result = None
    if tm is not None and hasattr(tm, "list_tools"):
        result = tm.list_tools()
    else:
        for attr in ("get_tools", "list_tools"):
            if hasattr(server, attr):
                result = getattr(server, attr)()
                break
    if asyncio.iscoroutine(result):
        result = await result
    if result is None:
        return []
    return list(result.values() if isinstance(result, dict) else result)


async def collect_all():
    found, seen = [], set()
    for s in servers:
        for t in await tools_of(s):
            fn = getattr(t, "fn", None)
            if fn is None or id(fn) in seen:
                continue
            seen.add(id(fn))
            found.append(t)
    return found


tools = asyncio.run(collect_all())

if not tools:
    coord = sys.modules.get("analytics_mcp.coordinator")
    print("Servers gevonden:", len(servers), flush=True)
    print("Modules:", module_names, flush=True)
    print("Inhoud coordinator:",
          [n for n in dir(coord) if not n.startswith("__")] if coord else None, flush=True)
    sys.exit("Geen tools gevonden. Server start niet.")

# --- 5. Nieuwe server met Google-login en de tools van Google ----------------
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

for t in tools:
    app.add_tool(Tool.from_function(t.fn, name=t.name, description=t.description))
    print(f"Tool geladen: {t.name}", flush=True)

if __name__ == "__main__":
    app.run(transport="http", host="0.0.0.0", port=PORT)
