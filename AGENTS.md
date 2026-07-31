# Contributor guidance

- Preserve the cross-framework benchmark; do not turn the project into a generic
  chatbot.
- Keep domain logic independent of fast-moving SDKs. Put AWS, Google, A2A, and
  MCP integrations behind adapters.
- Use `Decimal` for money and rates.
- Never ask an LLM to perform or verify arithmetic that deterministic code can do.
- Do not commit secrets, `.env`, raw tokens, account IDs, or unredacted trace
  exports.
- Pin fast-moving dependencies after an end-to-end version is known to work.
- Add tests for every bug found during interoperability work.
- Retain raw benchmark evidence but exclude sensitive content and generated bulk
  results from Git unless intentionally publishing a sanitized dataset.
- Distinguish observed results from hypotheses in documentation.

