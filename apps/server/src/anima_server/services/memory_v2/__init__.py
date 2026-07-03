"""Stable memory v2 package boundary.

The heavy implementations still live under ``services.agent`` while this
package becomes the import surface for memory-v2 contracts and facades.
"""

__all__ = ["domain", "retrieval", "salience", "temporal"]
