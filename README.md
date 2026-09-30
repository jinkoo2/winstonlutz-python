# Winston-Lutz (Python)

Python port of the C# Winston-Lutz IGRT watcher and the `winston_lutz_2d` ITK analysis core.

## Commands

```
conda activate winstonlutz

python -m winstonlutz analyze-image path\to\RI.xxx.dcm
python -m winstonlutz analyze path\to\YY-MM-DD_HH-MM-SS --data-root sample_data
python -m winstonlutz validate-golden sample_data
python -m winstonlutz watch --watch-path \\share\QA\2.IGRT --data-root D:\MachineQA\projects\winstonlutz\sample_data
python -m winstonlutz watch
python -m winstonlutz gui
python -m winstonlutz gui path\to\folder\with\RI.dcm
python -m winstonlutz plan-beams sample_data\Edge\Plan\RP.EdgeDryRun.WL.dcm
```

## Replace the C# Windows service

The GUI is **not** the service. C# `WinstonLutzWindowsService` is a headless watcher. The Python equivalent is:

```
python -m winstonlutz watch
```

It watches for trigger files matching **`Watcher.new_case_file_patterns`** (default **`RE.*.dcm`**), queues the case folder (`machine_to_case_dir_levels` parents up, default 1), waits a few seconds, then runs the same analysis/report/email path as C# (`winston_lutz_2d.exe` is no longer needed). Subfolders are included when **`watch_subfolders`** is true. When **`disk_scan_for_new_case_detection`** is true, every **`disk_scan_for_new_case_detection_sec`** it also walks `watch_path` for unprocessed case folders in case the filesystem watcher missed a create event (common on UNC shares).

### Paths (from the current C# `App.config`)

