# Winston-Lutz (Python)

Python port of the C# Winston-Lutz IGRT watcher and the `winston_lutz_2d` ITK analysis core.

## Commands

```
conda activate winstonlutz

python -m winstonlutz analyze-image path\to\RI.xxx.dcm
python -m winstonlutz analyze path\to\YY-MM-DD_HH-MM-SS --data-root sample_data
python -m winstonlutz validate-golden sample_data
python -m winstonlutz watch --watch-path \\share\QA\2.IGRT --data-root D:\MachineQA\projects\winstonlutz\sample_data
python -m winstonlutz gui
python -m winstonlutz gui path\to\folder\with\RI.dcm
python -m winstonlutz plan-beams sample_data\Edge\Plan\RP.EdgeDryRun.WL.dcm
```

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

- `WinstonLutz-<version>-windows-x64.exe`
- `WinstonLutz-<version>-linux-x64`
- `WinstonLutz-<version>-macos-arm64` (Apple Silicon runner)
- `winstonlutz-<version>-py3-none-any.whl` and source tarball

You can also run **Actions → Release → Run workflow** without a tag; that only uploads build artifacts, it does not create a GitHub Release.

The GUI **Open Case** flow depends on **RunMode**. In **Clinic** mode it picks a machine from settings, then a case folder that contains `RI.*.dcm` files. In **Simple** mode it opens a directory selector for that RI folder and uses the parent folder as the machine name (see [Simple run mode](#simple-run-mode)). **Settings** (toolbar, `Ctrl+,`) edits `Institution`, `RunMode`, and the `MACHINES` list (add/remove, folders, plan file, analysis knobs) and writes `winstonlutz.gui.settings.json` next to the executable. You can run field/BB analysis, review pass/fail in a table, view `report.html`, and inspect each image (pan, wheel zoom, window/level). Red cross = field center, green cross = BB. PyQt5 is required (`pip install PyQt5` or `pip install .[gui]`).

Per-PC GUI settings live in `winstonlutz.gui.settings.json` next to the executable. Daily logs go in `_logs/winstonlutz_YYYY-MM-DD.log` beside that file (created on startup; older than 7 days are deleted). If the folder cannot be created, file logging is skipped. CLI `-v` and `WINSTONLUTZ_LOG_LEVEL` raise the log level. The window title shows `Institution`. Clinic email settings stay in each machine tree’s `app.config.txt` / `config.txt`. Missing analysis keys keep the C++ defaults below, so an older settings file still runs.

`validate-golden` re-runs analysis on `sample_data` and compares `result.txt` to the original C++ output (default tolerance 0.1 mm). The repo includes three machines (`Edge`, `Edge_Cone`, `TrueBeam`) with three cases each; analysis outputs (`*_out`, `report.html`) are kept for the newest case only.

## Simple run mode

The GUI starts in **Simple** mode when any of these is true:

- `winstonlutz.gui.settings.json` is missing
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

## Machine settings (`winstonlutz.gui.settings.json`)

Each object under `MACHINES` is one linac (or cone mode). Edit in **Settings → Machines**, or the JSON file. After changing geometry or classification keys, **re-run analysis** on a known case before trusting new numbers — offsets in `result.txt` are not comparable across different `crop_mm` / `sad_mm`.

### Clinic / folders

| Key | Meaning | When to adjust |
|---|---|---|
| `Institution` (top-level) | Shown in the GUI window title. | Set once per PC to the hospital name. |
| `RunMode` (top-level) | `Clinic` (default) or `Simple`. Simple also applies when the settings file is missing or `MACHINES` is empty. | Use `Simple` for a one-folder Open Case picker without a machine list. |
| `error_email_to` (top-level) | If set, uncaught exceptions and `logger.exception` events are emailed with traceback, host, version, and argv. Full address or a local part (with `email_domain`). Empty/missing = no error email. | `jinkoo.kim@stonybrookmedicine.edu` |
| `email_from`, `email_domain`, `email_host_address`, `email_host_port`, `enable_ssl` | SMTP used for error emails (and required together with `error_email_to`). `email_from_enc_pw` stays in JSON if you need authenticated SMTP; leave empty for open relay. | Match clinic `app.config.txt`. |

Copy `winstonlutz.gui.settings.sample.json` to `winstonlutz.gui.settings.json` next to the executable (the live file is gitignored). `settings.json` and `configs.json` are also gitignored; `configs.sample.json` is the SMTP-only template if you keep a separate overlay file locally.
| `NAME` | Label in **Open Case** and in reports. | New machine, or to match the folder name under the data tree. |
| `DATA_FOLDER` | Directory that contains case folders (`YY-MM-DD_HH-MM-SS`). | Path to that linac’s `Data` folder on this PC. |
| `DICOM_PLAN_FILE` | Path to that machine’s RT Plan (`RP*.dcm`). When set, **Open Case** builds the table from plan beams (Beam, Name, Type, Gantry, Table, Coll) and matches each `RI.*.dcm` by `ReferencedBeamNumber`. The first read writes `{RP file}.json` beside it; later opens use that JSON unless the DICOM is newer or the JSON is missing. | Edge sample: `sample_data/Edge/Plan/RP.EdgeDryRun.WL.dcm`. Omit until you have a plan; the table is one row per RI and **Name** is `Gxxx_Tyyy_Czzz` from the snapped angles. |
| `IGNORE_BEAMS` | Plan beam numbers that are listed in the table but not scored. No RI shows **NA** (not Missing). They never fail `ALL_RI_IMAGE_REQUIRED`. | Edge CBCT is beam `2`: `[2]`. Omit or `[]` if every listed beam should be acquired. |
| `ALL_RI_IMAGE_REQUIRED` | If `true`, any required plan beam without a matching RI makes the case **Fail**. If `false` (default), pass/fail uses only acquired images; missing beams are labeled Missing but do not fail the case. | Set `true` only when every WL beam must be imaged. `IGNORE_BEAMS` (and CBCT by name) are never required. |
| `REPORT_TEMPLATE_FILE_PATH` | Path to `report1.tmpl.html` (the `full` template). The `full`/`short` sibling folders are inferred from this. | Point at that machine’s `ReportTmplt\\full\\report1.tmpl.html`. |
| `CASE_FOLDER_NAME_REGEX` | Only folders whose names match are treated as cases. Default `^\d{2}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}$`. | Different export naming (e.g. four-digit year). Empty/missing uses the default. |
| `record_csv_file` | Append-only CSV of date, time, operator, max *d*, G/T/C. | Clinic share path for trending; leave blank to skip CSV. |

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

`result.txt` stores isocenter-plane coordinates. The key `bb_cetner` is the historical typo from the C++ output. New runs also write `bb_search=` (`ConnectedComponent`, `LoG`, or `OtsuThreshold`). When the GUI opens a folder, it restores **MV BB Detection** / **kV BB Detection** from `bb_search` in `result.txt`, then the machine `config.txt`, then `{file}_out/log.txt`.

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
