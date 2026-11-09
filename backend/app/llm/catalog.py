"""Suggested models for the admin dropdowns. These are SUGGESTIONS: what you can actually use depends
on your Azure deployments / Bedrock model access. Every field also accepts a custom value."""

AZURE_DEPLOYMENTS = [
    {"value": "gpt-4.1", "label": "gpt-4.1 (recommended: strong tools + vision)"},
    {"value": "gpt-4o", "label": "gpt-4o (recommended: tools + vision)"},
    {"value": "gpt-4.1-mini", "label": "gpt-4.1-mini (cheaper, weaker analysis)"},
    {"value": "gpt-4o-mini", "label": "gpt-4o-mini (cheapest, weakest analysis)"},
    {"value": "gpt-5", "label": "gpt-5 (reasoning model, needs a newer API version)"},
    {"value": "gpt-5-mini", "label": "gpt-5-mini (reasoning model, needs a newer API version)"},
    {"value": "o4-mini", "label": "o4-mini (reasoning model)"},
    {"value": "o3", "label": "o3 (reasoning model)"},
]
AZURE_API_VERSIONS = [
    {"value": "2024-10-21", "label": "2024-10-21 (GA, stable: good for gpt-4o / gpt-4.1)"},
    {"value": "2025-04-01-preview", "label": "2025-04-01-preview (newer: try this for gpt-5 / o-series)"},
    {"value": "2025-03-01-preview", "label": "2025-03-01-preview"},
    {"value": "2024-08-01-preview", "label": "2024-08-01-preview (old)"},
    {"value": "2024-06-01", "label": "2024-06-01 (old GA)"},
]
BEDROCK_MODELS = [
    {"value": "us.anthropic.claude-sonnet-4-20250514-v1:0", "label": "Claude Sonnet 4 (US inference profile)"},
    {"value": "us.anthropic.claude-3-7-sonnet-20250219-v1:0", "label": "Claude 3.7 Sonnet (US inference profile)"},
    {"value": "us.anthropic.claude-3-5-sonnet-20241022-v2:0", "label": "Claude 3.5 Sonnet v2 (US inference profile)"},
    {"value": "anthropic.claude-3-haiku-20240307-v1:0", "label": "Claude 3 Haiku (cheap, weaker analysis)"},
    {"value": "us.amazon.nova-pro-v1:0", "label": "Amazon Nova Pro (US inference profile)"},
    {"value": "us.meta.llama3-3-70b-instruct-v1:0", "label": "Llama 3.3 70B (US inference profile)"},
]
AWS_REGIONS = ["us-east-1", "us-west-2", "eu-west-1", "eu-central-1", "ap-south-1", "ap-southeast-1", "ap-northeast-1"]


def catalog() -> dict:
    return {
        "azure_deployments": AZURE_DEPLOYMENTS, "azure_api_versions": AZURE_API_VERSIONS,
        "bedrock_models": BEDROCK_MODELS, "aws_regions": AWS_REGIONS,
        "notes": {
            "azure": "The deployment name is the name YOU gave the deployment in Azure AI Foundry. It is often the same as the model name, but not always.",
            "need": "Any chat model with tool calling works. Image attachments need a vision-capable model.",
        },
    }