| C# key | Python | Typical value |
|---|---|---|
| Watch Path | `Watcher.watch_path` | `\\varianfs\VA_TRANSFER\QA\2.IGRT` |
| wl_data_root | `Watcher.winstonlutz_data_root` | `\\uhmc-fs-share\Shares\RadOnc\Planning\Physics QA\WinstonLutz` |
| Log Path | `_logs\` next to the settings file / project | C# used `...\WinstonLutz\_logs` |
| image_tools_dir | unused | analysis is in-process |

Put those paths and match rules in **Settings → Watcher**, or in `settings.json`:

```json
"Watcher": {
  "watch_path": "\\\\varianfs\\VA_TRANSFER\\QA\\2.IGRT",
  "winstonlutz_data_root": "\\\\uhmc-fs-share\\Shares\\RadOnc\\Planning\\Physics QA\\WinstonLutz",
  "watch_subfolders": true,
  "new_case_file_patterns": ["RE.*.dcm"],
  "case_folder_name_regex": "^\\d{2}-\\d{2}-\\d{2}_\\d{2}-\\d{2}-\\d{2}$",
  "machine_to_case_dir_levels": 1,
  "queued_case_poll_sec": 10,
  "disk_scan_for_new_case_detection": true,
  "disk_scan_for_new_case_detection_sec": 60,
  "archive_old_cases": true,
  "archive_cases_older_than_days": 7,
  "archive_old_cases_at": "01:00"
}
```

| Key | Meaning |
|---|---|
| `new_case_file_patterns` | Filename globs that start a case. String or JSON array. Empty/missing → `RE.*.dcm`. |
| `watch_subfolders` | Watch subfolders of `watch_path`. Default `true`. |
| `case_folder_name_regex` | Case folder **name** must match (Python regex, full match). Missing → same default as machines (`YY-MM-DD_HH-MM-SS`). Empty string → any folder name. |
| `machine_to_case_dir_levels` | How many parents above the trigger file is the case folder. `1` = the file’s directory. Use `2` if Aria nests the DICOM one level deeper. |
| `queued_case_poll_sec` | Seconds between starting the next queued case. Default `10`. |
| `disk_scan_for_new_case_detection` | Walk `watch_path` for missed cases. Default `true`. |
| `disk_scan_for_new_case_detection_sec` | Seconds between those walks when `disk_scan_for_new_case_detection` is true. Default `60`. Folders found only by the scan wait at least 15 s (or `queued_case_poll_sec`, whichever is larger) after the newest trigger file so Aria can finish writing. |
| `archive_old_cases` | Nightly move of old case folders from each machine `WATCH_FOLDER` to `DATA_FOLDER`. Default `true`. |
| `archive_cases_older_than_days` | Keep this many calendar days of cases on the transfer share. Default `7`. |
| `archive_old_cases_at` | Local time (`HH:MM`) to start archiving. Default `01:00`. Runs in a ~3-hour window so a 1:15 service restart still archives; daytime restarts wait until the next night. Cases in the analysis queue are skipped. If the same case name already exists in `DATA_FOLDER`, the watch copy is left in place. |

The machine name is the **parent of the case folder** (`Edge/26-09-23_06-21-08/RE.*.dcm` → `Edge`; if the parent is `Data`, the grandparent is used). Look up that name in **MACHINES**. `config.txt` and `app.config.txt` are not used. `winstonlutz_data_root` is where JSON history and ReportTmplt live: `{winstonlutz_data_root}\{machine}\Data\Json` and `{winstonlutz_data_root}\{machine}\ReportTmplt` (or the template path on the machine in settings). After analysis the **full** `report.html` is emailed to **Notifications.email.new_case_email_to** (plus any extra addresses on that machine), with `result.png` files inlined (CID). Watcher start/stop goes to **event_email_to**. Crash mail uses **error_email_to**. A nightly archive that moved cases is emailed to **event_email_to**; archive errors go to **error_email_to**.

**Open Case** lists cases from each machine’s `WATCH_FOLDER` first, then `DATA_FOLDER`. If the same case name exists in both, the watch copy is shown. The watcher archives case folders older than `archive_cases_older_than_days` from watch to data at `archive_old_cases_at`. Older Watcher key names (`data_root`, `disk_scan`, `poll_sec`, …) are still read if present.

### Cutover

1. On the service PC, run packaged **WinstonLutz.service.exe** (or, from source, the `winstonlutz` conda env). Confirm watch:

   ```
   WinstonLutz.service.exe
   ```

   From source instead:

   ```
   conda activate winstonlutz
   python -m winstonlutz watch
   ```

   Log in as the **same Windows account** the C# service uses (UNC share permissions). You should see `Watcher started on ...`. Leave it running and wait for one new `RE.*.dcm`, or copy a test `RE.*.dcm` into a dummy case folder. Confirm `result.txt` / `report.html` appear. Ctrl+C to stop.

2. **services.msc**: stop **WinstonLutzWindowsService** (or whatever the C# service is named). Do not run C# and Python watchers at the same time — both would process the same case.

3. On the service PC, **Settings → Watcher → Install Watcher as Service** (Windows only). The dialog asks for `nssm.exe`, the program (`WinstonLutz.service.exe` when packaged, `python.exe` from source), arguments (empty for the service exe, or `-u -m winstonlutz watch` from source), app folder, settings file, and the Windows account that can reach the UNC shares. Administrator is required. You can still install **NSSM** (https://nssm.cc) by hand.

   Packaged (no separate Python). Keep `WinstonLutz.gui.exe`, `WinstonLutz.service.exe`, and `settings.json` in the same folder:

   ```
   nssm install WinstonLutzWatch C:\Apps\WinstonLutz.service.exe
   nssm set WinstonLutzWatch AppDirectory C:\Apps
   nssm set WinstonLutzWatch AppEnvironmentExtra WINSTONLUTZ_APP_CONFIG=C:\Apps\settings.json
   nssm set WinstonLutzWatch DisplayName "Winston-Lutz Watch"
   nssm set WinstonLutzWatch Start SERVICE_AUTO_START
   nssm set WinstonLutzWatch AppStdout C:\Apps\_logs\watch_stdout.log
   nssm set WinstonLutzWatch AppStderr C:\Apps\_logs\watch_stderr.log
   nssm set WinstonLutzWatch AppRotateFiles 1
   nssm set WinstonLutzWatch ObjectName "DOMAIN\service-account" "password"
   nssm start WinstonLutzWatch
   ```

   From source:

   ```
   nssm install WinstonLutzWatch C:\Users\jkim20\AppData\Local\anaconda3\envs\winstonlutz\python.exe
   nssm set WinstonLutzWatch AppDirectory D:\MachineQA\projects\winstonlutz
   nssm set WinstonLutzWatch AppParameters "-u -m winstonlutz watch"
   nssm set WinstonLutzWatch AppEnvironmentExtra WINSTONLUTZ_APP_CONFIG=D:\MachineQA\projects\winstonlutz\settings.json
   ```

   Set **ObjectName** to the same account as the C# service (needs **Log on as a service** plus read/write on both UNC shares). For a packaged install, `AppDirectory` is the folder that holds `WinstonLutz.gui.exe`, `WinstonLutz.service.exe`, and `settings.json`. From source, it is the project folder so `python -m winstonlutz` can import the package.

4. `nssm status WinstonLutzWatch` and check `_logs\winstonlutz_YYYY-MM-DD.log` plus the NSSM stdout/stderr files.

5. After a real linac export, confirm analysis and the short-report email. Then set the C# service to **Disabled** (do not uninstall until you are satisfied).

6. Keep using **WinstonLutz.gui.exe** (or `python -m winstonlutz gui`) on physicist PCs. That is review only; it does not replace the watcher. The Windows service runs **WinstonLutz.service.exe**.

`packaging/install_watch_service.ps1` also prints the NSSM commands with paths filled in for this machine.

## CI / CD

GitHub Actions runs tests on **Linux, Windows, and macOS** for every push and pull request (`.github/workflows/ci.yml`).

Pushing a version tag builds desktop apps and a Python package, then attaches them to a GitHub Release (`.github/workflows/release.yml`):

```
# bump version in pyproject.toml first
git add pyproject.toml
git commit -m "Release 0.1.1"
git tag v0.1.1
git push origin HEAD
git push origin v0.1.1
```

The tag must match `vMAJOR.MINOR.PATCH` (for example `v0.1.1`). Each release includes:

- `WinstonLutz.gui-<version>-windows-x64.exe` and `WinstonLutz.service-<version>-windows-x64.exe`
- `WinstonLutz.gui-<version>-linux-x64` and `WinstonLutz.service-<version>-linux-x64`
- `WinstonLutz.gui-<version>-macos-arm64` and `WinstonLutz.service-<version>-macos-arm64` (Apple Silicon runner)
- `winstonlutz-<version>-py3-none-any.whl` and source tarball

You can also run **Actions → Release → Run workflow** without a tag; that only uploads build artifacts, it does not create a GitHub Release.

The GUI **Open Case** flow depends on **RunMode**. In **Clinic** mode it picks a machine from settings, then a case folder that contains `RI.*.dcm` files. In **Simple** mode it opens a directory selector for that RI folder and uses the parent folder as the machine name (see [Simple run mode](#simple-run-mode)). A heading across the top shows the institution, signed-in user, **User settings**, and **Login** / **Logout** (OIDC). **Settings** (toolbar, `Ctrl+,`) has **General**, **Machines**, then **Notifications**. General is `Institution`, `RunMode`, and **Identity** (`user_id_method`: None, OSUser, or OIDC). Machines is the `MACHINES` list (add/remove, folders, plan file, analysis knobs). Notifications holds **Email** (SMTP plus `error_email_to`, `event_email_to`, `new_case_email_to`) and incoming webhooks for **Google Chat**, **Slack**, **Microsoft Teams**, and **Discord**. Saves go to `settings.json` next to the executable. You can run field/BB analysis, review pass/fail in a table, view `report.html`, and inspect each image (pan, wheel zoom, window/level). Red cross = field center, green cross = BB. PyQt5 is required (`pip install PyQt5` or `pip install .[gui]`).

Per-PC settings live in `settings.json` next to the executable (GUI and watcher). Daily logs go in `_logs/winstonlutz_YYYY-MM-DD.log` beside that file (created on startup; older than 7 days are deleted). If the folder cannot be created, file logging is skipped. CLI `-v` and `WINSTONLUTZ_LOG_LEVEL` raise the log level. The window title shows `Institution`. Clinic SMTP is **Settings → Notifications**. Missing analysis keys keep the C++ defaults below, so an older settings file still runs.

`validate-golden` re-runs analysis on `sample_data` and compares `result.txt` to the original C++ output (default tolerance 0.1 mm). The repo includes three machines (`Edge`, `Edge_Cone`, `TrueBeam`) with three cases each; analysis outputs (`*_out`, `report.html`) are kept for the newest case only.

## Simple run mode

The GUI starts in **Simple** mode when any of these is true:

- `settings.json` is missing
- `MACHINES` is missing or empty (no named machines)
- top-level `"RunMode": "Simple"` (Settings → General)

**Open Case** then shows a folder picker. Choose the directory that contains the `RI.*.dcm` files (a case folder). The **parent** of that folder is the machine name used in the window title and table header.

Typical layout:

```
Edge/                      ← machine name
  26-09-24_06-13-24/       ← select this folder
    RI.*.dcm
