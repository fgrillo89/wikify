# Ingest quality audit (post-slice6 structural pass)

Scope: mvp20 corpus (20 memristor/ALD PDFs under `data/papers/mvp20/`).
Baseline: the existing `data/wikify_simple/corpora/mvp20/` from slice 6.

## Phase 1 findings

### 1. Sections lost at the Document boundary
`ingest/refresh.py` hardcoded `sections=[]` on every Document even though
`parse_file` already returned `parsed.sections` and the chunker was
consuming them correctly. Every chunk in the slice-6 corpus carried a
valid `section_path`, but `doc.sections` was always empty — downstream
code reading `doc.sections` saw nothing.

### 2. Year fallback via PDF creation/mod date
`wikify_simple.ingest.metadata.extract_year_from_pdf_meta` already
returns `None` on miss (same as legacy) — that part was fine. The real
bug was in `ingest/parsers/pdf.py` which ordered sources as
`extract_year_from_pdf_meta(meta) or fn_year`. PDF creation date
reflects the last time the file was touched, not the publication year,
so:

| Paper         | filename year | PDF creationDate year | result before | result after |
|---------------|---------------|-----------------------|---------------|--------------|
| Chua 1971     | 1971          | 1999                  | 1999          | 1971         |
| Matveyev 2015 | 2015          | 2026                  | 2026          | 2015         |
| Li 2018       | 2018          | 2026                  | 2026          | 2018         |
| Adam 2017     | 2017          | 2016                  | 2016          | 2017         |
| Jeong 2019    | 2019          | 2018                  | 2018          | 2019         |

Fix: `fn_year or extract_year_from_pdf_meta(meta)`. Legacy
`wikify.ingest.extract.metadata` had this ordering; the port inverted
it. On a miss year is `None`; there is no `datetime.now()` fallback
anywhere in the module.

### 3. Kim 2021 authors: single-token metadata wins
`[2021 Kim] 4K-memristor...pdf` carries `meta["author"] = "H. Kim"`
(the corresponding author only), but the real byline in the markdown
body is
`H. Kim 1,2, M. R. Mahmoodi 1, H. Nili 1 & D. B. Strukov 1✉`.

Two bugs:

1. `parsers/pdf.py` preferred PDF metadata over markdown extraction, so
   a thin single-token list beat a richer in-document list.
2. `metadata._parse_author_line` could not handle:
   - affiliation superscript suffixes (`H. Kim 1,2`)
   - ampersand as a separator (`... & D. B. Strukov`)
   - the Unicode email envelope character `✉`

Fixes in `parsers/pdf.py` (ordering: markdown wins when it yields ≥2
authors, then PDF metadata, then filename) and in
`metadata._parse_author_line` (strip trailing per-author superscripts,
treat `&` as a comma, drop `✉`).

### 4. Topic noise
`"Above All"`, `"Compared With The Set Process"`,
`"Ibility With Cmos Technology.6"` etc all passed the legacy
`_is_valid_keyword` because "above", "compared" weren't in the
bad-starter set and truncated word stems weren't rejected anywhere.
Extended the bad-starter set with a standard stopword-prep list
(above/after/between/compared/than/that/those/under/…) and added
rejectors for
- digit-suffixed fragments (`technology.6`, `device.3`)
- truncated PDF column-break word starts (`ibility`, `ility`, `tion`,
  `sion`, `ment`, `ness`, `ance`, `ence`, `ous`, `ive` as a first
  token — these are word-suffix fragments, never standalone topics).

### 5. Liu 2020 figure trace (the asked-for per-step filter trace)

Opened
`data/papers/mvp20/[2020 Liu] Optimization of oxygen vacancy concentration in HfO2 HfOx bilayer-structured ultrathin memristors by atomic layer deposition and their biological synaptic behavior.pdf`
with fitz and traced each image on each page through the size, dedup,
and caption filters in `ingest/figures.py`:

