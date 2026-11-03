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

# No arguments means exactly one attempt, regardless of ACCOUNT_COUNT in .env.
if [ "$#" -eq 0 ]; then
    set -- 1
fi

exec "$python_bin" main.py "$@"
