#!/bin/bash
cd "$(dirname "$0")" || exit 1
.venv/bin/python main.py --no-analytics && .venv/bin/python export.py
