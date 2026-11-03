#!/bin/bash

# Run from the project directory, including when called from another directory.
cd "$(dirname "$0")" || exit 1

# Prefer the project's installed dependencies over the system Python.
if [ -x venv/bin/python ]; then
    python_bin=venv/bin/python
elif [ -x .venv/bin/python ]; then
    python_bin=.venv/bin/python
else
    python_bin=python3
fi

# Leave omitted count to ACCOUNT_COUNT in .env (default: 0 = unlimited).
exec "$python_bin" main.py "$@"
