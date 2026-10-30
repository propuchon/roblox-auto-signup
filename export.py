import json
import os
import re
import shutil
import sys
from datetime import datetime

for name in ("accounts.txt", "cookies.json"):
    if not os.path.exists(name):
        sys.exit(f"{name} not found. Please run main.py first.")

with open("cookies.json", "r", encoding="utf-8") as file:
    accounts = json.load(file)

cookies = []
for account in accounts:
    value = next((c["value"] for c in account["cookies"] if c["name"] == ".ROBLOSECURITY"), None)
    if value:
        cookies.append(value)
    else:
        print(f"Warning: No .ROBLOSECURITY cookie found for user {account['username']}")

with open("accounts.txt", "r", encoding="utf-8") as file:
    userPass = re.findall(r"^Username: (.*?), Password: (.*?), Email: ", file.read(), re.MULTILINE)

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
shutil.move("accounts.txt", generateDir)
shutil.move("cookies.json", generateDir)

print(f"Saved {len(cookies)} cookies and {len(userPass)} accounts to {outputDir}")
