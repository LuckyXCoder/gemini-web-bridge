# Gemini Web Bridge

An independently maintained derivative of [HanaokaYuzu/Gemini-API](https://github.com/HanaokaYuzu/Gemini-API), maintained by **LuckyXCoder**.

This is not an official Google project and is not an official continuation endorsed by the upstream maintainers. The original project and its contributors retain their credits. We continue to track upstream and contribute improvements through separate pull requests.

## Changes in this maintained version

Modified on **2026-10-31**, based on upstream commit `1ad0d4a93e505e77c79769d78ef2997641f973c8`:

- Optional `pro_image=True` for Nano Banana Pro image requests ([upstream PR #370](https://github.com/HanaokaYuzu/Gemini-API/pull/370)); the Pro selector approach also references [PR #304](https://github.com/HanaokaYuzu/Gemini-API/pull/304).
- Native `aspect_ratio` options ([upstream PR #371](https://github.com/HanaokaYuzu/Gemini-API/pull/371)): `1:1`, `9:16`, `16:9`, `3:4`, `4:3`, `2:3`, `21:9`, `1:8`, `8:1`.
- Integrated Pro and ratio options, with offline regression tests for independent options, combined requests, and per-turn option changes.

```python
chat = client.start_chat(model="gemini-pro")
response = await chat.send_message(
    "Generate a photo of a cat on a windowsill",
    pro_image=True,
    aspect_ratio="9:16",
)
```

An eligible signed-in account is required. The conversation model is distinct from the image model. Actual output dimensions are selected by Google and may approximate the requested ratio. A fresh-session combined request returned a 1536x2752 JPEG; the image-model label for that combined run has not been independently verified. Separate Pro-only generation was verified against the web UI label. Long-term session stability, resolution selection, output-format selection and native transparency are not established by these tests.

## Install this version from source

```bash
git clone https://github.com/LuckyXCoder/gemini-web-bridge.git
cd gemini-web-bridge
uv sync --no-dev
```

The Python import remains `gemini_webapi`. This repository has no separately published PyPI release; `pip install gemini-webapi` installs the upstream package, not these maintenance changes. The inherited badges and upstream installation examples below refer to the original project. Browser-cookie extraction is optional; the command above does not install its dependency.

## Optional local HTTP service

Install the optional server dependencies without browser-cookie extraction:

```bash
uv sync --no-dev --extra server
export GEMINI_BRIDGE_COOKIES=/absolute/path/to/cookies.json
export GEMINI_COOKIE_PATH=/absolute/path/to/private-cookie-cache
export GEMINI_BRIDGE_API_KEY='choose-a-private-service-key'
# Optional proxy for Google requests:
export GEMINI_PROXY=http://127.0.0.1:10808
uv run --no-dev --extra server python -m gemini_webapi.server
```

The service binds to `127.0.0.1:18088`. Each account must be signed in and available
to receive image tasks; accounts that fail initialization are paused. Cookie JSON may be a name/value object or `{ "cookies": { ... } }` and must
contain `__Secure-1PSID`. Keep the cookie file and cache private. The API key above
is a separate service credential; never send Google cookies to callers.

```bash
curl http://127.0.0.1:18088/v1/images/generations \
  -H "Authorization: Bearer $GEMINI_BRIDGE_API_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"model":"gemini-web-pro-image","prompt":"Generate a cat on a windowsill","aspect_ratio":"9:16","n":1,"response_format":"b64_json"}'
```

### Administrator API (optional)

Set a **different** administrator key to enable management. The generation API key
cannot access these endpoints. Use a private, writable accounts file; if it does not
exist, admin mode creates an empty one so the first account can be added dynamically.

```bash
unset GEMINI_BRIDGE_COOKIES
export GEMINI_BRIDGE_ACCOUNTS=/absolute/private/path/accounts.local.json
export GEMINI_BRIDGE_API_KEY='your-generation-service-key'
export GEMINI_BRIDGE_ADMIN_KEY='a-different-long-random-administrator-key'
uv run --no-dev --extra server python -m gemini_webapi.server
```

All management requests require `Authorization: Bearer <administrator-key>`:

| Method / path | Purpose |
| --- | --- |
| `GET /admin/api/accounts` | IDs, enabled flags, runtime state, last login-check time, last ten request outcomes, queue depth |
| `POST /admin/api/accounts` | Validate and add an account without restarting |
| `PUT /admin/api/accounts/{id}/cookies` | Validate replacement cookies and optionally update display_name atomically |
| `PATCH /admin/api/accounts/{id}` | Change display_name without replacing credentials or worker |
| `PUT /admin/api/accounts/{id}/enabled` | Enable/resume with login validation, or disable new work |
| `DELETE /admin/api/accounts/{id}` | Remove from configuration/pool after any active task finishes; retain credential files |

Add-account JSON (place actual credentials only in a private local file or protected
client, not shell history):

```json
{"display_name":"工作账号 user@example.com","secure_1psid":"COOKIE_VALUE","secure_1psidts":"COOKIE_VALUE"}
```

`display_name` supports Chinese, email-shaped names and other printable Unicode,
up to 100 characters. It is a display label, not a verified Google email or login
identifier. Internal IDs are stable ASCII strings: omitted IDs are generated by the
server; existing API clients can still supply the original `id` field. Changing a
name never renames cache/credential paths or changes account identity.

Cookie replacement takes both cookie values and optionally `display_name`; all
changes are applied only after successful validation. A name-only PATCH takes
`{"display_name":"新的账号名称"}` and performs no Google request. Enable/disable takes
`{"enabled":true}` or `{"enabled":false}`. Credentials are write-only and never returned. Validation does not
generate an image. A failed validation leaves existing configuration and clients intact.

Disabling a busy account immediately prevents new assignments while its current
request finishes. Updating cookies on a busy account returns 409; retry the update
after completion. Replacement uses a fresh cache directory to avoid stale-cookie
shadowing. Managed credential files and the accounts file are written with 0600
permissions using atomic file replacement. Old private credential/cache directories
are retained for recovery; they are not deleted automatically. Keep the configuration
parent directory private and persistent, and do not manually edit the accounts file
while management is running. Configuration is managed by one service process only.

Recent request records contain timestamp, elapsed seconds, success/failure and a
generic error code. They exclude prompts and secrets, retain only ten per account,
and are cleared on restart. Runtime state and the last check timestamp are not a
continuous guarantee that Google still accepts the session. Management endpoints
are not registered unless the administrator key is set. YAML editing and
service-key management are not included.

The service binds to loopback by default. `GEMINI_BRIDGE_HOST` and
`GEMINI_BRIDGE_PORT` can override the bind address/port for controlled deployments.
Keep management local or behind an SSH tunnel; do not expose plain HTTP credentials
on an untrusted network.

### Account status page (MVP)

Build the frontend once, then run the Python service with administrator mode enabled:

```bash
cd web
npm ci
npm run build
cd ..
uv run --no-dev --extra server python -m gemini_webapi.server
```

Open `http://127.0.0.1:18088/admin/` and enter the administrator key. The administrator key is kept in this tab's sessionStorage to survive refresh and is
validated again on page load. Logout or HTTP 401 clears it. It is not stored in
localStorage. Session storage is accessible to same-origin JavaScript, so keep this
local administration interface isolated from untrusted scripts.
For frontend development, `npm run dev` in `web/` proxies `/admin/api` to port 18088.
FastAPI serves the built `web/dist` on the same origin; Node.js is not needed at runtime.
`GEMINI_BRIDGE_WEB_DIR` can point to another frontend build directory.

The page shows account aliases, enabled/runtime state, last credential-check result,
last ten request outcomes and queue depth. Refresh is manual; there is no polling.
It supports individual credential checks, sequential batch checks with progress and
counts, adding accounts after login validation, editing display names or replacing
both cookie values, and deletion with confirmation.
Batch checks skip busy, checking or pending-removal accounts and never generate images.
Accounts added in the form are persisted and join the pool without restarting; Cookie
inputs are cleared on successful submission or dismissal and are never returned by the API.

Deleting an account first removes it from persisted configuration and stops new
assignments. An idle account disappears immediately; a busy account is marked pending
removal until its active task completes. Refresh the list to see that completion.
Cookie/cache files are retained on disk for recovery. No permanent file deletion occurs.
Existing Cookie values are never shown: leave both fields blank to change only the
name, or enter both values to replace credentials. Busy accounts allow name-only edits;
credential replacement must wait for the current task. There is no YAML editor or
image-generation UI.

`POST /admin/api/accounts/{id}/check` performs the manual login check. It uses a separate
client with a temporary copy of that account's cache, disables automatic cookie refresh
and browser-cookie extraction, then closes the probe and deletes its temporary files.
It does not generate images, change the account's enabled/runtime state, overwrite
credentials or update its persistent cache. Busy accounts and duplicate concurrent
checks return 409. Management updates for a checking account also return 409.
Results are `valid`, `invalid` (unauthenticated) or `unavailable` (network/configuration
failure), and represent only the check time. An invalid result does not automatically
pause or disable the worker: runtime state and login-check results are shown separately.

### Browser extension and JSON import

The local [Gemini Session Exporter](extensions/gemini-session-exporter/README.md)
provides a Manifest V3 popup for Chrome/Edge. It reads only `__Secure-1PSID` and
`__Secure-1PSIDTS` on an explicit click, using the active tab's cookie store. It tries
to detect the current email from visible account controls; always confirm it before
exporting. There is no background collector or network upload. A dedicated browser
profile/single-account session is required to avoid associating shared Google cookies
with the wrong email; non-default account routes are rejected by this MVP.

In **新增账号** or **编辑**, click **导入 JSON** and select the exported file or paste
its contents. JSON is parsed locally and only the account name and two credential
fields are copied into the form. IDs, file paths, API keys and enabled flags are never
imported. Confirm the form and save to perform server-side login validation. Both
extension exports and existing `{ "cookies": { ... } }`/flat Cookie JSON are supported.
The limit is 64KB per import. Imported/pasted JSON is cleared after filling or closing.
Files and clipboard contents contain login credentials and must remain private.

Extension logic tests (synthetic data only): `node --test tests/test_cookie_export_import.mjs`.
Actual Chrome installation, Cookie access and email detection require a manual browser
verification; mocked API tests are not proof of live Google compatibility.

### Multiple accounts

Use [accounts.example.json](accounts.example.json) as a template and store the real
configuration outside version control:

```bash
unset GEMINI_BRIDGE_COOKIES
export GEMINI_BRIDGE_ACCOUNTS=/absolute/path/to/accounts.local.json
uv run --no-dev --extra server python -m gemini_webapi.server
```

Paths in the accounts file are relative to that file, or can be absolute. Each enabled
entry needs a unique `id`, a `cookies_file`, and its own `cache_dir`. Set `enabled: false`
to exclude an entry. Manual file configuration is loaded at startup; after editing it, restart the service.
When the optional administrator API is enabled, use its endpoints for dynamic changes. A matching cache takes precedence over the
cookie file: for a deliberate account/session replacement, use a fresh private cache
directory so old cached credentials do not shadow the new session.

Duplicate IDs, repeated PSID credentials and shared cache directories are rejected.
Different browser sessions of the same Google account cannot be identified reliably
from these fields alone; provide distinct Google accounts, not multiple sessions of
one account. Each client has its own cookie jar, refresh task and explicit cache path;
the service never changes `GEMINI_COOKIE_PATH` between requests and never reads local
browser cookies. Cache directories are restricted to owner access and cache files
are written with owner read/write permissions. Protect the input cookie files as well.

Authenticated `GET /v1/accounts` returns only configured IDs and states (`idle`, `busy`,
`paused`); no cookies, emails or paths are exposed. Initialization failures, detected
authentication failures and quota errors pause that account until restart. Other
available accounts continue to process new requests. Runtime transport errors and
timeouts fail the current request without resubmission; the client is closed and may
be initialized for a later request. When no account remains available, new and queued
unsubmitted requests receive 503. A paused account is not automatically retried or
rotated back into the pool. Background session changes may only be detected at the
next request; this is not a continuous account-health or quota monitor.

Authenticated `GET /v1/models` lists `gemini-web-image` (default image mode) and
`gemini-web-pro-image` (Pro requested). These are service aliases, not official
Google model IDs. The chat model defaults to `gemini-pro` and can be set using
`GEMINI_BRIDGE_CHAT_MODEL`.

Responses follow the OpenAI image JSON shape: `created` and `data[].b64_json`.
The service downloads and validates a generated image before returning it; it does
not return web-search images or treat text-only replies as success. Bytes retain
their original format. Only `n=1`, `response_format="b64_json"`, `size="auto"`
(or omitted), and the documented native `aspect_ratio` values are supported.
Other fields, pixel sizes, edits, reference images, masks, and output format /
quality controls return HTTP 400; nothing is silently converted to a supported
size. This is a limited OpenAI-style endpoint, not full API compatibility.

Each available account has one worker and processes one request at a time. Workers
consume a shared queue, so different accounts can generate concurrently. The queue
holds up to four waiting requests in total; a full queue returns 429 without submission. The active request has a
300-second budget including initialization, generation and download; queue wait
is additional. Initialization failures before generation return 503. Generation timeouts return 504; upstream or download failures and text-only
results return 502. Errors use an `error` object and do not expose upstream
credentials or raw responses. Only the first generated image is returned.

The service bypasses the SDK generation retry decorator. It does not automatically
resubmit failed generations. A timeout or client disconnect does not prove that
Google stopped processing; the worker retains its slot until completion or timeout,
and disconnected results are not persisted. There is no task polling, idempotency
store or automatic failover in this MVP. Client/CPA retries can still create a new
generation and should be disabled for uncertain failures.

Offline checks (no Google credentials required):

```bash
uv sync --no-dev --extra server --extra dev
PYTHONPATH=src:tests uv run --no-dev --extra server --extra dev python -m unittest test_admin test_multi_account test_image_server test_pro_image test_image_aspect_ratio
```

Run only one service process (one Uvicorn worker) for a given account pool. Do not
share its accounts/cache with another running instance. The page provides minimal account management. This version has no weights or quota prediction; the optional
administrator API handles account addition and cookie replacement. Single-account
CPA v8.0.38 forwarding has been locally verified; the multi-account service keeps
the same image contract. Callers must use this supported parameter subset. Before exposing
a modified service to network users, provide access to its corresponding source
as required by AGPL section 13.

## License and attribution

This derivative is distributed under the **GNU Affero General Public License v3.0**; see the unchanged [LICENSE](LICENSE). Original copyright, license and attribution notices are retained, together with upstream Git history. Maintenance changes by LuckyXCoder are documented above and in Git history. For modified versions offered over a network, comply with the corresponding-source obligations in section 13. The software license does not grant access to Google services or override their terms.

---

## Inherited project documentation

The following documentation is retained from upstream, with image-option additions for this version.

<!-- markdownlint-disable MD033 MD041 -->

<p align="center">
    <img src="https://raw.githubusercontent.com/HanaokaYuzu/Gemini-API/master/assets/banner.png" width="55%" alt="Gemini Banner" align="center">
</p>
<p align="center">
    <a href="https://pypi.org/project/gemini-webapi">
        <img src="https://img.shields.io/pypi/v/gemini-webapi" alt="PyPI"></a>
    <a href="https://pepy.tech/project/gemini-webapi">
        <img src="https://static.pepy.tech/badge/gemini-webapi" alt="Downloads"></a>
    <a href="https://github.com/HanaokaYuzu/Gemini-API/network/dependencies">
        <img src="https://img.shields.io/librariesio/github/HanaokaYuzu/Gemini-API" alt="Dependencies"></a>
    <a href="https://github.com/HanaokaYuzu/Gemini-API/blob/master/LICENSE">
        <img src="https://img.shields.io/github/license/HanaokaYuzu/Gemini-API" alt="License"></a>
    <a href="https://github.com/psf/black">
        <img src="https://img.shields.io/badge/code%20style-black-000000.svg" alt="Code style"></a>
</p>
<p align="center">
    <a href="https://star-history.com/#HanaokaYuzu/Gemini-API">
        <img src="https://img.shields.io/github/stars/HanaokaYuzu/Gemini-API?style=social" alt="GitHub stars"></a>
    <a href="https://github.com/HanaokaYuzu/Gemini-API/issues">
        <img src="https://img.shields.io/github/issues/HanaokaYuzu/Gemini-API?style=social&logo=github" alt="GitHub issues"></a>
    <a href="https://github.com/HanaokaYuzu/Gemini-API/actions/workflows/pypi-publish.yml">
        <img src="https://github.com/HanaokaYuzu/Gemini-API/actions/workflows/pypi-publish.yml/badge.svg" alt="CI"></a>
</p>

# <img src="https://raw.githubusercontent.com/HanaokaYuzu/Gemini-API/master/assets/logo.svg" width="35px" alt="Gemini Icon" /> Gemini-API

A reverse-engineered asynchronous Python wrapper for the [Google Gemini](https://gemini.google.com) web app (formerly Bard).

## Features

- **Persistent Cookies** - Automatically refreshes cookies in background. Optimized for always-on services.
- **Image Generation** - Natively supports generating and editing images with natural language.
- **Video & Audio Generation** - Supports generating videos and audio/music content natively.
- **Deep Research** - Full deep research workflow with plan creation, status polling, and result retrieval.
- **System Prompt** - Supports customizing the model's system prompt with [Gemini Gems](https://gemini.google.com/gems/view).
- **Extension Support** - Supports generating content with [Gemini extensions](https://gemini.google.com/extensions), such as YouTube and Gmail.
- **Classified Outputs** - Categorizes text, thoughts, images, videos, and audio in the response.
- **Streaming Mode** - Supports stream generation, yielding partial outputs as they are generated.
- **CLI Tool** - Standalone command-line interface for quick interactions.
- **Official Flavor** - Provides a simple and elegant interface inspired by [Google Generative AI](https://ai.google.dev/tutorials/python_quickstart)'s official API.
- **Asynchronous** - Utilizes `asyncio` to run generation tasks and return outputs efficiently.

## Table of Contents

- [Features](#features)
- [Table of Contents](#table-of-contents)
- [Installation](#installation)
- [Authentication](#authentication)
- [Usage](#usage)
  - [Initialization](#initialization)
  - [Generate Content](#generate-content)
  - [Generate Content with Files](#generate-content-with-files)
  - [Conversations Across Multiple Turns](#conversations-across-multiple-turns)
  - [Continue Previous Conversations](#continue-previous-conversations)
  - [Read Conversation History](#read-conversation-history)
  - [Delete Previous Conversations from Gemini History](#delete-previous-conversations-from-gemini-history)
  - [Temporary Mode](#temporary-mode)
  - [Streaming Mode](#streaming-mode)
  - [Select Language Model](#select-language-model)
  - [List Available Models](#list-available-models)
  - [Apply System Prompt with Gemini Gems](#apply-system-prompt-with-gemini-gems)
  - [Manage Custom Gems](#manage-custom-gems)
    - [Create a Custom Gem](#create-a-custom-gem)
    - [Update an Existing Gem](#update-an-existing-gem)
    - [Delete a Custom Gem](#delete-a-custom-gem)
  - [Retrieve Model's Thought Process](#retrieve-models-thought-process)
  - [Retrieve Images in Response](#retrieve-images-in-response)
  - [Generate and Edit Images](#generate-and-edit-images)
  - [Retrieve Videos and Audio](#retrieve-videos-and-audio)
  - [Generate Content with Gemini Extensions](#generate-content-with-gemini-extensions)
  - [Check and Switch to Other Reply Candidates](#check-and-switch-to-other-reply-candidates)
  - [Deep Research](#deep-research)
  - [Logging Configuration](#logging-configuration)
- [CLI Tool](#cli-tool)
  - [Cookie Setup](#cookie-setup)
  - [CLI Commands](#cli-commands)
  - [Deep Research Workflow](#deep-research-workflow)
- [References](#references)
- [Stargazers](#stargazers)

## Installation

> [!NOTE]
>
> This package requires Python 3.11 or higher.

Install or update the package with pip.

```sh
pip install -U gemini_webapi
```

Optionally, the package offers a way to automatically import cookies from your local browser via optional dependency `browser-cookie3`. To enable this feature, install `gemini_webapi[browser]` instead. Currently, only Firefox is supported. The latest information about supported browsers can be found in the [browser-cookie3](https://github.com/borisbabic/browser_cookie3) repository.

```sh
pip install -U gemini_webapi[browser]
```

## Authentication

> [!TIP]
>
> If `browser-cookie3` is installed, you can skip this step and go directly to the [usage](#usage) section. Just make sure you are logged in to <https://gemini.google.com> in your browser.

- Go to <https://gemini.google.com> and log in with your Google account
- Press F12 to open the web inspector, go to the `Network` tab, and refresh the page
- Click any request and copy the cookie values of `__Secure-1PSID` and `__Secure-1PSIDTS`

> [!NOTE]
>
> If your application is deployed in a containerized environment (e.g. Docker), you may want to persist the cookies with a volume to avoid re-authentication every time the container rebuilds. You can set `GEMINI_COOKIE_PATH` environment variable to specify the path where auto-refreshed cookies are stored. Make sure the path is writable by the application.
>
> Here's part of a sample `docker-compose.yml` file:

```yaml
services:
  main:
    environment:
      GEMINI_COOKIE_PATH: /tmp/gemini_webapi
    volumes:
      - ./gemini_cookies:/tmp/gemini_webapi
```

> [!NOTE]
>
> The API's auto-cookie-refreshing feature doesn't require `browser-cookie3` and is enabled by default. It allows you to keep the API service running without worrying about cookie expiration.
>
> This feature may require you to log in to your Google account again in the browser. This is expected behavior and won't affect the API's functionality.
>
> To avoid this, it's recommended to get cookies from a separate browser session and close it as soon as possible for best utilization (e.g. a fresh login in the browser's private mode). More details can be found [in issue #6](https://github.com/HanaokaYuzu/Gemini-API/issues/6).

<!-- markdownlint-disable-line MD028 -->

> [!TIP]
>
> If cookies expire frequently, use Firefox to extract cookies. Recent versions of Chromium-based browsers use "Device Bound Session Credentials", which improves security but causes cookies to remain valid for only a few hours and prevents them from being renewed.

## Usage

### Initialization

Import the required packages and initialize a client with your cookies from the previous step. After successful initialization, the API will automatically refresh `__Secure-1PSIDTS` in the background as long as the process is alive.

```python
import asyncio
from gemini_webapi import GeminiClient

# Replace "COOKIE VALUE HERE" with your actual cookie values.
# Leave Secure_1PSIDTS empty if it's not available for your account.
Secure_1PSID = "COOKIE VALUE HERE"
Secure_1PSIDTS = "COOKIE VALUE HERE"


async def main():
    # If browser-cookie3 is installed, simply use `client = GeminiClient()`
    client = GeminiClient(Secure_1PSID, Secure_1PSIDTS, proxy=None)
    await client.init(timeout=30, auto_close=False, close_delay=300, auto_refresh=True)


asyncio.run(main())
```

> [!TIP]
>
> `auto_close` and `close_delay` are optional arguments for automatically closing the client after a certain period of inactivity. This feature is disabled by default. In an always-on service like a chatbot, it's recommended to set `auto_close` to `True` with a reasonable `close_delay` value for better resource management.

### Generate Content

Ask a single-turn question by calling `GeminiClient.generate_content`, which returns a `gemini_webapi.ModelOutput` object containing the generated text, images, thoughts, and conversation metadata.

```python
async def main():
    response = await client.generate_content("Hello World!")
    print(response.text)


asyncio.run(main())
```

> [!TIP]
>
> Simply use `print(response)` to get the same output if you just want to see the response text.

### Generate Content with Files

Gemini supports file input, including images and documents. Optionally, you can pass files as a list of paths in `str` or `pathlib.Path` to `GeminiClient.generate_content` together with a text prompt.

```python
async def main():
    response = await client.generate_content(
        "Introduce the contents of these two files. Is there any connection between them?",
        files=["assets/sample.pdf", Path("assets/banner.png")],
    )
    print(response.text)


asyncio.run(main())
```

### Conversations Across Multiple Turns

If you want to keep the conversation continuous, use `GeminiClient.start_chat` to create a `gemini_webapi.ChatSession` object and send messages through it. The conversation history will be handled automatically and updated after each turn.

```python
async def main():
    chat = client.start_chat()
    response1 = await chat.send_message(
        "Introduce the contents of these two files. Is there any connection between them?",
        files=["assets/sample.pdf", Path("assets/banner.png")],
    )
    print(response1.text)
    response2 = await chat.send_message(
        "Use image generation tool to modify the banner with another font and design."
    )
    print(response2.text, response2.images, sep="\n\n----------------------------------\n\n")


asyncio.run(main())
```

> [!TIP]
>
> Same as `GeminiClient.generate_content`, `ChatSession.send_message` also accepts `image` as an optional argument.

### Continue Previous Conversations

To manually retrieve previous conversations, you can pass a previous `ChatSession`'s metadata to `GeminiClient.start_chat` when creating a new `ChatSession`. Alternatively, you can persist previous metadata to a file or database if you need to access it after the current Python process has exited.

```python
async def main():
    # Start a new chat session
    chat = client.start_chat()
    response = await chat.send_message("Fine weather today")

    # Save chat's metadata
    previous_session = chat.metadata

    # Load the previous conversation
    previous_chat = client.start_chat(metadata=previous_session)
    response = await previous_chat.send_message("What was my previous message?")
    print(response)


asyncio.run(main())
```

### Read Conversation History

You can read the conversation history of a specific chat by calling `GeminiClient.read_chat` with the chat ID. It returns a `ChatHistory` object containing a list of `ChatTurn` objects ordered from newest to oldest.

```python
async def main():
    chat = client.start_chat()
    await chat.send_message("What is the capital of France?")

    # Read the chat history
    history = await client.read_chat(chat.cid)
    if history:
        for turn in history.turns:
            print(f"[{turn.role.upper()}] {turn.text}")
            print("\n----------------------------------\n")


asyncio.run(main())
```

To list all recent chats, use `GeminiClient.list_chats`:

```python
async def main():
    chats = client.list_chats()
    if chats:
        for chat_info in chats:
            print(f"{chat_info.cid}: {chat_info.title}")


asyncio.run(main())
```

### Delete Previous Conversations from Gemini History

You can delete a specific chat from Gemini history on the server by calling `GeminiClient.delete_chat` with the chat ID.

```python
async def main():
    # Start a new chat session
    chat = client.start_chat()
    await chat.send_message("This is a temporary conversation.")

    # Delete the chat
    await client.delete_chat(chat.cid)
    print(f"Chat deleted: {chat.cid}")


asyncio.run(main())
```

### Temporary Mode

You can start a temporary chat by passing `temporary=True` to `GeminiClient.generate_content` or `ChatSession.send_message`. Temporary chats won't be saved in Gemini history.

```python
async def main():
    response = await client.generate_content("Hello World!", temporary=True)
    print(response.text, "\n\n----------------------------------\n\n")

    chat = client.start_chat()
    await chat.send_message("Fine weather today", temporary=False)
    response2 = await chat.send_message("What's my last message?", temporary=True)
    print(response2.text)


asyncio.run(main())
```

### Streaming Mode

For longer responses, you can use streaming mode to receive partial outputs as they are generated. This provides a more responsive user experience, especially for real-time applications like chatbots.

The `generate_content_stream` method yields `ModelOutput` objects where the `text_delta` attribute contains only the **new characters** received since the last yield, making it easy to display incremental updates.

```python
async def main():
    async for chunk in client.generate_content_stream(
        "What's the difference between 'await' and 'async for'?"
    ):
        print(chunk.text_delta, end="", flush=True)

    print()


asyncio.run(main())
```

> [!TIP]
>
> Streaming mode accepts the same arguments as `generate_content`. You can also use streaming mode in multi-turn conversations with `ChatSession.send_message_stream`.

### Select Language Model

You can specify which language model to use by passing the `model` argument to `GeminiClient.generate_content` or `GeminiClient.start_chat`. Omit it, or pass `None`, to let Google use your account's default model.

Models are discovered **dynamically** at init time from your account, along with the request headers and tier capacity each one needs. Pass a name, alias, display name or id string - or an `AvailableModel` straight from `list_models()`. Names follow whatever Google publishes to your account (currently `gemini-pro`, `gemini-flash` and `gemini-flash-lite`), so run the snippet below to see your own list rather than hardcoding one.

```python
async def main():
    for model in client.list_models() or []:
        print(model.model_name, "|", model.display_name, "| available:", model.is_available)

    response1 = await client.generate_content(
        "What's your language model version? Reply with the version number only.",
        model="gemini-flash",  # a name, alias, display name or id
    )
    print(f"Model version (gemini-flash): {response1.text}")

    chat = client.start_chat(model=client.resolve_model("pro"))
    response2 = await chat.send_message(
        "What's your language model version? Reply with the version number only."
    )
    print(f"Model version ({chat.model}): {response2.text}")


asyncio.run(main())
```

> [!WARNING]
>
> The `Model` enum is **deprecated and pending removal**. Its ids and headers are hardcoded, and its `PLUS_`/`ADVANCED_` variants ask you to know your own account tier - a value the client now reads from the account itself, which is why nine members collapse into six actual models.
>
> `Model.from_name` and `Model.from_dict` have been **removed**; use `client.resolve_model(name)` and `AvailableModel.from_dict` instead. Passing a member to `generate_content` still works for now: it is mapped onto your account's equivalent model, or to the default model if your account has no counterpart, and logs a warning. Names the enum alone knew - `gemini-pro-advanced` and friends - no longer resolve at all; ask for `gemini-pro` and the tier is filled in for you.

You can also pass custom model header strings directly to access models that are not listed above.

```python
# "model_name" and "model_header" keys must be present
custom_model = {
    "model_name": "xxx",
    "model_header": {
        "x-goog-ext-525001261-jspb": '[1,null,null,null,"fbb127bbb056c959",null,null,0,[4,5,6,8],null,null,1,null,null,1,1,"EA3C5672-E422-4A5F-BE26-B5B57D3B9AC3"]',
        "x-goog-ext-73010989-jspb": "[0]",
        "x-goog-ext-73010990-jspb": "[0,0,0]",
    },
}

response = await client.generate_content("What's your model version?", model=custom_model)
```

> [!NOTE]
>
> A dictionary is read into an `AvailableModel`: the model id is taken from the header you supply and the header is then rebuilt, so `capacity` and `model_number` come from the dictionary (or its defaults) rather than from other slots of the string. Prefer a model name; reach for this only for a model discovery does not list yet.

### List Available Models

The client dynamically discovers which models are available for your account at initialization. Use `GeminiClient.list_models` to see all available models and their details.

```python
async def main():
    await client.init()  # Make sure the client is initialized first
    models = client.list_models()
    if models:
        for model in models:
            print(f"{model.display_name}: {model.model_name}")


asyncio.run(main())
```

### Apply System Prompt with Gemini Gems

System prompts can be applied to conversations via [Gemini Gems](https://gemini.google.com/gems/view). To use a gem, you can pass the `gem` argument to `GeminiClient.generate_content` or `GeminiClient.start_chat`. `gem` can be either a gem ID string or a `gemini_webapi.Gem` object. Only one gem can be applied to a single conversation.

> [!TIP]
>
> There are some system predefined gems that are not shown to users by default (and therefore may not work properly). Use `client.fetch_gems(include_hidden=True)` to include them in the fetch result.

```python
async def main():
    # Fetch all gems for the current account, including both predefined and user-created ones
    await client.fetch_gems(include_hidden=False)

    # Once fetched, gems will be cached in `GeminiClient.gems`
    gems = client.gems

    # Get the gem you want to use
    system_gems = gems.filter(predefined=True)
    coding_partner = system_gems.get(id="coding-partner")

    response1 = await client.generate_content(
        "What's your system prompt?",
        gem=coding_partner,
    )
    print(response1.text)

    # Another example with a user-created custom gem
    # Gem ids are consistent strings. Store them somewhere to avoid fetching gems every time
    your_gem = gems.get(name="Your Gem Name")
    your_gem_id = your_gem.id
    chat = client.start_chat(gem=your_gem_id)
    response2 = await chat.send_message("What's your system prompt?")
    print(response2)
```

### Manage Custom Gems

You can create, update, and delete your custom gems programmatically with the API. Note that predefined system gems cannot be modified or deleted.

#### Create a Custom Gem

Create a new custom gem with a name, system prompt (instructions), and optional description:

```python
async def main():
    # Create a new custom gem
    new_gem = await client.create_gem(
        name="Python Tutor",
        prompt="You are a helpful Python programming tutor.",
        description="A specialized gem for Python programming",
    )

    print(f"Custom gem created: {new_gem}")

    # Use the newly created gem in a conversation
    response = await client.generate_content(
        "Explain how list comprehensions work in Python", gem=new_gem
    )
    print(response.text)


asyncio.run(main())
```

#### Update an Existing Gem

> [!NOTE]
>
> When updating a gem, you must provide all parameters (name, prompt, description) even if you only want to change one of them.

```python
async def main():
    # Get a custom gem (assuming you have one named "Python Tutor")
    await client.fetch_gems()
    python_tutor = client.gems.get(name="Python Tutor")

    # Update the gem with new instructions
    updated_gem = await client.update_gem(
        gem=python_tutor,  # Can also pass gem ID string
        name="Advanced Python Tutor",
        prompt="You are an expert Python programming tutor.",
        description="An advanced Python programming assistant",
    )

    print(f"Custom gem updated: {updated_gem}")


asyncio.run(main())
```

#### Delete a Custom Gem

```python
async def main():
    # Get the gem to delete
    await client.fetch_gems()
    gem_to_delete = client.gems.get(name="Advanced Python Tutor")

    # Delete the gem
    await client.delete_gem(gem_to_delete)  # Can also pass gem ID string
    print(f"Custom gem deleted: {gem_to_delete.name}")


asyncio.run(main())
```

### Retrieve Model's Thought Process

When using models with thinking capabilities, the model's thought process will be populated in `ModelOutput.thoughts`.

```python
async def main():
    response = await client.generate_content("What's 1+1?", model="gemini-pro")
    print(response.thoughts)
    print(response.text)


asyncio.run(main())
```

### Retrieve Images in Response

Images in the API's output are stored as a list of `gemini_webapi.Image` objects. You can access the image title, URL, and description by calling `Image.title`, `Image.url` and `Image.alt` respectively.

```python
async def main():
    response = await client.generate_content("Send me some pictures of cats")
    for image in response.images:
        print(image, "\n\n----------------------------------\n\n")


asyncio.run(main())
```

### Generate and Edit Images

You can ask Gemini to generate and edit images with Nano Banana, Google's latest image model, using natural language.

To request a native image aspect ratio, pass `aspect_ratio` to `generate_content`,
`generate_content_stream`, `ChatSession.send_message`, or `ChatSession.send_message_stream`:

```python
response = await client.generate_content(
    "Generate a photo of a cat sitting on a windowsill",
    model="gemini-pro",
    aspect_ratio="9:16",
)
```

On this integration branch, the same request can also select Pro image generation:

```python
chat = client.start_chat(model="gemini-pro")
response = await chat.send_message(
    "Generate a photo of a cat on a windowsill",
    pro_image=True,
    aspect_ratio="9:16",
)
```

Both options apply to the current call, not all later turns. Omit `aspect_ratio`
to leave the ratio unspecified; omit `pro_image` to use the default image mode.
The account must have access to the requested image model.

Supported values are `"1:1"`, `"9:16"`, `"16:9"`, `"3:4"`, `"4:3"`, `"2:3"`,
`"21:9"`, `"1:8"`, and `"8:1"`. These include the web menu options and additional
ratios verified through native web requests. Arbitrary ratios such as `"7:13"`
are not supported. This selects image generation and requires a signed-in account with image access.
Omit the argument (or pass `None`) to preserve the existing generation behavior.
The ratio is sent as a native option without modifying the prompt or resizing the result;
Google chooses the actual pixel dimensions, which may only approximate the requested ratio.
This option cannot be combined with `deep_research=True` and does not specify resolution
or output format.


> [!IMPORTANT]
>
> Google has some limitations on Gemini's image generation feature, so availability may vary by region/account. Here's a summary copied from [official documentation](https://support.google.com/gemini/answer/14286560) (as of Sep 10, 2025):
>
> > This feature's availability in any specific Gemini app is also limited to the supported languages and countries of that app.
> >
> > For now, this feature isn't available to users under 18.
> >
> > To use this feature, you must be signed in to Gemini Apps.

You can save images returned from Gemini locally by calling `Image.save()`. Optionally, you can specify the file path and file name by passing `path` and `filename` arguments to the function. This works for both `WebImage` and `GeneratedImage`.

```python
async def main():
    response = await client.generate_content("Generate some pictures of cats")
    for i, image in enumerate(response.images):
        await image.save(path="temp/", filename=f"cat_{i}.png", verbose=True)
        print(image, "\n\n----------------------------------\n\n")


asyncio.run(main())
```

> [!NOTE]
>
> By default, when asked to send images (like in the previous example), Gemini will send images fetched from the web instead of generating images with an AI model, unless you specifically ask it to "generate" images in your prompt. In this package, web images and generated images are treated differently as `WebImage` and `GeneratedImage`, and are automatically categorized in the output.

### Retrieve Videos and Audio

Gemini can generate videos and audio/music content. These are returned as `GeneratedVideo` and `GeneratedMedia` objects in `ModelOutput.videos` and `ModelOutput.media` respectively. You can save them to disk just like images.

> [!NOTE]
>
> You may need an active subscription to access Gemini's video and audio generation features.

```python
async def main():
    response = await client.generate_content("Generate a short video of a cat playing")

    # Save generated videos
    for video in response.videos:
        result = await video.save(path="temp/", verbose=True)
        print(f"Video saved: {result}")

    # Save generated media (audio/music)
    for media in response.media:
        result = await media.save(path="temp/", verbose=True)
        print(f"Media saved: {result}")


asyncio.run(main())
```

> [!NOTE]
>
> `GeneratedMedia.save()` accepts a `download_type` parameter: `"audio"`, `"video"`, or `"both"` (default). Generated video/audio may take time to render — the save method will poll automatically until the content is ready.

### Generate Content with Gemini Extensions

> [!IMPORTANT]
>
> To access Gemini extensions in the API, you must activate them on the [Gemini website](https://gemini.google.com/extensions) first. As with image generation, Google also has limitations on the availability of Gemini extensions. Here's a summary copied from [official documentation](https://support.google.com/gemini/answer/13695044) (as of March 19, 2025):
>
> > To connect apps to Gemini, you must have​​​​ Gemini Apps Activity on.
> >
> > To use this feature, you must be signed in to Gemini Apps.
> >
> > Important: If you're under 18, Google Workspace and Maps apps currently only work with English prompts in Gemini.

After activating extensions for your account, you can access them in your prompts either in natural language or by starting your prompt with "@" followed by the extension keyword.

```python
async def main():
    response1 = await client.generate_content("@Gmail What's the latest message in my mailbox?")
    print(response1, "\n\n----------------------------------\n\n")

    response2 = await client.generate_content(
        "@Youtube What's the latest activity of Taylor Swift?"
    )
    print(response2, "\n\n----------------------------------\n\n")


asyncio.run(main())
```

> [!NOTE]
>
> For region availability, your Google account's **preferred language** only needs to be set to one of the three supported languages listed above. You can change your language settings [in your Google account settings](https://myaccount.google.com/language).

### Check and Switch to Other Reply Candidates

A response from Gemini sometimes contains multiple reply candidates with different generated content. You can check all candidates and choose one to continue the conversation. By default, the first candidate is chosen.

```python
async def main():
    # Start a conversation and list all reply candidates
    chat = client.start_chat()
    response = await chat.send_message("Recommend a science fiction book for me.")
    for candidate in response.candidates:
        print(candidate, "\n\n----------------------------------\n\n")

    if len(response.candidates) > 1:
        # Control the ongoing conversation flow by choosing candidate manually
        new_candidate = chat.choose_candidate(index=1)  # Choose the second candidate here
        followup_response = await chat.send_message(
            "Tell me more about it."
        )  # Will generate content based on the chosen candidate
        print(new_candidate, followup_response, sep="\n\n----------------------------------\n\n")
    else:
        print("Only one candidate available.")


asyncio.run(main())
```

### Deep Research

Gemini's deep research feature is an autonomous research agent that browses the web, analyzes sources, and produces a comprehensive report. You can access it programmatically through the API.

Under the hood this is an ordinary chat conversation: Gemini answers the first turn with a **plan**, and a second turn **confirms** it and starts the research. The task then runs server-side, detached from the session, so polling for the report does not require holding the conversation open.

The finished report is delivered as an **inline document**, not as reply text — the reply itself is only a short notice such as _"I've completed your research"_. `result.text` returns the report, and `result.document` exposes it with its id and title:

```python
result = await client.deep_research("...")
doc = result.document
print(doc.title)  # 'SQLite vs DuckDB for Analytics'
print(doc.content)  # markdown report: headings, tables, links, [cite: N] markers
print(doc.sources)  # the web sources those [cite: N] markers resolve to

Path("report.md").write_text(doc.markdown, encoding="utf-8")  # body + numbered source list
```

The same document is available on any deep research turn as `output.deep_research_document`, so a report can be recovered later from the chat alone:

```python
output = await client.fetch_latest_chat_response("c_abc123")
if (doc := output.deep_research_document) and doc.ready:
    print(doc.content)
```

> [!NOTE]
>
> You may need an active subscription to access Gemini's deep research feature.

**Quick one-call method:**

```python
async def main():
    result = await client.deep_research(
        "Compare the top 3 cloud providers and their AI offerings",
        poll_interval=10.0,
        timeout=600.0,
    )
    print(f"Done: {result.done}")
    print(result.text)


asyncio.run(main())
```

Both turns run in a single chat session. Pass `model` to choose one, or `chat` to supply your own session and keep a reference to the conversation after the call returns.

> [!TIP]
>
> Reports commonly take 5-10 minutes, and longer for broad topics. Raise `timeout` past the 600 second default if `result.done` comes back `False`.

**Step-by-step workflow** for more control, including reviewing the plan before committing to it:

```python
async def main():
    # Plan and confirmation are two turns of one conversation, so share a chat session
    chat = client.start_chat()

    # Step 1: Create a research plan
    plan = await client.create_deep_research_plan(
        "What are the latest advancements in quantum computing?",
        chat=chat,
    )
    print(f"Title: {plan.title}")
    print(f"ETA: {plan.eta_text}")
    for step in plan.steps:
        print(f"  - {step}")

    # Step 2 (optional): Ask for changes on the same session to get a revised plan back
    # plan = await client.create_deep_research_plan("Focus on error correction", chat=chat)

    # Step 3: Confirm the plan to start the research
    await client.start_deep_research(plan, chat=chat)

    # Step 4: Poll for completion
    result = await client.wait_for_deep_research(
        plan,
        poll_interval=10.0,
        timeout=600.0,
    )

    print(result.text)


asyncio.run(main())
```

### Logging Configuration

This package uses [loguru](https://loguru.readthedocs.io/en/stable/) for logging and exposes a function `set_log_level` to control the log level. You can set the log level to one of the following values: `DEBUG`, `INFO`, `WARNING`, `ERROR`, and `CRITICAL`. The default value is `INFO`.

```python
from gemini_webapi import set_log_level

set_log_level("DEBUG")
```

> [!NOTE]
>
> Calling `set_log_level` for the first time will **globally** remove all existing loguru handlers. You may want to configure logging directly with loguru to avoid this issue and have more advanced control over logging behaviors.

## CLI Tool

A standalone CLI (`cli.py`) is included for interacting with Gemini from the terminal. It supports single-turn questions, multi-turn chat, deep research, image download, and account diagnostics.

### Cookie Setup

Export your cookies from [gemini.google.com](https://gemini.google.com) and save them as a JSON file. The CLI supports multiple formats:

```json
{ "__Secure-1PSID": "value...", "__Secure-1PSIDTS": "value..." }
```

You can also use a browser cookie extension export (array-of-objects format is supported).

> [!NOTE]
>
> The CLI automatically persists updated cookies back to the JSON file after each run. Use `--no-persist` to disable this behavior.

### CLI Commands

**Global options** (placed before the subcommand):

```sh
--cookies-json PATH    Path to cookies JSON file (required)
--proxy URL            Proxy URL (or uses HTTPS_PROXY env)
--model NAME           Model name, alias or id (default: your account's default model)
--verbose              Enable debug logging
--no-persist           Don't update cookies file after run
--request-timeout SEC  HTTP timeout in seconds (default: 300)
```

**Available commands:**

```sh
# Ask a single question (streams by default)
python cli.py --cookies-json cookies.json ask "What is quantum computing?"

# Ask with image input
python cli.py --cookies-json cookies.json ask --image photo.jpg "Describe this"

# Non-streaming mode
python cli.py --cookies-json cookies.json ask --no-stream "Hello"

# Continue a conversation (chat ID from previous output)
python cli.py --cookies-json cookies.json reply c_abc123 "Tell me more"

# List your chat history
python cli.py --cookies-json cookies.json list

# Read a specific chat conversation
python cli.py --cookies-json cookies.json read c_abc123

# List the models your account can use
python cli.py --cookies-json cookies.json models

# Download a generated image
python cli.py --cookies-json cookies.json download "https://..." -o output.png

# Account diagnostics (status, quotas, usage limits, available models)
python cli.py --cookies-json cookies.json inspect
```

### Deep Research Workflow

The CLI supports Gemini's Deep Research feature — an autonomous research agent that browses the web, analyzes sources, and produces a comprehensive report.

```sh
# 1. Submit a research task. Prints both a chat ID and a research (task) ID
python cli.py --cookies-json cookies.json research send --prompt "AI chip competition 2025"

# 2. Check progress. Pass --research-id for the actual task state - without it the chat
#    cannot be told apart from the plan and confirmation replies, which arrive immediately
python cli.py --cookies-json cookies.json research check c_abc123 --research-id 0f3d...

# 3. Fetch the full result
python cli.py --cookies-json cookies.json research get c_abc123

# 4. Save result to a file
python cli.py --cookies-json cookies.json research get c_abc123 --output report.md
```

## References

[Google AI Studio](https://ai.google.dev/tutorials/ai-studio_quickstart)

[acheong08/Bard](https://github.com/acheong08/Bard)

## Stargazers

<p align="center">
    <a href="https://star-history.com/#HanaokaYuzu/Gemini-API">
        <img src="https://api.star-history.com/svg?repos=HanaokaYuzu/Gemini-API&type=Date" width="75%" alt="Star History Chart"></a>
</p>

### 指定账号生图验证

管理页的“验证账号状态”只检查登录；“测试生图”使用选中账号生成一张
1:1 测试图，可使用默认橘猫提示词或自定义提示词，可选普通或 Pro 模式，会消耗生图额度。
管理接口：`POST /admin/api/accounts/{id}/image-test`，
请求体 `{"model":"gemini-web-image"}` 或 `{"model":"gemini-web-pro-image"}`，
使用独立管理员 Bearer Key。仅空闲、已启用账号可测试；忙碌、验证中、
暂停、禁用或待删除账号返回 409。

测试只提交一次，不切换账号。浏览器断开后服务仍保持该账号占用直到结束或
超时，避免重复生成。返回 `image_test` 结果及成功时的 `image` 生图响应；
列表仅保留最近一次测试的模式、状态、时间、耗时和错误分类，不保存图片。
记录在服务重启或替换账号凭证后清空。Pro 表示请求模式，不代表额外核验
Google 网页中的模型标签。

生图失败会暂停该账号以避免继续接单，但仅明确认证失败才修改登录状态。
超时无法保证上游未执行，不应立即重复提交。暂停账号可通过现有启用接口
重新验证恢复，或更新凭证；批量验证不会提交生图。

生图验证请求可传入可选的 `prompt`（去除首尾空白后非空，最多 16000 字符）；省略时使用默认提示词。最近测试记录包含本次模型和提示词，仅保存在内存中。

### 脱敏诊断日志

服务默认向终端输出 JSON 诊断事件。设置 `GEMINI_BRIDGE_LOG_DIR` 可同时写入
该目录的 `diagnostics.jsonl`，每个文件最多 5 MiB，保留 3 个轮转备份，
文件权限为 0600。时间使用 UTC（北京时间加 8 小时）。

事件通过 `account_id` 和 `request_id` 关联；每次续期具有独立请求 ID。
记录续期开始/跳过原因、HTTP 状态、是否收到 PSIDTS、值是否变化、
缓存写入结果、续期停止及客户端关闭原因。生图记录 generation、
image_selection、download、image_validation 阶段和异常类型。
管理端最近生图结果包含 request_id，便于对应日志。
诊断日志不包含 Cookie 值、密钥、请求头、提示词、响应正文或原始异常消息；
SDK 既有日志不等同于这份独立诊断日志，请勿直接上传完整调试输出。

`renewal_finished` 只表示轮换函数返回，不表示已独立验证登录；
`renewal_skipped` 表示未实际发起轮换请求。缓存写入成功也不保证登录有效。
本次仅增加可观察性，不改变暂停、续期、重试或认证分类策略。

### 批量迁移账号

管理页点击“导出全部账号”下载一个 JSON；在目标 Bridge 点击“批量导入”，
选择该文件并点击“验证并导入”。导出只包含名称、启用状态和两个必要 Cookie，
优先使用正在运行的会话值，其次续期缓存，最后原始凭证；不包含本机路径、
管理员/服务 Key、其他 Cookie、日志或图片。

导入逐个验证，成功项立即保存，重复凭证跳过，失败项显示在结果列表，
不覆盖已有账号。目标端生成新的账号 ID、私有凭证文件和独立缓存目录。
同一个邮箱的不同会话不能仅凭显示名称自动去重。单文件最多 100 个账号、
前端上限 2 MiB；导入不会生图。网络中断时先刷新目标列表确认结果，重新导入
会跳过相同凭证。已失效的账号不会导入，需更新凭证后再迁移。

两个接口都只接受独立管理员 Bearer Key：
- POST /admin/api/accounts/export：返回带 no-store 的凭证 JSON；
  有账号忙碌、正在验证或待删除时返回 409，避免迁移过程冲突。
- POST /admin/api/accounts/import：接受相同 JSON 结构，返回逐项
  imported / skipped / failed；页面每次发送一项，以显示进度并限制等待时间。

导出文件包含可用于登录的凭证，不提交 Git。迁移后应停止源端服务，
避免本地与云端同时维护同一会话；云端仍需独立验证登录和生图。

### YAML 调度配置

复制仓库中的 `bridge.example.yaml` 到自己的配置文件：

```yaml
scheduler:
  queue_capacity: 4
  execution_timeout_seconds: 300
```

通过环境变量指定文件后启动：

```bash
export GEMINI_BRIDGE_CONFIG=/absolute/path/bridge.yaml
python -m gemini_webapi.server
```

两个字段必须是正整数，允许省略任一字段并使用默认值。未设置环境变量时
默认排队上限 4、执行超时 300 秒。未知字段、非法 YAML、找不到文件都会
拒绝启动，避免错误配置静默生效。配置只在创建服务时读取，修改后需要重启。

排队上限只计算等待任务，不含执行中的任务；执行超时不包含排队时间。
单账号并发仍为 1，仍由空闲账号取共享 FIFO 队列，不支持在 YAML 中修改
并发和分配策略。管理页面生图测试的等待时间随后端超时调整，额外留 30 秒。
嵌入式 Python 调用显式传入的 `create_app(timeout=..., queue_size=...)`
优先于 YAML；普通启动和本地预览脚本未显式传入时使用 YAML。

## Docker / Compose MVP

The frontend and Python HTTP server run in one image and one service process.
No separate frontend container, database, Redis or Nginx is required.

```bash
# First start, from the repository root:
cp bridge.example.yaml bridge.yaml
chmod 644 bridge.yaml  # scheduler settings only; readable by container UID 10001
# Optional: cp .env.example .env, then configure an outbound proxy if required.
docker compose up -d --build
```

Open `http://127.0.0.1:18089/admin/`. The default host port deliberately differs
from the local Python preview (18088). An empty account list is supported:
import your exported account JSON through the management page.

The first start creates independent keys in the persistent data volume. Keys
are never printed in startup logs. Retrieve them locally when needed:

```bash
# For management page login:
docker compose exec gemini-bridge cat /data/keys/admin-key.txt
# For CPA / API callers (different from the administrator key):
docker compose exec gemini-bridge cat /data/keys/service-key.txt
```

The container runs as UID/GID 10001. The named `bridge-data` volume holds keys,
`accounts.json`, managed credential files, renewal caches and logs under `/data`.
They survive restart and container recreation. **Do not use `docker compose down -v`
when you want to keep accounts.** To back up, retain the volume or export accounts;
account JSON export does not include service/admin keys or historical logs.

`bridge.yaml` is mounted read-only. Edit it on the host and recreate the service
to apply startup settings (recreation also refreshes a bind mount after editors
replace files atomically):

```bash
docker compose up -d --force-recreate
# Stop without deleting the data volume:
docker compose down
```

On OrbStack, use `GEMINI_PROXY=http://host.docker.internal:10808` in `.env` if
Google access requires the host v2rayN proxy. `127.0.0.1` inside a container is
not the host. Linux deployments must provide their own reachable proxy endpoint
when needed; no existing CPA, CPAMP or Nginx settings are changed by this Compose.
The published port binds only to host loopback. Remote administration can use
an SSH tunnel; CPA in another container needs an explicitly reachable Bridge
address/network before integration. Do not assume its `localhost` is this service.

### Container logs

```bash
# Console: startup, access logs, warnings and safe diagnostic events
docker compose logs --tail=100 -f gemini-bridge
# Dedicated sanitized JSON diagnostics
docker compose exec gemini-bridge tail -f /data/logs/diagnostics.jsonl
# Status (health check only reads the local admin page, never calls Google)
docker compose ps
```

There are two independent limits: diagnostics use 5 MiB per file plus 3 backups
(about 20 MiB); Docker `json-file` uses 10m and at most 3 files (about 30 MB).
Oldest files are removed at rotation. HTTP health checks only establish local
service readiness, not account validity. Automatic restart handles process exits;
it does not repair expired Google sessions. Stopping the service can cancel an
in-flight generation with an unknown upstream outcome; do not immediately retry.

This MVP keeps existing log levels. YAML runtime log-level switches/reload are
not implemented yet. Do not enable raw upstream SDK DEBUG output to diagnose
credentials; use the dedicated sanitized events. No real account data, local
configuration, logs, `.git`, tests or browser captures enter the build context.

Images are built locally for the Docker host architecture; no public image has
been published by this setup. Optional `GEMINI_BRIDGE_VERSION` sets package build
metadata, and `GEMINI_BRIDGE_IMAGE` sets the image tag. For releases use a fixed tag
and retain the corresponding AGPL source revision; this local MVP defaults to
`gemini-web-bridge:local` / `0.0.0+docker`.
