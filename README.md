> [!WARNING]
> The tool has been archived from 25 April 2026, meaning that it will no longer receive updates anymore and it might break at any time.

> [!NOTE]  
> Join the [Discord server](https://qing762.is-a.dev/discord) for issues. Thanks a lot!

> [!WARNING]
> Please be advised that usage of this tool is entirely at your own risk. I assume no responsibility for any adverse consequences arising from its use, and users are encouraged to exercise caution and their own judgment when utilising this tool.

# Roblox Auto Signup

A tool that auto-fetches a temporary email address and creates an account at https://roblox.com/.

## How it works

The process begins by utilising the [Mail.tm](https://mail.tm/) service to obtain a temporary email address. Then it will create an [Roblox](https://roblox.com) account. The email address is then utilized for verification for the [Roblox](https://roblox.com) account. Subsequently, another request is made to [Mail.tm](https://mail.tm/) to retrieve the email confirmation link. Upon account activation, the user can log in to Roblox and enjoy the game with the generated account.


## Features

- Able to use any Chromium-based browser
- Able to use non-authenticated proxies
- Be able to prompt to change to your own password instead of using the default one
- Password complexity checker for custom password
- Automatically checks if the username generated is taken or not. If yes, a new one would be generated
- Customizable username prefix
- Friends adding functionality
- Able to randomly customise the account avatar (Clothes, Body size, etc)
- Error handling
- Compatible with [Roblox Account Manager](https://github.com/ic3w0lf22/Roblox-Account-Manager)
- Structured username format
- Update checker functionality
- The script does the job by itself
- No webdriver required
- Fast execution time


## Installation / Usage

### [>>> VIDEO GUIDE <<<](https://qing762.is-a.dev/roblox-guide)

#### 1. Portable executable method:
- Just download the executable from the [releases tab](https://github.com/qing762/roblox-auto-signup/releases) and run it to generate accounts.
- If your antivirus has flagged for potential malware, that should be a false flag so feel free to safely ignore. If you dont trust it enough somehow, feel free to use [Step 2](https://github.com/qing762/roblox-auto-signup#2-python-file-method) and build it yourself instead.
- The account details should be generated at the `accounts.txt` file under the same directory.

#### 2. Python file method:
 - First, ensure that Python is installed:
 ``` shell
 https://www.python.org/downloads/
 ```
 - Run the following command:
 ```shell
 git clone https://github.com/qing762/roblox-auto-signup/ && cd roblox-auto-signup && pip install -r requirements.txt && python main.py
 ```

And you're all set! Follow the instructions as you interact with the Python file.

#### Run one attempt on macOS / Linux

If the project already has a `venv` or `.venv` with its dependencies installed:

```sh
./run.sh 1
```

The launcher uses the project's Python environment and works from any working
directory. Running `./run.sh` without arguments also makes one attempt. An explicit
count overrides `ACCOUNT_COUNT` in `.env`.

For a fresh installation:

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
./run.sh 1
```

Configuration is read from `.env`; missing settings are prompted in the terminal.
Set `BROWSER_PATH` to your Chrome/Chromium executable, or leave it empty to use
automatic detection. For a minimal first run, set `EMAIL_VERIFICATION=n`,
`CUSTOMIZATION=n`, and leave `FOLLOW_USERS` and `PROXIES` empty.

If a CAPTCHA is detected, the script closes its browser and stops the entire run.
It submits once and does not dismiss or retry the challenge.
Successful account details are saved to `accounts.txt` and `cookies.json` in the
project directory. A failed attempt exits with status 1.

#### Visible and headless browser modes

```sh
./run.sh 1 --no-headless  # visible browser (the default)
./run.sh 1 --headless     # Chromium runs in the background without a window
```

The equivalent Python command is `venv/bin/python main.py 1 --headless`.
Windowed mode types fields character by character. Headless mode fills each field
in one browser-protocol input call, preserving input events, and uses a fixed
1280×900 window size instead of maximizing a desktop window. Both modes use the
same form and proxy configuration.

In both modes, detecting a visible CAPTCHA marks the current attempt as failed,
closes its browser, exports the run, and exits without starting another attempt.
Unrecognized challenges or unfinished forms still fail after the normal
180-second timeout.

#### Automatic exports and stopping a run

Choose an export mode independently of the browser mode:

```sh
./run.sh 1 --export-mode dated
./run.sh 1 --export-mode append --headless
```

`dated` is the default. Each invocation creates its own
`output/<timestamp>-<id>/` directory. It includes the traditional `generate/`
folder (`accounts.txt`, `cookies.json`, `userfail.txt`, `timings.csv`) and `format/`
folder (`cookies_<count>.txt`, `userpass_<count>.txt`, `userfail_<count>.txt`).
It also keeps the detailed files below in the dated directory. Results are saved
when an attempt starts, when its stage changes, as soon as login cookies are
captured, and when the attempt finishes.

| File | Contents |
| --- | --- |
| `results.json` | Full run status, counts, credentials, cookies, stages, and failure reasons |
| `results.csv` | One row per attempted account, with status and reason |
| `success.txt` | Successful attempts as `username:password` |
| `failed.txt` | Failed attempts as `username:password` |
| `interrupted.txt` | Attempts stopped before completion |
| `in_progress.txt` | Attempts currently running |
| `cookies.txt` | Login cookies captured during this run |

`append` uses only these three export files, with no new dated folder or numbered
filenames:

```text
output/cookies.txt
output/userfail.txt
output/userpass.txt
```

Files are created only if missing. Existing bytes are preserved, and new lines
are appended and flushed to disk. Running the command again continues writing to
the same files. Stage updates and shutdown do not repeat a line already exported
for that attempt. Separate attempts remain separate records, even if their values
match. If a file's existing last line has no newline, one is appended before the
new entry.

Captured login cookies go to `cookies.txt`; credentials for created accounts go
to `userpass.txt` immediately. Failed attempts and interrupted attempts without a
confirmed created account go to `userfail.txt`. If signup succeeded before optional
processing was interrupted, its credentials stay in `userpass.txt`. This compact
mode does not create the detailed JSON/CSV report; use `dated` for failure reasons,
timings, and a separate interrupted status. The account-count argument still
controls how many attempts run; choosing append does not enable an infinite loop.

Neither mode needs a separate `export.py` command. Existing export files from
earlier runs are not moved or deleted.

Ctrl+C (`SIGINT`), `SIGTERM`, and terminal hangup (`SIGHUP`, where available) trigger
export and browser cleanup. Completed results retain their statuses; an unfinished
attempt becomes `interrupted` in dated mode. If signup completed before an interruption during
optional processing, `account_created=true` and the captured cookies remain in
`results.json` even though the attempt is marked `interrupted`.

Force kill (`SIGKILL`, such as `kill -9`) cannot run shutdown code or close the
browser. Previously written data remains available. In dated mode the last attempt may
still say `in_progress` and the run may still say `running`. `results.json` is the
canonical atomic snapshot; derived CSV/text files can lag if the process is killed
between file updates. An in-progress attempt does not imply that signup succeeded
or failed on the server. In append mode, an unfinished attempt cannot be added to
`userfail.txt` after SIGKILL, and a kill during a write can leave a partial final line.

#### Proxy lists with separate logins

Put one proxy per line in `proxies.txt` (this file is ignored by Git):

```text
user1:password1@host1:8080
user2:password2@host2:8080
```

Then configure `.env`:

```dotenv
PROXIES=
PROXY_FILE=proxies.txt
PROXY_USERNAME=
PROXY_PASSWORD=
```

Alternatively, put the entries on one `PROXIES=` line separated by commas.
Each entry accepts `[http:// or https://][username:password@]host:port`.
Percent-encode reserved characters in inline usernames/passwords, such as
`@` as `%40`, `,` as `%2C`, and `#` as `%23`.
Entries without inline credentials use the shared `PROXY_USERNAME` and
`PROXY_PASSWORD` if set. Exact duplicates are tested only once; entries with
different credentials remain separate even when the host is the same.

Each attempt uses the next working proxy in order, wrapping around if needed.
Authenticated proxies use a separate browser authentication extension per attempt
and disable incognito. The extension's temporary directory is removed afterward.
These settings apply to browser traffic and the proxy connectivity check;
other Python HTTP requests, including username validation and email, retain their
existing connection settings.



## Contributing

Contributions are always welcome!

To contribute, fork this repository and do anything you wish. After that, make a pull request.


## Feedback / Issues

If you have any feedback or issues running the code, please join the [Discord server](https://qing762.is-a.dev/discord)

### FOR ROBLOX CORPORATION EMPLOYEES, IF YOU WISH TO REQUEST THE TAKING DOWN THIS PROJECT

If the company wishes to discontinue or terminate this project, please do not hesitate to reach out to me. I can be reached at [Discord/qing762](https://discord.com/users/635765555277725696). Thank you for your attention to this matter.


## License

Licensed under the GNU General Public License v3.0. See [LICENSE](https://github.com/qing762/roblox-auto-signup/blob/main/LICENSE) for details.


---


[![Star History Chart](https://api.star-history.com/svg?repos=qing762/roblox-auto-signup&type=Date&theme=dark)](https://www.star-history.com/#qing762/roblox-auto-signup&Date)


---
