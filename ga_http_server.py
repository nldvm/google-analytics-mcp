"""HTTP + Google OAuth wrapper rond de officiële analytics-mcp tools."""
import asyncio
import importlib
import os
import pkgutil
import sys

from fastmcp import FastMCP
from fastmcp.server.auth.providers.google import GoogleProvider
from fastmcp.server.dependencies import get_access_token
from fastmcp.tools import Tool
from google.oauth2.credentials import Credentials

import analytics_mcp

CLIENT_ID = os.environ.get("GA_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GA_OAUTH_CLIENT_SECRET")
BASE_URL = os.environ.get("GA_BASE_URL")
PORT = int(os.environ.get("PORT", "8080"))

# Fail closed: zonder OAuth start de server niet.
if not (CLIENT_ID and CLIENT_SECRET and BASE_URL):
    sys.exit("GA_OAUTH_CLIENT_ID, GA_OAUTH_CLIENT_SECRET en GA_BASE_URL zijn verplicht.")


def per_user_credentials(*args, **kwargs):
    token = get_access_token()
    if token is None:
        raise PermissionError("Geen ingelogde gebruiker voor dit verzoek.")
    return Credentials(token=token.token)


# 1. Credentials-functie van Google vervangen (vóór het laden van de tools).
patched = []
for mod_name in ("analytics_mcp.tools.utils", "analytics_mcp.tools.client"):
    try:
        mod = importlib.import_module(mod_name)
    except ImportError:
        continue
    for fn_name in ("_create_credentials", "_get_credentials"):
        if hasattr(mod, fn_name):
            setattr(mod, fn_name, per_user_credentials)
            patched.append(f"{mod_name}.{fn_name}")

if not patched:
    sys.exit("Geen credentials-functie gevonden om te vervangen. Server start niet.")
print("Credentials vervangen in:", ", ".join(patched), flush=True)

# 2. Alle modules van Google's package laden.
for info in pkgutil.walk_packages(analytics_mcp.__path__, "analytics_mcp."):
    try:
        importlib.import_module(info.name)
    except Exception as e:  # noqa: BLE001
        print(f"Overgeslagen: {info.name} ({e})", flush=True)

# 3. Alle server-objecten zoeken (tools-modules eerst).
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
    print("Inhoud coordinator:",
          [n for n in dir(coord) if not n.startswith("__")] if coord else None, flush=True)
    sys.exit("Geen tools gevonden. Server start niet.")

# 4. Nieuwe server met Google-login en de tools van Google.
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
