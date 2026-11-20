import os
import sys


def secret_preview(value: str | None) -> str:
    if not value:
        return ""
    if len(value) <= 2:
        return f"{value[:1]}*****" if len(value) == 2 else "*****"
    if len(value) <= 8:
        return f"{value[:1]}*****{value[-1:]}"
    prefix_length = 6 if value.startswith("sk-") else 4
    # Always omit at least one character, including for unusually short keys.
    prefix_length = min(prefix_length, len(value) - 5)
    return f"{value[:prefix_length]}*****{value[-4:]}"


def _credential_service() -> str:
    # Keep the installed runtime's existing Windows Credential Manager entries.
    return "DouyinCut" if getattr(sys, "frozen", False) else "DouyinCut Dev"


def read_secret(name: str, service: str | None = None) -> str | None:
    # Process environment supports development without persisting a secret in a file.
    value = os.environ.get(f"DOUYINCUT_{name.upper()}")
    if value:
        return value
    try:
        import keyring
        return keyring.get_password(_credential_service() if service is None else service, name)
    except Exception:
        return None


def write_secret(name: str, value: str, service: str | None = None):
    import keyring
    if service is None:
        service = _credential_service()
    if value:
        keyring.set_password(service, name, value)
    else:
        try:
            keyring.delete_password(service, name)
        except keyring.errors.PasswordDeleteError:
            pass


def storage_status() -> dict:
    """Report whether the packaged app can reach a system credential store, never any value."""
    try:
        import keyring
        store = keyring.get_keyring()
        backend = f"{type(store).__module__}.{type(store).__qualname__}"
        available = getattr(store, "priority", 0) > 0
    except Exception:
        return {"available": False, "backend": "unavailable"}
    return {"available": bool(available), "backend": backend}
