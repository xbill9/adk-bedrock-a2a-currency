# Exchange-rate MCP server

## Local implementation

Install the project and launch the stdio server:

```bash
pip install -e .
currency-mcp-server
```

The local server uses deterministic fixture rates so it can be tested without
network access or credentials. It supports MCP JSON-RPC `initialize`, `ping`,
`tools/list`, and `tools/call`. Replace `StaticRateProvider` with a live provider
adapter for benchmark runs, retaining its timestamp and provider identity.

Expose a deliberately small tool surface:

```text
convert_currency(
  amount: decimal,
  source_currency: ISO-4217 code,
  target_currency: ISO-4217 code
) -> {
  converted_amount,
  rate,
  source_currency,
  target_currency,
  observed_at,
  provider
}
```

Requirements:

- validate inputs before calling the provider;
- use decimal arithmetic;
- return the provider and timestamp;
- set explicit connect/read timeouts;
- distinguish provider, validation, authentication, and transport errors;
- never place credentials in tool output or traces;
- add a deterministic fake provider for tests; and
- require approval for consequential tools, although this MVP is read-only.