```

If the parent is named `Data`, the grandparent is used instead, so the usual clinic tree still names the linac:

```
Edge/                      ← machine name
  Data/
    26-09-24_06-13-24/     ← select this folder
      RI.*.dcm
```

Simple mode does not use the `MACHINES` list: no RT Plan table, no HTML report (View Report is disabled; the template path is unknown), and no per-machine crop/SID/BB defaults beyond the built-in analysis defaults. Beam **Name** is `Gxxx_Tyyy_Czzz` from the image angles. Switch **Settings → RunMode** to **Clinic** and add machines when you want the machine/case dialog, plan matching, HTML reports, and per-linac knobs.

```json
{
  "Institution": "Stony Brook University Hospital",
  "RunMode": "Simple"
}
```

## Identity

**Settings → Identity.** `user_id_method` is `None` (default), `OSUser`, or `OIDC`.

| Method | What happens |
|---|---|
| `None` | No user id. Clinic settings only. Window title is Institution. Operator comes from DICOM if present. |
| `OSUser` | Uses the person already logged into Windows / Linux / macOS. No extra login. Writes `_users/<id>.json` (gitignored). If the OS has no email, User settings asks for one. Window title and the top bar show that name. If DICOM OperatorsName is empty, the summary Operator field uses the OS user. |
| `OIDC` | At startup a **Sign in** dialog opens, then the system browser to the identity provider. After login, the app receives an authorization code on a **loopback** HTTP listener and exchanges it for tokens (Authorization Code + PKCE). |

### What each OS can provide (no extra login)

Email is **not** guaranteed. Domain-joined Windows often has a UPN (`user@hospital.edu`); Linux/macOS local accounts usually do not.

| Field | Windows | Linux | macOS |
|---|---|---|---|
| username | `GetUserName` / `%USERNAME%` | `pwd` / `$USER` | same as Linux |
| display_name | `GetUserNameEx` NameDisplay (AD/full name) | GECOS first field (`/etc/passwd`) | GECOS, else Directory Services `RealName` |
| email | UPN if it looks like `user@domain` | usually empty | `EMailAddress` in Directory Services if set |
| domain | `%USERDOMAIN%`, `%USERDNSDOMAIN%`, `DOMAIN\user` (SAM) | — | — |
| UPN | `GetUserNameEx` NameUserPrincipal | — | — |
| uid / gid / groups | — | `pwd` + `getgroups` | same as Linux |
| GeneratedUID | — | — | `dscl` UniqueID |
| hostname, home, OS version | all | all | all |

### ID provider configuration (OIDC)

OIDC is the protocol. Keycloak (or Entra ID, Okta, Google) is an **issuer**. There is no separate Keycloak method.

Settings JSON (`Identity.oidc`):

```json
{
  "Identity": {
    "user_id_method": "OIDC",
    "oidc": {
      "issuer": "https://login.apps.myphysics.net/realms/myphysics",
      "client_id": "winstonlutz",
      "scopes": "openid profile email",
      "redirect_uri": "http://127.0.0.1:17843/callback",
      "registration_url": "https://login.apps.myphysics.net/realms/myphysics/account/"
    }
  }
}
```

| Field | Meaning |
|---|---|
| `issuer` | Keycloak: `{keycloak_url}/realms/{realm}`. Example: `https://login.apps.myphysics.net` + realm `myphysics`. Discovery: `{issuer}/.well-known/openid-configuration`. |
| `client_id` | **This desktop app’s** public client (recommended name `winstonlutz`). Not `account-console`. |
| `scopes` | At least `openid`. `profile` and `email` fill display name and email from the ID token / userinfo. |
| `redirect_uri` | Loopback return URL the app listens on after the browser login. Must match Keycloak **Valid redirect URIs** exactly. |
| `registration_url` | Browser page for **Create account**. Use the Keycloak **Account Console** (`{issuer}/account/`), not the static `/protocol/openid-connect/registrations?client_id=account-console` URL. |