| page | img | xref | WxH       | bytes  | ext  | filter result       |
|------|-----|------|-----------|--------|------|---------------------|
| 1    | 0   | 140  | 43x43     | 1570   | png  | SMALL + TINY_BYTES  |
| 1    | 1   | 140  | 43x43     | 1570   | png  | SMALL + DUP         |
| 1    | 2   | 140  | 43x43     | 1570   | png  | SMALL + DUP         |
| 2    | 0   | 181  | 767x717   | 155022 | jpeg | KEEP → Fig. 1       |
| 3    | 0   | 190  | 831x691   | 136942 | jpeg | KEEP → Fig. 2       |
| 3    | 1   | 268  | 862x633   | 171903 | jpeg | KEEP → Fig. 3       |
| 4    | 0   | 201  | 861x748   | 149698 | jpeg | KEEP → Fig. 4       |
| 5    | 0   | 245  | 968x412   | 68588  | jpeg | KEEP → Fig. 6       |
| 5    | 1   | 121  | 970x393   | 76598  | jpeg | KEEP → p5_img1      |
| 6    | 0   | 220  | 911x1392  | 266566 | jpeg | KEEP → Fig. 7       |
| 6    | 1   | 48   | 921x656   | 121413 | jpeg | KEEP → Fig. 8       |

`extract_pdf_media` returned 8 valid dicts. So the extractor was fine —
the zero-image count observed in the slice-6 corpus happened **later**
in `save_doc_images`. The old layout wrote binaries to
`corpus/images/{long_doc_id_with_hash}/fig_NNN.ext` and the full
absolute path on Liu 2020 came to **265 characters** — 5 over the
Windows MAX_PATH (260) limit. `save_doc_images` then swallowed the
`OSError` in a `try/except: continue` and returned an empty list, so
the corpus recorded 0 images for Liu 2020 while every other paper (all
with shorter filenames) worked fine.

Two structural fixes, both required:

1. Legacy folder naming. `_image_slug` now produces
   `{sanitized_stem[:80]}` (mirrors
   `wikify.ingest.extract.media._make_paper_slug`). The slug lives on
   disk; `doc_id` (still including the 12-char hash) remains the
   corpus index key. Per-file names are caption-resolved
   (`Fig_1.jpeg`, `Fig_2.jpeg`, …) falling back to `fig_NNN.ext` when
   no caption is matched. Sidecar name is `bin_path + ".json"`.
2. Remove the silent `try/except: continue` swallows in
   `save_doc_images` and `_write_sidecar`. If a write fails under the
   new layout, it is a real bug and should raise. No more masked
   data loss.

After the fix, Liu 2020 persists **8 images** on disk with caption
labels preserved.

### 6. Image decode spot-check

Loaded `fig_000.jpeg` from three different papers out of `mvp20_v2`:
Liu 2020 (Fig. 1 @ 767x717 / 155 KB), Adam 2017 (Fig. 1 @ high res),
Strukov 2008 fallback. Each decodes as a valid JPEG via PIL, dimensions
match the sidecar, and pixel histograms are non-uniform — not blank,
not a single-colour decoration. Confirmed the extractor is producing
real rendered figures, not placeholder bytes.

## Phase 3 changes

| File                                         | Change |
|----------------------------------------------|--------|
| `ingest/refresh.py`                          | build `list[DocSection]` from grouped chunk `section_path`; drop the `sections=[]` hardcode. Switch `image_dir` to `_image_slug(src)` (clean ≤80-char folder, no hash suffix). |
| `ingest/parsers/pdf.py`                      | author ordering: markdown ≥2 → meta → markdown → filename. Year ordering: `fn_year or meta_year`. |
| `ingest/metadata.py` (`_parse_author_line`)  | strip trailing affiliation superscripts, treat `&` as separator, drop `✉` and friends. |
| `ingest/images.py`                           | legacy naming: caption label → `Fig_1.png` style; fallback `fig_NNN.ext`. Removed silent `try/except: continue` around write. Added `_figure_stem` and `_disambiguate`. |
| `ingest/topics.py`                           | extended `_KEYWORD_BAD_STARTERS` with standard preps/stopwords; reject digit-suffix fragments (`\.\d`); reject word-suffix fragments as first token (`ibility`, `tion`, `sion`, `ment`, …). |
| `tests/wikify_simple/test_ingest_quality.py` | new test file covering all four behaviours. |

## Phase 4 validation (mvp20_v2, fresh run)

