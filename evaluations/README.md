# Evaluation plan

Run every case in `mcp_only`, `a2a_only`, and `verified` modes. Expand the seed
cases to at least 30 before publishing.

Record one JSON object per run:

- case ID and mode;
- success/failure classification;
- expected and observed amounts;
- numeric relative error;
- tool-selection result;
- A2A completion result;
- recovery result;
- end-to-end and component latency;
- prompt/completion tokens;
- estimated model and infrastructure cost;
- trace ID;
- SDK/package versions; and
- free-form failure notes.

Report median and p95, not only averages. Run enough warm and cold requests to
separate model variance from hosted-agent cold starts. Keep deterministic
calculation outside the LLM and score the LLM on orchestration and explanation.

Do not claim exchange-rate accuracy without retaining the source and observation
timestamp. This project is a systems benchmark, not financial advice.

