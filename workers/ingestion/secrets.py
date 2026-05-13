"""Pull narrative-platform secrets from Azure Key Vault using managed identity."""
from __future__ import annotations

from dataclasses import dataclass

from azure.identity import DefaultAzureCredential
from azure.keyvault.secrets import SecretClient


@dataclass(frozen=True)
class IngestionSecrets:
    reddit_author_salt: str


def fetch_secrets(keyvault_uri: str) -> IngestionSecrets:
    credential = DefaultAzureCredential()
    client = SecretClient(vault_url=keyvault_uri, credential=credential)
    return IngestionSecrets(
        reddit_author_salt=client.get_secret("reddit-author-salt").value or "",
    )