```
y= 1971 sec=  1 ch= 13 sec%=100 img= 8 auth= 1 | Memristor-The missing circuit element
y= 2008 sec=  5 ch= 22 sec%=100 img= 0 auth= 4 | The missing memristor found
y= 2010 sec=  3 ch= 18 sec%=100 img= 4 auth= 5 | Nanoscale-memristor-device-as-synapse-in-neur
y= 2011 sec=  2 ch= 14 sec%=100 img= 3 auth= 5 | Dopant Control by Atomic Layer Deposition in
y= 2014 sec=  5 ch= 28 sec%=100 img= 5 auth=10 | Ultra-Low-Energy Three-Dimensional Oxide-Base
y= 2014 sec=  7 ch=  9 sec%=100 img= 2 auth= 3 | Foldable neuromorphic memristive electronics
y= 2015 sec=  9 ch= 28 sec%=100 img= 2 auth= 1 | Resistive switching and synaptic properties
y= 2017 sec=  8 ch= 32 sec%=100 img=16 auth= 5 | 3-D Memristor Crossbars for Analog and Neurom
y= 2017 sec= 11 ch= 35 sec%=100 img= 7 auth= 6 | Analog Synaptic Behavior of a Silicon Nitride
y= 2018 sec=  7 ch= 15 sec%=100 img= 7 auth=16 | In-Memory Computing with Memristor Arrays
y= 2018 sec= 12 ch= 34 sec%=100 img=10 auth= 2 | A multi-level memristor based on ALD iron ox
y= 2018 sec=  4 ch= 16 sec%=100 img= 2 auth= 2 | Bio-mimicked ALD iron oxide memristor
y= 2018 sec= 26 ch= 70 sec%=100 img=18 auth= 1 | Memristor Crossbar Arrays for Analog/Neurom
y= 2019 sec= 12 ch= 31 sec%=100 img= 6 auth= 2 | Improving linearity via Al in HfO2 memristor
y= 2019 sec= 17 ch=107 sec%=100 img=20 auth= 3 | Memristor devices for neural networks
y= 2019 sec= 13 ch= 43 sec%=100 img= 7 auth= 8 | Defect-Engineered Electroforming-Free HfOx
y= 2020 sec= 21 ch= 70 sec%=100 img=13 auth= 2 | Memristors Based on 2D Materials
y= 2020 sec= 12 ch= 33 sec%=100 img= 8 auth= 6 | Optimization of oxygen vacancy in HfO2/HfOx
y= 2021 sec= 13 ch= 52 sec%=100 img= 7 auth= 4 | 4K-memristor analog-grade passive crossbar
y= 2025 sec=  7 ch= 19 sec%=100 img=19 auth= 8 | Stable Synapse Function of Bilayer Memristor
```

Headline numbers:

- **Sections**: 20/20 docs populated (was 0/20). `section%=100` on
  every doc — every chunk maps to an entry in `doc.sections`.
- **Years**: no more `2026`, no more `1999` for Chua. Every year
  matches the filename (the canonical source). Chua 1971 recovered.
- **Kim 2021 authors**: `['H. Kim', 'M. R. Mahmoodi', 'H. Nili', 'D. B. Strukov']`
  (was `['H. Kim']`).
- **Strukov 2008 authors**: 4 (was 2) — same markdown-wins fix
  incidentally picked up the other two co-authors.
- **Liu 2020 images**: 8 persisted on disk (was 0). Labeled
  `Fig_1.jpeg`, `Fig_2.jpeg`, …, `p5_img1.jpeg` (unmatched), …
- **Topics**: 21 entries, all real phrases. Representative slice:
  `Analog Resistive Switching`, `Atomic Layer Deposition`,
  `Electroforming-Free`, `HFO2`, `Memristors`, `Oxygen Vacancy`,
  `Resistive Switching`, `Spiking Neural Network`, `Synapse`, `Synaptic
  Device`. No `Above All`, no `Compared With The Set Process`, no
  `Ibility With Cmos Technology.6`, no `Above All`.
- **Image folders on disk** use the slug convention:
  `[2020 Liu] Optimization of oxygen vacancy concentration in HfO2 HfOx bil`
  (≤80 chars, no hash tail), well under MAX_PATH.

Chunk sanity: every doc lands in the expected 9–107 range, mean ~550
chars per chunk (`_TARGET_CHARS=1600` with paragraph-aware split), 100%
of non-image chunks have a `section_path`. Image-caption chunks
(`section_path=['__image__', ...]`) are correctly excluded from
`doc.sections`.

## Phase 5 checks

- `uv run ruff format .` — clean on all touched files.
- `uv run ruff check` — 0 errors in any file I modified. (21 pre-existing
  errors live outside this sweep.)
- `uv run python scripts/check_no_vendor_imports.py` — OK (56 files).
- `uv run pytest tests/wikify_simple/` — 36 passed (was 30; 6 new in
  `test_ingest_quality.py`).
