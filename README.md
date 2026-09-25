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
```

The GUI lets you pick a folder of `RI.*.dcm` files, run field/BB analysis, review pass/fail in a table, view `report.html`, and inspect each image (pan, wheel zoom, window/level). Red cross = field center, green cross = BB. PyQt5 is required (`pip install PyQt5` or `pip install .[gui]`).

`validate-golden` re-runs analysis on `sample_data` and compares `result.txt` to the original C++ output (default tolerance 0.1 mm). The repo includes three machines (`Edge`, `Edge_Cone`, `TrueBeam`) with three cases each.

## Analysis pipeline

For each RI DICOM:

1. Read the image (GDCM) and set the origin at the image center.
2. Classify **MV** vs **kV** (see below).
3. Optionally preprocess (`--preprocess`, off by default): 2×2 median, rescale to 0–255, invert if the field is dark. Production sample_data was generated without this.
4. Crop **50 mm** about the image center. All field/BB work is done on this crop, with the origin reset to the image center.
5. Find the **field center**, then the **BB center**, in the image plane (mm).
6. Scale offsets to isocenter: `scale = 1000 / SID_mm` (SID from DICOM `3002|0026`, default 1500 mm).
7. Write `{file}_out/result.txt` and `result.png` (crop min-max stretched to 8-bit, red FC / green BB crosses).

`result.txt` stores isocenter-plane coordinates. The key `bb_cetner` is the historical typo from the C++ output. New runs also write `bb_search=` (`ConnectedComponent`, `LoG`, or `OtsuThreshold`). When the GUI opens a folder, it restores **MV BB Detection** / **kV BB Detection** from `bb_search` in `result.txt`, then the machine `config.txt`, then `{file}_out/log.txt`.

## Determining MV vs kV

Used to choose field search and which BB algorithm (GUI **MV BB** / **kV BB** dropdowns). Checked in this order:

1. **Energy (preferred).** Read KVP from `ExposureSequence` item `(0018,0060)`, or top-level KVP if present. Varian writes 6 MV as `KVP=6000` and kV images as `70` or `85`.
   - `KVP ≥ 1000` → **MV**
   - otherwise → **kV**
2. **RT image text.** `RTImageDescription` contains `[MV]` / `[kV]`, or `RTImageLabel` starts with `MV` / `kV`.
3. **Image size (legacy).** Columns × Rows `1190×1190` → MV; `1024×768` → kV.

If none match, the image is skipped. SimpleITK/GDCM does not expose nested KVP, so energy is read with pydicom.

| | MV | kV |
|---|---|---|
| Field center | Otsu + center of mass | Image center `(0, 0)` |
| BB method | GUI **MV BB** | GUI **kV BB** |

## Field-center detection

**MV only** (`field_search_yes`):

1. Otsu threshold the 50 mm crop (`sitk.OtsuThreshold`, inside 0 / outside 255).
2. Field center = center of mass of the non-zero mask, in physical mm (origin at the image center).

**kV** (`field_search_no`): field center is `(0, 0)` (the image center).

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
