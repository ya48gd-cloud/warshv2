"""Shared test helpers — importable by all test modules."""

def auth(token: str) -> dict:
    """Return Authorization header dict."""
    return {"Authorization": f"Bearer {token}"}
