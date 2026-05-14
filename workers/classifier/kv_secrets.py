"""Fetch secrets from Azure Key Vault for the classifier worker."""
from __future__ import annotations

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient

# Default prompt template — overridden by KV secret `conviction-prompt-v1`.
# Keep in sync with docs/NARRATIVE_METHODOLOGY.md §3.
_DEFAULT_PROMPT = """\
Classify the Reddit post below into exactly one conviction state.

Ticker: {ticker}
Extractor sentiment: {sentiment}
Post content: {rationale}

Conviction states:
- researched_bull: cites data, metrics, product or financial evidence for a bullish thesis
- researched_bear: critical thesis with evidence against the stock
- emotional_bull: enthusiasm or hype without substantive evidence ("to the moon", "can't lose")
- emotional_bear: fear, panic, or FUD without evidence
- uncertainty: explicitly undecided or confused about the thesis
- earnings_focused: tied to specific upcoming or recent earnings event
- product_thesis: driven by product or technology roadmap belief
- ecosystem_thesis: driven by industry-wide tailwind or macro trend
- institutional_watch: mentions analyst upgrades, price targets, or institutional buying
- exit_signal: profit-taking, covering a position, or conviction loss

Respond with JSON only.\
"""


@dataclass(frozen=True)
class ClassifierSecrets:
    openai_api_key: str
    openai_endpoint: str
    openai_deployment: str
    prompt_template: str


def fetch_secrets(keyvault_uri: str) -> ClassifierSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)

    def _get(name: str) -> str:
        return client.get_secret(name).value or ""

    def _get_optional(name: str, default: str) -> str:
        try:
            value = client.get_secret(name).value
            return value if value else default
        except Exception:
            return default

    return ClassifierSecrets(
        openai_api_key=_get("openai-api-key"),
        openai_endpoint=_get("openai-endpoint"),
        openai_deployment=_get_optional("openai-deployment", "gpt-4o-mini"),
        prompt_template=_get_optional("conviction-prompt-v1", _DEFAULT_PROMPT),
    )
