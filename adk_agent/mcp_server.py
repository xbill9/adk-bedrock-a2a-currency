"""FastMCP exchange-rate server colocated with the benchmark agent container.

Copied from xbill9/currency-agent@aeef3c4 mcp-server/server.py (IPv4 pin
dropped; it was a local-sandbox workaround). Serves live Frankfurter rates
over streamable HTTP so the ADK agent's MCP tool works identically on Cloud
Run and locally.
"""

import asyncio
import logging
import os

import httpx
from fastmcp import FastMCP

logger = logging.getLogger(__name__)
logging.basicConfig(format="[%(levelname)s]: %(message)s", level=logging.INFO)

mcp = FastMCP("Currency MCP Server 💵")


@mcp.tool()
def get_exchange_rate(
    currency_from: str = "USD",
    currency_to: str = "EUR",
    currency_date: str = "latest",
):
    """Use this to get current exchange rate.

    Args:
        currency_from: The currency to convert from (e.g., "USD").
        currency_to: The currency to convert to (e.g., "EUR").
        currency_date: The date for the exchange rate or "latest". Defaults to "latest".

    Returns:
        A dictionary containing the exchange rate data, or an error message if the request fails.
    """
    logger.info(
        f"--- 🛠️ Tool: get_exchange_rate called for converting {currency_from} to {currency_to} ---"
    )
    try:
        response = httpx.get(
            f"https://api.frankfurter.dev/v1/{currency_date}",
            params={"from": currency_from, "to": currency_to},
        )
        response.raise_for_status()

        data = response.json()
        if "rates" not in data:
            logger.error(f"❌ rates not found in response: {data}")
            return {"error": "Invalid API response format."}
        logger.info(f"✅ API response: {data}")
        return data
    except httpx.HTTPError as e:
        logger.error(f"❌ API request failed: {e}")
        return {"error": f"API request failed: {e}"}
    except ValueError:
        logger.error("❌ Invalid JSON response from API")
        return {"error": "Invalid JSON response from API."}


if __name__ == "__main__":
    logger.info(f"🚀 MCP server started on port {os.getenv('MCP_PORT', '8081')}")
    asyncio.run(
        mcp.run_async(
            transport="http",
            host="127.0.0.1",
            port=int(os.getenv("MCP_PORT", "8081")),
        )
    )