Profiles are stored under `_users/` (gitignored), keyed by `oidc:{sub}`.

### Keycloak (myphysics) — native / loopback client

This matches how a native app should talk to the same realm used by Image Labeler 3D (`login.apps.myphysics.net` / `myphysics`). Labeler **registers** in the Account Console and **signs in** with email/password to its API. Winston-Lutz **signs in** in the browser with Authorization Code + PKCE and a loopback redirect (RFC 8252).

Create a dedicated client in Keycloak (realm **myphysics**):

1. **Client ID:** `winstonlutz` (same as `Identity.oidc.client_id`).
2. **Client type / access:** public (no client secret). Client authentication **Off**.
3. **Capability:** Standard flow **On**. Direct access grants (password) **Off**. Implicit **Off**.
4. **PKCE:** required, method **S256** (`code_challenge_method=S256`). Keycloak 26 already requires this for `account-console`; the desktop client must use it too.
5. **Valid redirect URIs** — add the loopback URI from settings, exactly:
   - `http://127.0.0.1:17843/callback`
   - Prefer **`127.0.0.1`**, not `localhost` (IPv4 vs IPv6 / hosts-file surprises).
   - A wildcard such as `http://127.0.0.1:*` is only OK if your Keycloak version documents it; the app uses a **fixed port** so an exact URI is safer.
6. **Valid post logout redirect URIs:** optional; same loopback origin if you add logout later.
7. **Web origins:** `http://127.0.0.1:17843` if the admin console asks for CORS. Token exchange is a native POST, not a browser CORS call.
8. **Do not** reuse **`account-console`**. That client’s redirects are the Account Console SPA (`…/realms/myphysics/account/`). It will reject `http://127.0.0.1:17843/callback` until you add that URI, and it is the wrong client for this executable.

