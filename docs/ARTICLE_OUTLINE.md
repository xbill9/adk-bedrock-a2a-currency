# Article outline

## Working title

**Can Amazon Bedrock AgentCore Talk to Google ADK? Building and Benchmarking a
Cross-Cloud A2A Agent**

## Thesis

A2A makes cross-framework delegation possible, but interoperability should be
judged by reproducibility, failure handling, latency, and cost—not by whether a
single demo request succeeds.

## Sections

1. **Why this test**
   - Existing Google ADK/A2A currency sample
   - Need for a real AWS-native coordinator (Strands + Bedrock + AgentCore)
   - Research questions and non-goals
   - Prior result: the same benchmark ran Azure Foundry → GCP; this measures
     the AWS → GCP leg with the same domain core
2. **What each layer does**
   - Bedrock AgentCore Runtime hosting
   - Strands Agents orchestration on a Bedrock model
   - MCP tools
   - A2A agent discovery/delegation
3. **Architecture and trust boundaries**
4. **Building the deterministic baseline**
5. **Connecting the exchange-rate MCP server**
6. **Connecting the Google ADK agent over A2A v1.0**
7. **Deploying the coordinator to AgentCore Runtime**
8. **Authentication and least privilege (IAM execution role, SigV4 invoke)**
9. **Evaluation methodology**
10. **Results**
    - correctness and completion
    - median/p95 latency
    - token/cost overhead
    - recovery behavior
    - comparison with the retained Azure-coordinator baseline
11. **What broke**
    - protocol mismatches
    - SDK/deployment changes
    - authentication and agent-card issues
12. **When A2A is justified**
13. **Reproduction instructions and source**

## Evidence checklist

- Exact test date, AWS region, Bedrock model ID, and package versions
- Commit SHAs for both implementations
- Agent-card samples with sensitive values removed
- Raw evaluation output
- Pricing assumptions
- Trace screenshots with identifiers redacted
- Clear labels for preview capabilities
