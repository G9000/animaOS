from __future__ import annotations

import logging

from mnemonic import Mnemonic

logger = logging.getLogger(__name__)

RECOVERY_DOMAIN_PREFIX = "recovery:"


def generate_recovery_phrase() -> str:
    """Generate a 12-word BIP39 mnemonic recovery phrase."""
    m = Mnemonic("english")
    return m.generate(strength=128)  # 128 bits → 12 words