**Loopback listener:** on **Sign in**, the app binds `127.0.0.1:17843`, opens the system browser to Keycloak, and waits for `/callback?code=…`. Windows may show a firewall prompt for Python/the exe the first time — allow private networks. If the port is in use, change `redirect_uri` in Settings and update Valid redirect URIs to match.

**Registration:** open `{issuer}/account/` (Create account on the Sign in dialog). The Account Console SPA starts OIDC **with PKCE**, then Register. Do **not** use:

`…/protocol/openid-connect/registrations?client_id=account-console&response_type=code&scope=openid&redirect_uri=…`

Keycloak 26 rejects that URL because `account-console` requires `code_challenge_method`. Image Labeler 3D already rewrites that broken URL to `/realms/{realm}/account/`. Winston-Lutz does the same if an old settings file still has it.

**After login:** the ID token / userinfo `sub` is the stable user id. Email and name come from claims when `email` / `profile` scopes are granted.

### User bar, User settings, My machines

The main window has a heading across the top: app name, institution, **current user** (name and email), **User settings**, and **Login** / **Logout** (OIDC only). OSUser has no login/logout; the OS person is already the user.

**User settings** (per profile under `_users/`):

| Field | Meaning |
|---|---|
| Email | Required for QA notification mail. Taken from OIDC claims or OS (Windows UPN / macOS Directory Services) when present. If **OSUser** (or OIDC) has no email, the app prompts you to enter one here. |
| My machines | Check the linacs you are responsible for (names from clinic **Settings → Machines**). |
| New QA case emails | **Off**, **My machines**, or **All machines**. Uses clinic SMTP (`Settings → Notifications` `email_from` + `email_host_address`). Does not use `error_email_to`. |

Existing case folders are remembered on first launch so you are not emailed for history. A later new case folder (or the directory watcher) emails subscribers once per machine/case.

## Notifications

**Settings → Notifications** (after Machines). Uncaught exceptions and `logger.exception` events are sent to **error_email_to** and every chat channel that is filled in. Watcher start/stop goes to **event_email_to**. The full IGRT report after watcher analysis goes to **new_case_email_to**. Empty arrays turn that mail off. Each section has a **Send test** button that uses the values currently in the form (Save is not required). A success or SMTP/webhook error dialog is shown.

| Section | JSON | Notes |
|---|---|---|
| Email | `Notifications.email` | SMTP plus three address lists (**JSON arrays**, or a legacy string). Local parts use `email_domain`. Needs `email_from` and `email_host_address`. Empty array = that mail off. `email_from_enc_pw` is kept in JSON if you use authenticated SMTP. |
| `error_email_to` | crashes, `logger.exception`, missing watch folder | Sample: `["jinkoo.kim@stonybrookmedicine.edu"]`. |
| `event_email_to` | watcher start/stop | Same array form. |
| `new_case_email_to` | clinic-wide IGRT report after analysis | A machine may still list extra addresses. |
| Google Chat | `Notifications.google_chat.webhook_url` | Incoming webhook for a Chat space. Posted as `{"text": "..."}`. |
| Slack | `Notifications.slack.webhook_url` | Incoming webhook for a channel. Posted as `{"text": "..."}`. |
| Microsoft Teams | `Notifications.microsoft_teams.webhook_url` | Incoming webhook (Workflows or Office 365 connector). Posted as `{"text": "..."}`. |
| Discord | `Notifications.discord.webhook_url` | Channel webhook. Posted as `{"content": "..."}`. |

Copy `settings.sample.json` to `settings.json` next to the executable (the live file is gitignored). The settings file is **JSONC**: `//` and `/* */` comments are stripped before `json.loads`. **Settings → Save** writes strict JSON and drops comments. `configs.json` is also gitignored; `configs.sample.json` is a short SMTP/webhook template. SMTP fields live only under `Notifications.email`. Older files with top-level `email_*` / `error_email_to` still work if `Notifications.email` is absent. Saving Settings writes the nested `Notifications` block and drops those top-level email keys. An older `winstonlutz.gui.settings.json` in the same folder is still read if `settings.json` is missing.

## Post-processing

After analysis (watcher or GUI), winstonlutz runs **Settings → Post-processing** steps in order. Today there is one type: **`docuforms2_igrt`**, ported from `_ref_projects/docuforms_import/scripts/upload_igrt`.

