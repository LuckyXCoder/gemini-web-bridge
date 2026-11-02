# Gemini Bridge Session Exporter

Minimal Manifest V3 extension for Chrome and Edge, part of Gemini Web Bridge.
AGPL-3.0; see LICENSE. Not an official Google extension.

## Install locally

1. Extract the ZIP into a private local directory.
2. Open `chrome://extensions` (Edge: `edge://extensions`).
3. Enable Developer mode, select **Load unpacked**, and select this directory.
4. Review the permissions yourself. No extension is installed automatically.

## Export and import

1. Use a dedicated browser profile or an incognito session containing only the target Google account. For incognito, explicitly allow this extension there in the browser's extension settings.
2. Open `https://gemini.google.com/app`, click the extension, then **读取当前账号**.
3. Verify the detected email, or enter it if the account controls cannot be read. The export buttons become available once cookies are read and the email is valid.
4. Download JSON or copy JSON. In Bridge, open **新增账号** or **编辑**, select **导入 JSON**, and choose a file or paste the JSON. Review the filled fields before saving.

The two cookies are read through the browser's cookies API, including HttpOnly cookies. Only these two names are requested, only for gemini.google.com, from the cookie store containing the active tab. The email is extracted only from visible account controls, not chat content. Google UI changes or cross-origin account frames may prevent automatic email detection; the email can be entered manually.

Google may share session cookies across multiple signed-in accounts. A detected email does not cryptographically prove Cookie ownership. This MVP rejects non-default `/u/N` or `authuser` account routes and requires a dedicated/single-account session. It does not configure Bridge's account_index. Do not export multiple account labels from the same shared Google session as though they were distinct accounts.

## Privacy

No network uploads, analytics, background scripts, browser storage, or credential logging. Cookie values are kept in popup memory only and are not displayed. Downloads and clipboard contents remain sensitive after the popup closes; keep them private. CSP disables network connections from the popup. Permissions are `cookies`, `activeTab`, `scripting`, `clipboardWrite`, with hosts `https://gemini.google.com/*` and `https://google.com/*`. Reading email via a clicked extension uses temporary active-tab access. The extension does not read browsing history.

This does not keep cookies valid indefinitely. Bridge validates credentials on import/save. Browser installation, actual Google account-control detection and cookie-store permissions must be verified manually in your browser before relying on it.

## Updating from 0.1.0

Version 0.1.1 adds permission for google.com because the two session cookies use
the parent domain .google.com. The previous Gemini-only permission could filter
them out of cookies.getAll results. Queries still request only the two named
cookies applicable to the Gemini URL; no additional cookies are exported.

Replace the unpacked extension files, reload it in chrome://extensions, and
review/accept the updated site permissions if Chrome asks. Confirm version 0.1.1
before retrying. Missing and duplicate cookies now have separate messages.

## Diagnostics (0.1.2)

Expand **诊断信息** and click **复制诊断信息** after reproducing a failure.
Diagnostics contain extension/browser versions, exact-domain permission checks,
default-account route status, email candidate count, Cookie match/filter counts,
and the failed step. They never include Cookie values, email addresses, complete
page URLs, raw browser exception messages, or chat content. Each read resets the
report; closing the popup discards it. No logs are persisted or uploaded.
