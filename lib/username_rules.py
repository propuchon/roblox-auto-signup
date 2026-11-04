"""Local Roblox username syntax checks, not availability or content moderation.

Source: https://en.help.roblox.com/hc/en-us/articles/4412614080532-Xbox-Error-901
"""
import re


def validate_username(name):
    if not isinstance(name, str) or not 3 <= len(name) <= 20:
        raise ValueError("Username must contain 3-20 characters")
    if not re.fullmatch(r"[A-Za-z0-9_]+", name):
        raise ValueError("Username may contain only ASCII letters, digits and underscore")
    if name.count("_") > 1 or name.startswith("_") or name.endswith("_"):
        raise ValueError("Username may contain one underscore, never at the beginning or end")
    return name


def validate_prefix(prefix):
    # A trailing underscore is allowed here because a numeric suffix follows it.
    if not re.fullmatch(r"[A-Za-z0-9_]+", prefix):
        raise ValueError("NAME_PREFIX may contain only ASCII letters, digits and underscore")
    if prefix.startswith("_") or prefix.count("_") > 1:
        raise ValueError("NAME_PREFIX cannot start with underscore or contain more than one")
    return prefix