```json
"PostProcessing": [
  {
    "type": "docuforms2_igrt",
    "enabled": true,
    "backend_url": "https://roweb3.uhmc.sbuh.stonybrook.edu:9001",
    "verify_ssl": false,
    "dry_run": false,
    "attach_dcm_zip": true,
    "attach_pdf": true,
    "resubmit": false,
    "timeout_sec": 300,
    "form_ids": [
      {"machine": "Edge", "form_id": "sb_edge_mlc_wl"},
      {"machine": "Edge_Cone", "form_id": "sb_edge_cone_wl"},
      {"machine": "TrueBeam", "form_id": "sb_truebeam_mlc_wl"},
      {"machine": "TrueBeamSH_Cone", "form_id": "sb_edge_cone_wl"},
      {"machine": "TrueBeamSH", "form_id": "pfcc_truebeamsh_mlc_wl"}
    ],
    "email_success_event_to": ["jinkoo.kim@stonybrookmedicine.edu"],
    "email_failure_event_to": ["jinkoo.kim@stonybrookmedicine.edu"]
  }
]
```

It submits beam offsets and result.png images to `POST {backend_url}/api/forms/{form_id}/submit`, optionally uploads `input_dcm.zip` and **`report.pdf`** (from the full `report.html`) to `/api/upload`. Form id comes from **`form_ids`** (`machine` → `form_id`). A machine with no row is skipped. A successful upload writes `.docuforms2_igrt.json` in the case folder so the same case is not submitted again (`resubmit` overrides). Cases are **not** moved to `imported/`. `email_success_event_to` is emailed for `ok` / `dry-run`; `email_failure_event_to` for `failed` (clinic SMTP from **Notifications**). Skipped cases are not emailed. Add further objects to the `PostProcessing` array later for other steps.

## Machine settings (`settings.json`)

Each object under `MACHINES` is one linac (or cone mode). Edit in **Settings → Machines**, or the JSON file. After changing geometry or classification keys, **re-run analysis** on a known case before trusting new numbers — offsets in `result.txt` are not comparable across different `crop_mm` / `sad_mm`.

### Clinic / folders

| Key | Meaning | When to adjust |
|---|---|---|
| `Institution` (top-level) | Shown in the GUI window title. | Set once per PC to the hospital name. |
| `RunMode` (top-level) | `Clinic` (default) or `Simple`. Simple also applies when the settings file is missing or `MACHINES` is empty. | Use `Simple` for a one-folder Open Case picker without a machine list. |
| `NAME` | Label in **Open Case** and in reports. | New machine, or to match the folder name under the data tree. |
| `WATCH_FOLDER` | Live case folders from Aria transfer (`YY-MM-DD_HH-MM-SS`). **Open Case** lists this tree first. | Clinic: `\\varianfs\VA_TRANSFER\QA\2.IGRT\{machine}` (Edge, TrueBeam, …). |
| `DATA_FOLDER` | Archive of case folders. **Open Case** lists this after `WATCH_FOLDER`. Nightly watcher archive lands here. | Path to that linac’s `Data` folder on this PC. |
| `DICOM_PLAN_FILE` | Path to that machine’s RT Plan (`RP*.dcm`). When set, **Open Case** builds the table from plan beams (Beam, Name, Type, Gantry, Table, Coll) and matches each `RI.*.dcm` by `ReferencedBeamNumber`. The first read writes `{RP file}.json` beside it; later opens use that JSON unless the DICOM is newer or the JSON is missing. | Edge sample: `sample_data/Edge/Plan/RP.EdgeDryRun.WL.dcm`. Omit until you have a plan; the table is one row per RI and **Name** is `Gxxx_Tyyy_Czzz` from the snapped angles. |
| `IGNORE_BEAMS` | Plan beam numbers that are listed in the table but not scored. No RI shows **NA** (not Missing). They never fail `ALL_RI_IMAGE_REQUIRED`. | Edge CBCT is beam `2`: `[2]`. Omit or `[]` if every listed beam should be acquired. |
| `ALL_RI_IMAGE_REQUIRED` | If `true`, any required plan beam without a matching RI makes the case **Fail**. If `false` (default), pass/fail uses only acquired images; missing beams are labeled Missing but do not fail the case. | Set `true` only when every WL beam must be imaged. `IGNORE_BEAMS` (and CBCT by name) are never required. |
| `REPORT_TEMPLATE_FILE_PATH` | Path to `report1.tmpl.html` (the `full` template). The `full`/`short` sibling folders are inferred from this. After a watcher analysis, this **full** `report.html` is emailed (images inlined via CID). | Point at that machine’s `ReportTmplt\\full\\report1.tmpl.html`. |
| `CASE_FOLDER_NAME_REGEX` | Only folders whose names match are treated as cases. Default `^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$`. | Different export naming (e.g. four-digit year). Empty/missing uses the default. |
| `record_csv_file` | Append-only CSV of date, time, operator, max *d*, G/T/C. | Clinic share path for trending; leave blank to skip CSV. |
| `new_case_email_to` | Optional extra IGRT-report addresses for this machine, added to **Notifications.email.new_case_email_to**. Empty = clinic list only. | Leave empty unless this linac needs extra people. |

