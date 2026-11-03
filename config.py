import os
from pathlib import Path

SECRET_KEYS = {"PASSWORD", "PROXIES", "PROXY_USERNAME", "PROXY_PASSWORD"}


def loadEnv(path=None):
    # Minimal .env reader: KEY=VALUE lines, # comments; real environment variables take precedence
    if path is None:
        path = Path(__file__).resolve().parent / ".env"
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as file:
        for line in file:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def ask(key, prompt):
    # Answer from the environment when the key is set (even to an empty value), otherwise prompt as usual.
    # The value is used once, so a rejected answer falls back to typing instead of looping forever.
    value = os.environ.pop(key, None)
    if value is None:
        return input(prompt)
    print(f"\n{key}={'[hidden]' if key in SECRET_KEYS and value else value} (from .env)")
    return value


loadEnv()
