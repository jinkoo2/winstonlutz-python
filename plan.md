# Winston-Lutz Python Port Plan

## Goal

Port `_ref_projects/WinstonLutz` (C# watcher / worker / report / email pipeline) to Python, replacing the native image-analysis executable with a Python ITK implementation of the Winston-Lutz algorithm.

Keep the same clinical workflow and data contracts so existing machine folders, `config.txt`, HTML templates, JSON history, and CSV records continue to work.

---

## What the current system does

The C# solution is an **automated IGRT QA pipeline**, not an interactive viewer.

1. A Windows service (`WinstonLutzWindowsService`) or console host (`WinstonLutzCmd`) watches a transfer share (`\\rovarianimage\VA_TRANSFER\QA\2.IGRT`) for new **`RE.*.dcm`** files.
2. On create/rename, the case directory (parent of that file) is queued.
3. Every 10 seconds a worker thread pops a case and runs `WinstonLutz.Run()`.
4. The case folder name must be `YY-MM-DD_HH-MM-SS` (17 characters, one `_`, four `-`).
5. For each **`RI.*.dcm`** in the case folder, C# shells out to:

   `{image_tools_dir}\winston_lutz_2d.exe`

6. Results are written next to each DICOM as `{file}_out\result.txt` and `{file}_out\result.png`.
7. The pipeline then:
   - writes a per-case JSON file under `{machine_data_root}\Data\Json\{dirname}.json`
   - appends a one-line summary to `record_csv_file` from `config.txt`
   - builds `report.html` (full) and `report.short.html` from machine HTML templates
   - emails the short report via SMTP (password decrypted from `config.txt`)

A separate console app (`WinstonLutzReport`) builds weekly / monthly / annual trend HTML (Google Charts) from the JSON store and emails those files.

`MoveFolder` is a small utility that archives case folders older than N days.

The Angular app under `web/angular/charts` is a stub (Chart.js + a placeholder HTTP call). It is **not** part of the production pipeline.

---

## Important: which C++ code is the analysis core

There are **two** C++ trees. They share ITK ideas but are not the same program.

| Tree | Role | Used by C# service? |
|---|---|---|
| `projects-cpp/imagetools/2d/winston_lutz_2d.cxx` | Headless CLI that C# actually launches | **Yes** |
| `projects-cpp/winstonlutz` (`WinstonLutzII`) | Qt + VTK + ITK **interactive GUI** (ROI / disk / line widgets, `ImageSegmenter`) | **No** |

The Python analysis module should port **`winston_lutz_2d.cxx`**. That is the algorithm the C# project depends on.

`projects-cpp/winstonlutz/ImageSegmenter` is still useful as a reference: its Otsu field mask and ConfidenceConnected BB seed loop match `calc_field_center()` and `calc_bb_center_by_confidence_connected_image_filter()` in `winston_lutz_2d.cxx`. Do **not** port the Qt/VTK GUI in this project.

---

## Current analysis pipeline (`winston_lutz_2d`)

CLI:

```
winston_lutz_2d.exe <dcm> <out_dir> field_search_yes|field_search_no
    bb_search_LoG|bb_search_ConnectedComponent|bb_search_OtsuThreshold
    [tag=value&tag=value]
```

C# currently calls it twice if needed:

- **MV (Edge):** `field_search_yes bb_search_{MV_bb_search_method} 0028|0011=1190&0028|0010=1190`
- **kV fallback:** `field_search_no bb_search_{kV_bb_search_method} 0028|0011=1024&0028|0010=768`

Internal steps:

1. Read DICOM with ITK + GDCM.
2. Recenter origin to the image center.
3. Read tags: gantry `300A|011E`, collimator `300A|0120`, couch `300A|0122` (stored as **`360 - couch`**), SID `3002|0026`, operator `0008|1070`.
4. Optional DICOM tag match; skip the file if size/tags do not match.
5. Median filter, rescale to `[0, 255]`, invert if the field is dark.
6. Crop a **50 mm × 50 mm** window at the image center.
7. **Field center (MV):** Otsu mask, then center of mass. kV uses `(0, 0)` as field center.
8. **BB center:**
   - `LoG`: erode field mask (radius 10), Laplacian-of-Gaussian (`sigma=1`), mask, threshold at `max/2`, center of mass
   - `ConnectedComponent`: ConfidenceConnected from the field-center seed, multiplier 2.5, 5 iterations, neighborhood 2, up to 10 outer loops until centroid moves `< 0.01`
   - `OtsuThreshold`: Otsu then center of mass (kV)
9. Scale image-plane offsets to isocenter: `scale = 1000 / SID_mm`.
10. Write `result.txt`, overlay PNGs, and MHD debug images.

`result.txt` keys (preserve spelling for compatibility, including the `bb_cetner` typo):

```
SID_mm=...
Operator=...
Gantry=...
Table=...
Collimator=...
field center=x,y
bb_cetner=x,y
bb offset=x,y
```

C# treats a case as MV if `{file}_out\img.field.mask.mhd` exists.

---

## Proposed Python architecture

Create a new project (suggested: `winstonlutz/` at the MachineQA root, not inside `_ref_projects`).

```
winstonlutz/
  pyproject.toml
  README.md
  winstonlutz/
    __init__.py
    config.py          # param / config.txt + app.config.txt
    models.py          # WinstonLutzItem
    analysis.py        # SimpleITK port of winston_lutz_2d
    pipeline.py        # WinstonLutz.Run: analyze, csv, json, html
    watcher.py         # directory watch + queue
    worker.py
    emailer.py         # SMTP + password decrypt
    report.py          # trend HTML (weekly/monthly/annual)
    archive.py         # MoveFolder equivalent
    cli.py             # analyze | watch | report | archive
  templates/           # optional local copies; production uses machine ReportTmplt
  tests/
    fixtures/          # small anonymized RI DICOMs + golden result.txt
```

**Entry points**

- `winstonlutz analyze <case_dir>` — one case, same as `WinstonLutz.Run`
- `winstonlutz watch` — long-running watcher (NSSM / Task Scheduler / Windows service)
- `winstonlutz report weekly|monthly|annual <machine_dir>`
- `winstonlutz archive <src> <dst> <age_days>`

Do not require a .NET Windows Service project. Run the watcher as a Python process supervised by NSSM or Task Scheduler.

---

## Recommended libraries

| Concern | Library | Why |
|---|---|---|
| ITK filters | **SimpleITK** | Direct mapping of Otsu, Median, LoG, ConfidenceConnected, crop, mask, MHD/PNG I/O |
| DICOM tags | **pydicom** (or SimpleITK metadata) | Gantry / couch / SID / operator / size filters |
| Watcher | **watchdog** | `RE.*.dcm` create/rename, recursive |
| Config | stdlib | Keep `key = value` files; `#` comments |
| HTML | string replace first, **Jinja2** later | Existing `{{{token}}}` templates can stay as-is |
| Email | **smtplib** + **email** | Same SMTP settings |
| Password decrypt | **pycryptodome** Rijndael-256-CBC | C# `RijndaelManaged` uses **256-bit block size**, not AES-128. Must match `Rfc2898DeriveBytes` (1000 iterations, 32-byte salt + 32-byte IV + ciphertext, passphrase `qwert12345!@#$%`) |
| Overlay PNG | SimpleITK compose, or **Pillow** / matplotlib | Match `result.png` well enough for reports |
| Tests | **pytest** | Golden-file comparison vs C++ exe |

---

## Compatibility rules (do not break these)

Keep these contracts so old cases and templates still work:

- Case folder naming and the 17-character check
- Watch filter `RE.*.dcm`; process `RI.*.dcm`
- `{dcm}_out\result.txt` / `result.png` / `img.field.mask.mhd`
- `result.txt` key names, including `bb_cetner`
- JSON item fields: `gantry`, `table`, `collimator`, `field_center`, `bb_center`, `bb_offset_from_field_center`, `DCM`, `user`, `MV`
- CSV line: `date,time,user,max_dist,G=..;T=..;C=..`
- HTML tokens: `{{{title}}}`, `{{{img}}}`, `{{{bb_offset_from_field_center}}}`, `{{{pass_fail}}}`, `{{{result}}}`, etc.
- `config.txt` keys: `machine`, `MV_bb_search_method`, `kV_bb_search_method`, `record_csv_file`, `WL_pass_tolerance`, email keys
- `app.config.txt` keys: `image_tools_dir` (unused once analysis is in-process), email status notify keys
- Table angle = `360 - DICOM couch (300A,0122)`

Improvements that are safe:

- Call analysis in-process (no `winston_lutz_2d.exe`)
- A real thread-safe queue instead of `Stack` + timer
- Deduplicate case processing with a lock file or in-memory set
- Structured logging
- Configurable MV/kV size filters (today Edge 1190×1190 / 1024×768 is hard-coded)
- Do not commit the encryption passphrase; move it to env / secret store after decrypt compatibility is proven

---

## Phased work

### Phase 0 — Scaffold

- Create the package, `pyproject.toml`, CLI stub, and config loader matching `param.cs`.
- Document `watch_path`, `log_path`, `data_root`, and per-machine `config.txt` layout.

### Phase 1 — Analysis module (highest risk)

Port `winston_lutz_2d.cxx` to `analysis.py` with SimpleITK:

- DICOM read + tag extract + match criteria
- preprocess (median, normalize, invert, 50 mm crop, origin at center)
- field Otsu + COM
- BB: LoG, ConfidenceConnected loop, Otsu
- SID scale to isocenter
- write `result.txt`, masks, `result.png`

**Acceptance:** on a small set of historical `RI.*.dcm` files, Python `result.txt` centers agree with the C++ exe to a tight tolerance (suggest **≤ 0.1 mm** at iso, ideally pixel-level agreement). Compare both MV and kV, and both configured BB methods.

Keep the C++ exe available as a fallback flag (`--use-native-exe`) during this phase only.

### Phase 2 — Case pipeline

Port `WinstonLutz.Run`:

- directory-name validation
- loop `RI.*.dcm`, pick MV vs kV method from `config.txt`
- parse `result.txt` → `WinstonLutzItem`
- JSON + CSV
- full / short HTML from existing `ReportTmplt`
- email short report

**Acceptance:** running `winstonlutz analyze <old_case_dir>` produces the same JSON/CSV/HTML shape as C#.

### Phase 3 — Watcher and worker

Port `WinstonLutz_FileSystemWatcher` + `WinstonLutz_Worker`:

- watch `RE.*.dcm` recursively
- debounce / queue unique case dirs
- short random delay (C# waits 1–3 s) so files finish writing
- per-case log file
- exception email via `app.config.txt`

**Acceptance:** dropping a test `RE.*.dcm` into a dummy watch tree starts analysis once, not many times.

### Phase 4 — Trend reports

Port `WinstonLutzLib.report` + `WinstonLutzReport`:

- load JSON history
- cardinal-angle grouping (`round to 10°`, 360 → 0)
- KV vs MV (`field_center == [0,0]` ⇒ KV)
- weekly / monthly / annual HTML
- email attachments

Keep the existing Google Charts templates first. A later optional step can replace them with a small Python plot or a new web UI.

### Phase 5 — Ops extras

- `archive` command (MoveFolder)
- NSSM / Task Scheduler install notes
- optional PDF via weasyprint or similar (C# `wkhtmltopdf` path is unused in the main `Run()` path)

### Phase 6 — Out of scope for the first port

- Qt/VTK GUI (`WinstonLutzII` widgets, manual ROI/circle editing)
- Angular charts stub
- Duplicate `EmailerCmd` project (email lives in the lib)

Revisit a Python GUI only if physicists still need interactive override of field/BB centers.

---

## Suggested package mapping

| C# / C++ | Python |
|---|---|
| `WinstonLutzLib.param` | `config.py` |
| `WinstonLutzItem` | `models.py` |
| `winston_lutz_2d.cxx` | `analysis.py` |
| `WinstonLutz` | `pipeline.py` |
| `WinstonLutz_FileSystemWatcher` | `watcher.py` |
| `WinstonLutz_Worker` | `worker.py` |
| `email` + `Crypt.StringCipher` | `emailer.py` |
| `report` + `WinstonLutzReport` | `report.py` + `cli report` |
| `MoveFolder` | `archive.py` |
| `WinstonLutzCmd` / Windows Service | `cli watch` + NSSM |

---

## Testing plan

1. **Unit tests** for config parsing, directory-name rules, SID scaling, result.txt parse (including `bb_cetner`), cardinal angles, pass/fail vs `WL_pass_tolerance`.
2. **Golden analysis tests** using 4–8 anonymized images:
   - Edge MV 1190×1190
   - Edge kV 1024×768
   - at least one LoG and one ConnectedComponent case
3. Side-by-side run: C++ `winston_lutz_2d.exe` vs Python on the same DICOM; fail the test if iso offsets differ beyond tolerance.
4. **Pipeline test** with a fake case folder and local templates; assert JSON/CSV/HTML tokens.
5. **Watcher test** with a temp directory (create `RE.*.dcm`, assert one queued job).

Do not use production PHI images in the repo. Anonymize fixtures first.

---

## Risks

- **Numeric parity.** SimpleITK vs ITK 4.x (C++ was built against ITK 4.4/4.8) can differ slightly in Otsu / LoG. Budget time for filter-parameter matching and a fallback to the native exe.
- **Rijndael-256.** Standard AES libraries will not decrypt existing `email_from_enc_pw` values. Use Rijndael-256 or re-encrypt passwords once.
- **File-system events on a UNC share.** `watchdog` on `\\rovarianimage\...` can miss or double-fire events. Keep the 10 s timer + unique-case queue; consider a periodic directory scan as backup.
- **Incomplete writes.** C# already sleeps before processing. Keep a “file size stable” check before opening `RI.*.dcm`.
- **Hard-coded Edge matrix sizes.** TrueBeam or other imagers will be skipped unless match criteria become configurable.
- **Thread safety.** C# uses a non-generic `Stack` from a timer and the watcher callback. Use a `queue.Queue` + a processing set.

---

## Recommended order of implementation

1. Phase 1 analysis + golden tests (this is the only scientifically sensitive part).
2. Phase 2 case pipeline (reports and email on a manual `analyze` command).
3. Phase 3 watcher (this is what replaces the Windows service).
4. Phase 4 trend reports.
5. Phase 5 archive + service install notes.

Ship after Phase 3: a physicist can still generate trend reports with the old `WinstonLutzReport.exe` against the same JSON files.

---

## Success criteria

- A case processed by Python produces the same `result.txt` geometry (within tolerance), the same JSON/CSV/HTML shape, and a short-report email.
- Watcher mode can replace `WinstonLutzWindowsService` without changing the share layout or machine `config.txt`.
- No dependency on `winston_lutz_2d.exe`, Qt, VTK, or .NET at runtime.
)