### Pass/fail and BB search

| Key | Meaning | When to adjust |
|---|---|---|
| `WL_pass_tolerance` | Case/row **Pass** if `d ≤` this value (mm). Default **1.0**. | Departmental WL spec (e.g. 0.75 or 1.5 mm). The GUI **Tol** spin box can override for viewing. |
| `MV_bb_search_method` | BB finder for MV images: `ConnectedComponent`, `LoG`, or `OtsuThreshold`. | If the MV BB is missed or the mask is too large. Edge typically `ConnectedComponent`; TrueBeam MV often `LoG`. |
| `kV_bb_search_method` | Same choices for kV. | If kV BB detection fails. TrueBeam kV often `OtsuThreshold`; Edge often `ConnectedComponent`. |
| `MV_field_search_method` | How MV **field center** is found. Only **`Otsu`** (Otsu threshold + center of mass). This is **not** ConnectedComponent; that name is the BB finder. | Leave `Otsu`. |
| `kV_field_search_method` | How kV field center is found. Only **`ImageCenter`** (panel/image origin `(0,0)`). kV has no radiation field. | Leave `ImageCenter` for Varian OBI-style WL. |

### Geometry (field / BB search region and isoplane scale)

| Key | Meaning | Default | When to adjust |
|---|---|---|---|
| `crop_mm` | Square crop about the **image center**, in mm. All field/BB work uses this ROI. A two-element list `[x, y]` sets width and height separately. | `50.0` | **Increase** if the jaw/cone field or BB is clipped (common with larger MV fields). **Decrease** for small cones so the crop is not mostly collimator/outside air. The BB must lie inside this window; the code assumes isocenter projects near panel center. |
| `sad_mm` | Assumed source-to-axis distance. Offsets are scaled by `sad_mm / SID`. | `1000.0` | Machines whose SAD is not 100 cm (uncommon for C-arm linacs). Changing this scales every mm in `result.txt`. |
| `default_sid_mm` | Used only when DICOM SID (`3002,0026`) is missing. | `1500.0` | Match the imager SID you actually use (e.g. 1600 mm) if tags are absent. If SID is in the DICOM, this key is ignored. |

### MV vs kV classification

Used to pick field search and which BB method. Energy is tried first, then RT image text, then size.

| Key | Meaning | Default | When to adjust |
|---|---|---|---|
| `MV_kvp_min` | `KVP ≥` this → **MV**, else **kV**. Varian stores 6 MV as `KVP=6000`; kV is typically 70–85. | `1000.0` | Raise if a vendor writes diagnostic kV above 1000 (unusual). Lower only if MV energy is stored as a small number and is being classified as kV. |
| `MV_image_size` | Fallback `[columns, rows]` treated as MV when KVP and RT labels are missing. | `[1190, 1190]` | Match that linac’s MV panel matrix (Elekta / other Varian panels differ). Set `[0, 0]` to disable this fallback. |
| `kV_image_size` | Same for kV. | `[1024, 768]` | Match the kV panel matrix. Disable with `[0, 0]` if size would collide with another modality. |

If energy, label, and size all fail, the image is **skipped** (not analyzed).

### Nominal angles (display and sort)

These are **not** the measured DICOM angles. They are the planned WL walk. Analysis writes the **closest** list entry (circular distance, **360° ≡ 0°**) as `Gantry` / `Table` / `Collimator` in `{file}_out/result.txt`. The GUI table, HTML report titles, and CSV use those same values. Ties keep the earlier list entry.

Empty or omitted list = no snap; that axis sorts by the raw angle.

List **order** is the sort order (not increasing angle). Default table sort is type (MV then kV), then gantry list, then table, then collimator. Hover a G/T/C cell to see the actual DICOM angle.

| Key | Meaning | When to adjust |
|---|---|---|
| `nominal_gantry_angles` | Allowed gantry bins and walk order. | Start `[270, 0, 90, 180]`. Change if this linac’s WL card uses a different sequence or extra cardinals. |
| `nominal_table_angles` | Allowed table (IEC couch) bins and walk order. | Start `[90, 45, 0, 315, 270]`. Add/remove kicks this machine actually uses. |
| `nominal_collimator_angles` | Allowed collimator bins and walk order. | Start `[135, 90, 45, 0, 315, 270, 225]`. Trim if you only ever use 0/90/270. |

### Example (one machine)

