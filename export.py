import json
import os
import re
import shutil
import sys
from datetime import datetime

# A run where every account failed only leaves timings.csv behind
files = [name for name in ("accounts.txt", "cookies.json", "timings.csv") if os.path.exists(name)]
if not files:
    sys.exit("No accounts.txt, cookies.json or timings.csv found. Please run main.py first.")

accounts = []
if os.path.exists("cookies.json"):
    with open("cookies.json", "r", encoding="utf-8") as file:
        accounts = json.load(file)

cookies = []
for account in accounts:
    value = next((c["value"] for c in account["cookies"] if c["name"] == ".ROBLOSECURITY"), None)
    if value:
        cookies.append(value)
    else:
        print(f"Warning: No .ROBLOSECURITY cookie found for user {account['username']}")

userPass = []
if os.path.exists("accounts.txt"):
    with open("accounts.txt", "r", encoding="utf-8") as file:
        userPass = re.findall(r"^Username: (.*?), Password: (.*?), Email: ", file.read(), re.MULTILINE)

timings = []
if os.path.exists("timings.csv"):
    with open("timings.csv", "r", encoding="utf-8") as file:
        timings = [line.rstrip("\n").split(",", 3) for line in file if line.strip()]

outputDir = os.path.join("output", datetime.now().strftime("%Y-%m-%d-%H%M%S"))
generateDir = os.path.join(outputDir, "generate")
formatDir = os.path.join(outputDir, "format")
os.makedirs(generateDir)
os.makedirs(formatDir)

with open(os.path.join(formatDir, f"cookies_{len(cookies)}.txt"), "w", encoding="utf-8") as file:
    file.write("".join(f"{value}\n" for value in cookies))

with open(os.path.join(formatDir, f"userpass_{len(userPass)}.txt"), "w", encoding="utf-8") as file:
    file.write("".join(f"{username}:{password}\n" for username, password in userPass))

# Move instead of copy so the next main.py run starts with fresh files
for name in files:
    shutil.move(name, generateDir)

print(f"Saved {len(cookies)} cookies and {len(userPass)} accounts to {outputDir}")

if timings:
    failed = [row for row in timings if row[1] == "failed"]
    seconds = [float(row[2]) for row in timings if row[1] == "success"]
    print(f"Success: {len(seconds)}, Failed: {len(failed)}")
    if seconds:
        print(f"Time per successful account: avg {sum(seconds) / len(seconds):.1f}s, min {min(seconds):.1f}s, max {max(seconds):.1f}s")
