import os

from strands.models.bedrock import BedrockModel


def load_model() -> BedrockModel:
    """Get Bedrock model client using IAM credentials.

    Defaults to Nova Micro: the cheapest Bedrock model with tool calling,
    which is all the coordinator needs (the benchmark tool does the work).
    Model IDs must be inference-profile IDs (us./global. prefix).
    """
    return BedrockModel(
        model_id=os.getenv("BEDROCK_MODEL_ID", "us.amazon.nova-micro-v1:0"),
        max_tokens=int(os.getenv("BEDROCK_MAX_TOKENS", "1024")),
    )
