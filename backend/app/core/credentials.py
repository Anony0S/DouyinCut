import os


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


def read_secret(name: str) -> str | None:
    # Process environment supports development without persisting a secret in a file.
    value = os.environ.get(f"DOUYINCUT_{name.upper()}")
    if value:
        return value
    try:
        import keyring
        return keyring.get_password("DouyinCut", name)
    except Exception:
        return None


def write_secret(name: str, value: str):
    import keyring
    if value:
        keyring.set_password("DouyinCut", name, value)
    else:
        try:
            keyring.delete_password("DouyinCut", name)
        except keyring.errors.PasswordDeleteError:
            pass
