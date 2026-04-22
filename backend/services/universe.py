"""
Curated stock universe for the Momentum screener auto-scan.
~75 liquid, high-momentum names across AI, semis, cloud, fintech, and growth.
"""
from __future__ import annotations

MOMENTUM_UNIVERSE: list[str] = [
    # AI / Semiconductors
    "NVDA", "AMD", "AVGO", "QCOM", "MRVL", "ARM", "SMCI", "MU",
    "AMAT", "LRCX", "KLAC", "TSM", "TXN", "ON", "INTC", "MPWR", "ASML",
    # AI Software / Cloud Security
    "PLTR", "CRWD", "NET", "SNOW", "DDOG", "ZS", "PANW", "NOW",
    "CRM", "ORCL", "WDAY", "HUBS", "MDB", "APP", "GTLB", "CFLT",
    # Mega-cap tech
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "TSLA",
    # Fintech / Crypto-adjacent
    "COIN", "HOOD", "SQ", "AFRM", "SOFI", "MSTR", "PYPL",
    # Growth / Consumer tech
    "SHOP", "UBER", "ABNB", "RBLX", "RDDT",
    # Enterprise / Hardware
    "DELL", "HPE", "IBM", "CSCO",
    # Emerging momentum / Quantum / Space
    "NBIS", "IONQ", "RGTI", "OKLO", "SMR", "ACHR",
    # Healthcare growth
    "LLY", "MRNA", "HIMS",
    # Power / Energy (AI infrastructure)
    "VST", "CEG", "NRG", "FSLR",
    # Sector ETFs
    "QQQ", "SOXX", "SMH",
]

UNIVERSE_SIZE: int = len(MOMENTUM_UNIVERSE)