```json
{
  "NAME": "Edge",
  "crop_mm": 50.0,
  "sad_mm": 1000.0,
  "default_sid_mm": 1500.0,
  "MV_field_search_method": "Otsu",
  "kV_field_search_method": "ImageCenter",
  "MV_kvp_min": 1000.0,
  "MV_image_size": [1190, 1190],
  "kV_image_size": [1024, 768],
  "nominal_gantry_angles": [270, 0, 90, 180],
  "nominal_table_angles": [90, 45, 0, 315, 270],
  "nominal_collimator_angles": [135, 90, 45, 0, 315, 270, 225],
  "DICOM_PLAN_FILE": "D:\\MachineQA\\projects\\winstonlutz\\sample_data\\Edge\\Plan\\RP.EdgeDryRun.WL.dcm",
  "IGNORE_BEAMS": [2],
  "ALL_RI_IMAGE_REQUIRED": false
}
```

## Analysis pipeline

For each RI DICOM:

1. Read the image (GDCM) and set the origin at the image center.
2. Classify **MV** vs **kV** (see below).
3. Optionally preprocess (`--preprocess`, off by default): 2×2 median, rescale to 0–255, invert if the field is dark. Production sample_data was generated without this.
4. Crop **`crop_mm`** about the image center (default **50 mm**; a `[x, y]` list is also allowed). All field/BB work is done on this crop, with the origin reset to the image center.
5. Find the **field center**, then the **BB center**, in the image plane (mm).
6. Scale offsets to isocenter: `scale = sad_mm / SID_mm` (defaults **SAD 1000 mm**, SID from DICOM `3002|0026`, fallback `default_sid_mm` **1500 mm**).
7. Write `{file}_out/result.txt` and `result.png` (center crop, Laplacian-of-Gaussian σ=1, min-max to 8-bit, red FC / green BB crosses).

`result.txt` stores isocenter-plane coordinates. The key `bb_cetner` is the historical typo from the C++ output. New runs also write `bb_search=` (`ConnectedComponent`, `LoG`, or `OtsuThreshold`). When the GUI opens a folder, it restores **MV BB Detection** / **kV BB Detection** from `bb_search` in `result.txt`, then the machine entry in settings JSON, then `{file}_out/log.txt`.

## Determining MV vs kV

Used to choose field search and which BB algorithm (GUI **MV BB** / **kV BB** dropdowns). Checked in this order:

1. **Energy (preferred).** Read KVP from `ExposureSequence` item `(0018,0060)`, or top-level KVP if present. Varian writes 6 MV as `KVP=6000` and kV images as `70` or `85`.
   - `KVP ≥ MV_kvp_min` (default **1000**) → **MV**
   - otherwise → **kV**
2. **RT image text.** `RTImageDescription` contains `[MV]` / `[kV]`, or `RTImageLabel` starts with `MV` / `kV`.
3. **Image size (legacy).** Columns × Rows matching `MV_image_size` (default **1190×1190**) → MV; `kV_image_size` (default **1024×768**) → kV.

If none match, the image is skipped. SimpleITK/GDCM does not expose nested KVP, so energy is read with pydicom.

| | MV | kV |
|---|---|---|
| Field center | Otsu + center of mass | Image center `(0, 0)` |
| BB method | GUI **MV BB** | GUI **kV BB** |

## Field-center detection

**MV only** (`field_search_yes`):

1. Otsu threshold the center crop (`sitk.OtsuThreshold`, inside 0 / outside 255).
2. Field center = center of mass of the non-zero mask, in physical mm (origin at the image center).

**kV** (`kV_field_search_method` = `ImageCenter`): field center is `(0, 0)` (the image/panel center). kV images have no radiation field to measure.

## BB-center detection

All methods return a mask and a center of mass in the image physical frame. The GUI/CLI method names map to these:

### ConnectedComponent (default)

ITK `ConfidenceConnected` grown from a seed, then iterated on the new centroid:

- Seed = field center (MV) or `(0, 0)` (kV).
- Parameters: multiplier **2.5**, **5** iterations, neighborhood radius **2**, replace value 255.
- Repeat up to **10** times. Stop when the centroid moves less than **0.01 mm**.

Typical default for Edge / TrueBeamSH.

### LoG

Laplacian-of-Gaussian blob detector, used for TrueBeam MV in the original machine configs:

1. Binary-erode the field mask (ball radius **10**). If there is no field mask (kV + LoG), use a full-crop mask.
2. `LaplacianRecursiveGaussian` with σ **= 1**, normalize across scale.
3. Mask the LoG with the eroded field.
4. Threshold at **max / 2**.
5. BB center = center of mass of that mask.

### OtsuThreshold

Otsu on the crop, then center of mass of the mask. Common kV fallback.

## Pass / fail

`d = ||BB_iso − field_iso||`. The case (or GUI row) **Pass**es if `d ≤` tolerance (default **1.0 mm**, GUI **Tol**).
