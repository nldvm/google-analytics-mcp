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

import analytics_mcp.tools
import analytics_mcp.tools.utils as ga_utils
from analytics_mcp.coordinator import mcp as ga_server

CLIENT_ID = os.environ.get("GA_OAUTH_CLIENT_ID")
CLIENT_SECRET = os.environ.get("GA_OAUTH_CLIENT_SECRET")
BASE_URL = os.environ.get("GA_BASE_URL")
PORT = int(os.environ.get("PORT", "8080"))

# Fail closed: zonder OAuth start de server niet.
if not (CLIENT_ID and CLIENT_SECRET and BASE_URL):
    sys.exit("GA_OAUTH_CLIENT_ID, GA_OAUTH_CLIENT_SECRET en GA_BASE_URL zijn verplicht.")


def per_user_credentials():
    token = get_access_token()
    if token is None:
        raise PermissionError("Geen ingelogde gebruiker voor dit verzoek.")
    return Credentials(token=token.token)


# Elke API-aanroep gebruikt nu de Google-login van de gebruiker.
ga_utils._create_credentials = per_user_credentials

# Laad alle tool-modules zodat ze zich registreren.
for mod in pkgutil.walk_packages(analytics_mcp.tools.__path__, "analytics_mcp.tools."):
    importlib.import_module(mod.name)


async def collect_tools(server):
    tm = getattr(server, "_tool_manager", None)
    if tm is not None and hasattr(tm, "list_tools"):
        result = tm.list_tools()
        if asyncio.iscoroutine(result):
            result = await result
    else:
        getter = getattr(server, "get_tools", None) or getattr(server, "list_tools")
        result = await getter()
    return list(result.values() if isinstance(result, dict) else result)


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

for t in asyncio.run(collect_tools(ga_server)):
    app.add_tool(Tool.from_function(t.fn, name=t.name, description=t.description))
    print(f"Tool geladen: {t.name}", flush=True)

if __name__ == "__main__":
    app.run(transport="http", host="0.0.0.0", port=PORT)
