# ENflorA – Bulk submission of sequencing data to ENA

ENflorA is a small set of Python scripts that help you submit biological
sequencing data to the [European Nucleotide Archive (ENA)](https://www.ebi.ac.uk/ena/browser/home).
You fill in spreadsheets (Excel, CSV or TSV) with your metadata, point the
scripts at your sequence files, and ENflorA handles XML generation, file
compression, manifest creation, and submission.

It was originally built for plastid genome projects, but works for any
organism: sample metadata is driven by whichever ENA checklist you point it at,
with no code changes (see [Using a different
checklist](#using-a-different-checklist)).

For background on ENA's object types and metadata model, see the
[ENA submission documentation](https://ena-docs.readthedocs.io/en/latest/submit/general-guide/metadata.html).


## Index

- [How it works](#how-it-works)
- [Requirements](#requirements)
- [Installation and setup](#installation-and-setup)
- [Try it first: demo mode](#try-it-first-demo-mode)
- [Configuration (`config.yaml`)](#configuration-configyaml)
- [Folder layout](#folder-layout)
- [Script reference](#script-reference)
  - [`biosamples.py`](#biosamplespy)
  - [`make_table.py`](#make_tablepy)
  - [`runs.py`](#runspy)
  - [`analysis.py`](#analysispy)
  - [`resolve_accessions.py`](#resolve_accessionspy)
  - [`hpc.sh`](#hpcsh)
  - [`lftp_sub.sh` (optional)](#lftp_subsh-optional)
- [Using a different checklist](#using-a-different-checklist)
- [Passing accessions between steps](#passing-accessions-between-steps)
- [Logs and receipts](#logs-and-receipts)


## How it works

ENflorA mirrors ENA's own data model. There are three scripts, each handling
one type of ENA object. They are **independent of each other**: run one, two,
or all three. When you do submit everything from scratch, this is the order,
because each step produces accession IDs the next one needs:

```
 1. Create a Study on ENA         (manual, one-time, via the Webin Portal)
         ↓ study accession
 2. biosamples.py                 → registers your samples, returns sample accessions
         ↓ sample accessions
 3. runs.py                       → uploads your raw reads, returns ERR* accessions
         ↓ run accessions
 4. analysis.py                   → uploads your assemblies/annotations
```

You don't have to run all three, and the relationships are not one-to-one:
several read sets can belong to one sample, samples can be reused across
studies, and an assembly can be built on reads submitted by someone else. So
running a single step on its own is a normal case, not an exception. If your
samples are already registered on ENA, skip `biosamples.py` and put the
existing sample accessions straight into your runs table. Same for analysis:
if your reads are already in ENA, just reference those run accessions.

When you do run the steps back to back, you don't have to copy accessions from
one table into the next by hand: see [Passing accessions between
steps](#passing-accessions-between-steps).

### What each step does

**Study** — An umbrella grouping for your project. You create this once,
manually, on the [Webin Portal](https://www.ebi.ac.uk/ena/submit/webin/)
(or the [test portal](https://wwwdev.ebi.ac.uk/ena/submit/webin/) for dry runs).

**biosamples.py** — Registers biological source material: what organism, where
and when it was collected, voucher IDs, GPS coordinates, etc. Takes a
spreadsheet (`BiosampleList.xlsx`) and submits it as XML via `curl`. Returns
one SAMEA accession per sample.

**runs.py** — Uploads raw sequencing reads (FASTQ, BAM, or CRAM) together with
library metadata (instrument, strategy, source). Takes a spreadsheet
(`ExperimentList.xlsx`) plus your read files. Builds per-sample Webin-CLI
manifests, compresses files, and submits. Returns one ERR accession per read set.

**analysis.py** — Uploads genome assemblies or annotations (FASTA or EMBL flat
files). Takes a spreadsheet (`AnalysisList.xlsx`) plus your sequence files.
Can automatically convert GenBank (`.gb`) to EMBL format via Biopython. Handles
contig, scaffold, and chromosome-level submissions. Returns one ERZ accession
per assembly.

Each spreadsheet template has a `DATA` sheet (where you fill in your rows) and
an `INFO` sheet explaining every column. Plain text works everywhere a table is
read: all three scripts accept Excel (`.xlsx`, `.xls`), comma-separated
(`.csv`) and tab-separated (`.tsv`, `.tab`, `.txt`) files with the same column
headers.


## Requirements

What you need depends on how you run ENflorA.

### On FU Berlin's Curta HPC (via `hpc.sh`)

**Nothing.** The `hpc.sh` script loads all necessary modules (Python 3.11,
Java 21), creates a virtual environment with all Python packages, and runs
the scripts. You just need a Webin account and the Webin-CLI JAR file.

### On any other machine (via `set_env.py`)

`set_env.py` creates a virtual environment and installs Python packages for
you, but you need the following already available on your system:

| What | Needed for | Notes |
|------|-----------|-------|
| **Python ≥ 3.8** | all scripts | tested on 3.8–3.12 |
| **Java ≥ 17** | `runs.py`, `analysis.py` | not needed for biosamples |
| **curl** | `biosamples.py` | typically pre-installed on Linux/macOS |
| **Webin-CLI JAR** | `runs.py`, `analysis.py` | [download here](https://github.com/enasequence/webin-cli/releases) |

`set_env.py` handles the rest (pandas, openpyxl, pyyaml, biopython). On an
HPC with a `module` system, add `-H` to also load Java.

### Fully manual (no `set_env.py`)

If you want to manage everything yourself, you need the system tools above
plus these Python packages:

| Package | Needed for | Minimum version |
|---------|-----------|-----------------|
| pandas | all scripts (data handling) | 1.2 |
| PyYAML | all scripts (config file) | any |
| openpyxl | reading `.xlsx` input files | 3.0 |
| Biopython | `.gb` → `.embl` conversion in `analysis.py` only | 1.78 |

Install with: `pip install pandas openpyxl pyyaml biopython`

Biopython is only needed if you submit GenBank files that need conversion to
EMBL format. If you only submit `.embl` or `.fasta` files, you can skip it.


## Installation and setup

```bash
git clone https://github.com/alfarodeprado/ENflorA.git
cd ENflorA
```

Then pick one of these approaches:

**Option A — HPC (FU Berlin Curta):**
Set `ena_object` inside `hpc.sh` and submit with `sbatch hpc.sh`. The script
loads Python/Java modules, creates the virtual environment, and runs everything.
You don't need to call `set_env.py` yourself.

**Option B — `set_env.py`:**
```bash
python set_env.py -s     # create/update virtual environment
python set_env.py -r     # open a shell with venv activated
```
On another HPC with `module` support, add `-H` to load Java:
`python set_env.py -s -r -H`
(You may need to edit the `module add Java/21.0.5` line in `set_env.py` to
match your cluster.)

**Option C — Manual:**
```bash
pip install pandas openpyxl biopython pyyaml
```

### Credentials

Put your Webin login in `credentials.txt` at the repo root (two lines:
username, then password). The file ships with placeholder values — replace
them with your own. You can also pass credentials via `-u` / `-p` flags.

To verify that everything is set up correctly, run the demo (see next section).


## Try it first: demo mode

Before using real data, you can do a complete dry run with bundled synthetic
sequences. This verifies that your setup — credentials, Python, Java,
Webin-CLI — is working end-to-end.

```bash
cd biosamples && python biosamples.py --demo
cd runs      && python runs.py --demo
cd analysis  && python analysis.py --demo
```

Demo mode submits to ENA's test server (data gets auto-deleted the next day in the
test server), prints verbose `[demo]` trace lines so you can see exactly what's
happening, and is hardcoded to never touch the live server.

See [`demo/README.md`](demo/README.md) for the full step-by-step walkthrough,
including how to run it from the HPC.


## Configuration (`config.yaml`)

All three scripts share a single YAML config in the repo root:

```yaml
credentials: ../credentials.txt
jar: ../webin-cli-8.2.0.jar

# Paths to input tables. Can be .xlsx, .xls, .csv, .tsv, .tab, or .txt
data_biosamples: BiosampleList.xlsx
data_runs:       ExperimentList.xlsx
data_analysis:   AnalysisList.xlsx

submit: True
live:   False

sub_dir_biosamples:               # where biosamples XMLs and accessions go
sub_dir_runs:                     # where runs submission folders go
sub_dir_analysis:                 # where analysis submission folders go

# --- biosamples only ---
checklist: ERC000037.xml          # the ENA sample checklist to validate against

extra_mandatory:                  # optional: fields you require, ENA doesn't
  - bio_material
  - altitude
defaults:                         # optional: value used when a cell is empty
  plant growth medium: soil
column_aliases:                   # optional: your column name -> checklist field
  latitude: geographic location (latitude)

assembly_level: chromosome        # contig | scaffold | chromosome
mingaplength: 50                  # used only if scaffold & no AGP
```

`checklist` is the only new key you have to set, and only if you are not
submitting plants. The three below it are optional; see [Keeping your own
column names and rules](#keeping-your-own-column-names-and-rules).

**Precedence:** each script checks the config file first. If a value is missing
or empty, it falls back to the command-line argument. If both are unset, the
script's internal default is used. So a non-empty config value overrides the
CLI flag — if you want CLI control over a parameter, leave it blank in the
config.

All data paths are resolved relative to the script's working directory (i.e.
`biosamples/`, `runs/`, or `analysis/`), which is why the template values like
`BiosampleList.xlsx` resolve to e.g. `biosamples/BiosampleList.xlsx`.


## Folder layout

```
ENflorA/
├── biosamples/
│   ├── biosamples.py
│   ├── make_table.py            # builds a blank table from a checklist
│   ├── ERC000037.xml            # ENA plant checklist (the default)
│   └── BiosampleList.xlsx       # template — fill in DATA sheet
├── runs/
│   ├── runs.py
│   └── ExperimentList.xlsx      # template
├── analysis/
│   ├── analysis.py
│   └── AnalysisList.xlsx        # template
├── resolve_accessions.py        # fills new accessions into the next step's table
├── demo/                        # bundled test data for --demo mode
│   ├── config.yaml
│   ├── Demo*.xlsx
│   ├── sequences/
│   └── README.md
├── config.yaml                  # shared config
├── set_env.py                   # virtualenv setup helper
├── hpc.sh                       # SLURM job script (FU Berlin)
├── lftp_sub.sh                  # optional FTP upload helper
├── credentials.txt              # Webin username + password
└── webin-cli-*.jar              # Webin-CLI (download from ENA)
```

Each script writes its outputs into a `submission/` subdirectory within its
own folder (e.g. `biosamples/submission/`, `runs/submission/`). This keeps
generated files separate from your input data. The `submission/` directories
are gitignored.


## Script reference

### `biosamples.py`

| | |
|---|---|
| **Config keys** | `data_biosamples`, `checklist`, `sub_dir_biosamples`, `credentials`, `submit`, `live`, plus the optional `extra_mandatory`, `defaults`, `column_aliases` |
| **Input** | `BiosampleList.xlsx`, `.csv` or `.tsv`/`.tab`/`.txt` — one row per sample — and an ENA checklist XML |
| **Outputs** | `submission/biosamples.xml`, `submission/submission.xml`, `submission/biosample_accessions.txt` |
| **Submits via** | `curl` to ENA's REST API |

The columns ENflorA expects, and which of them are mandatory, come from the
checklist XML named by the `checklist` config key. The repo ships with ENA's
plant checklist [ERC000037](https://www.ebi.ac.uk/ena/browser/view/ERC000037)
configured; see [Using a different checklist](#using-a-different-checklist) for
anything else. Each sample is tagged with the accession of whichever checklist
was used, so the two can never disagree.

Accessions are appended to `biosample_accessions.txt` across runs (not
overwritten), with deduplication. A `server` column records whether each
accession came from the test or the live endpoint — test accessions do not
exist on the live server, so never copy one into a live submission.

### `make_table.py`

| | |
|---|---|
| **Config keys** | `checklist`, `data_biosamples`, `extra_mandatory` |
| **Input** | an ENA sample checklist XML |
| **Outputs** | a blank `.xlsx` (with `DATA` and `INFO` sheets), or a `.csv`/`.tsv` plus a companion `_INFO` file |
| **Submits via** | nothing — it never contacts ENA |

A one-off helper, run before `biosamples.py` when you need a table for a
checklist you have none for. Options: `--checklist`, `-o`, `--all-fields`,
`--force`. See [Using a different checklist](#using-a-different-checklist).

### `runs.py`

| | |
|---|---|
| **Config keys** | `data_runs`, `sub_dir_runs`, `credentials`, `jar`, `submit`, `live` |
| **Input** | `ExperimentList.xlsx` or `.tsv`/`.tab`/`.txt` — one row per read set, with paths to FASTQ/BAM/CRAM files |
| **Outputs** | `submission/<SAMPLE>/manifest.txt` + compressed read files per sample, `submission/run_accessions.txt` |
| **Submits via** | Webin-CLI (`-context reads`) |

Handles paired-end reads (FASTQ1 + FASTQ2 columns), single-end, BAM, and CRAM.
Already-compressed files are symlinked rather than re-compressed.
On successful submission, accessions are printed to the terminal and appended to submission/run_accessions.txt (one tab-separated line per submitted row, with a `server` column recording whether it came from the test or the live endpoint).

### `analysis.py`

| | |
|---|---|
| **Config keys** | `data_analysis`, `sub_dir_analysis`, `credentials`, `jar`, `submit`, `live`, `assembly_level`, `mingaplength` |
| **Input** | `AnalysisList.xlsx` or `.tsv`/`.tab`/`.txt` — one row per assembly, with either a `FLATFILE` (.embl/.gb) or `FASTA` column |
| **Outputs** | `submission/<SAMPLE>/manifest.txt` + compressed sequence files (+ `chr_list.txt` for chromosome-level), `submission/analysis_accessions.txt` |
| **Submits via** | Webin-CLI (`-context genome`) |

Assembly level handling:
- **Chromosome:** generates `chr_list.txt`. Defaults to a single circular
  plastid chromosome; override with `CHR_NAME`, `CHR_TYPE`, `CHR_LOCATION`
  columns in your spreadsheet.
- **Scaffold:** requires either an `AGP` column or `MINGAPLENGTH` (set per-row
  or globally in config).
- **Contig:** no extra files needed.

On successful submission, accessions are printed to the terminal and appended to submission/analysis_accessions.txt (one tab-separated line per submitted row, with a `server` column recording whether it came from the test or the live endpoint).

### `resolve_accessions.py`

| | |
|---|---|
| **Config keys** | `live` (to pick test or live accessions) |
| **Input** | a runs or analysis table, plus `biosample_accessions.txt` and/or `run_accessions.txt` |
| **Outputs** | a copy of the table with `_resolved` added to its name |
| **Submits via** | nothing — it never contacts ENA |

Optional helper that swaps temporary stand-ins (a sample's `isolate`, a read
set's `NAME`) for the accessions ENA assigned in the previous step. See
[Passing accessions between steps](#passing-accessions-between-steps).

### `hpc.sh`

SLURM job script for FU Berlin's Curta cluster. Set `ena_object` to
`biosamples`, `runs`, `analysis`, `make_table` or `resolve_accessions` inside
the script, then `sbatch hpc.sh`. For demo mode, also set `demo="true"`
(`make_table` ignores it, since it never contacts ENA). `resolve_accessions`
also needs `resolve_table` and `resolve_accession_files`, set just below; see
[Passing accessions between steps](#passing-accessions-between-steps).

It loads the necessary modules (Python 3.11, Java 21), calls `set_env.py` to
build the virtual environment, activates it, and runs the chosen script. You
don't need to install anything or call `set_env.py` yourself.

### `lftp_sub.sh` (optional)

Standalone FTP upload helper for large read files when Webin-CLI uploads are
too slow or unreliable for certain file sizes. Compresses files, generates MD5
checksums, and uploads via `lftp` to ENA's FTP drop box with automatic resume.

This script is completely independent from the three main Python scripts. It
only handles the *upload*; you still need to register the files on ENA
afterwards. The workflow is:

1. Run `lftp_sub.sh` — it uploads your files and produces a helper TSV with
   remote paths and MD5 values.
2. Go to the Webin Portal → Submit Reads → download the spreadsheet template
   for your file type.
3. Fill in the template with your study/sample accessions, library metadata,
   and the file paths + MD5s from the helper TSV.
4. Upload the filled template back to the portal.

Requirements: `bash`, `lftp`, `pigz` or `gzip`, `md5sum`.


## Using a different checklist

ENA describes every sample against a **checklist**: a list of metadata fields,
some of them mandatory, chosen to suit a kind of organism or sample. Which one
you need depends on what you are submitting. ENflorA ships configured for the
plant checklist [ERC000037](https://www.ebi.ac.uk/ena/browser/view/ERC000037),
but any ENA sample checklist works, and switching does not involve editing any
code.

If you are submitting plants, skip this section. `ERC000037.xml` and a
ready-made `BiosampleList.xlsx` are already in the repo and `config.yaml`
already points at them.

For anything else:

1. **Download your checklist.** Browse
   [ENA's checklist list](https://www.ebi.ac.uk/ena/browser/checklists), pick
   the one that fits your samples, and download its XML. Put it wherever you
   like, `biosamples/` is the obvious place.

2. **Point the config at it,** and at the table you want to create:

   ```yaml
   checklist: ERC000019.xml
   data_biosamples: MySampleList.xlsx
   ```

3. **Generate a blank table:**

   ```bash
   cd biosamples
   python make_table.py
   ```

   This writes `MySampleList.xlsx` with one column per checklist field, an
   `INFO` sheet documenting every field, and a `[unit]` column beside each
   field that takes a unit. Fill a `[unit]` cell in and that unit is used. Leave
   it empty and the checklist's own unit is used, where the checklist offers
   exactly one. Fields offering a choice of units (say `mm` or `m`) and left
   empty are submitted without a unit rather than guessed at, so fill those in.

   `make_table.py` will not overwrite an existing file; use `-o` to write
   elsewhere, or `--force` if you really mean it. By default only the
   mandatory and recommended fields become columns, since a large checklist can
   have over a hundred; `--all-fields` includes the optional ones too, and any
   field left out is still listed on `INFO` so you can add it as a column
   yourself.

4. **Fill it in,** one row per sample.

5. **Submit as usual:**

   ```bash
   python biosamples.py
   ```

   `biosamples.py` reads the same checklist XML, checks your table against it,
   and tags each sample with the right checklist accession automatically.

### Three columns you will not find in any checklist

Every ENA sample needs these regardless of checklist, because they belong to
ENA's sample record rather than to the checklist's field list. `make_table.py`
always puts them first:

| Column | Becomes |
|---|---|
| `isolate` | the sample alias — must be unique within your Webin account |
| `organism` | the sample title |
| `taxon_id` | the [NCBI taxonomy](https://www.ncbi.nlm.nih.gov/taxonomy) ID |

### What is checked, and what is not

Mandatory fields must be present and filled in for every sample, or the run
stops. Missing recommended fields produce a note. Empty recommended and
optional cells are simply not submitted. Any column that matches no checklist
field is submitted as a free sample attribute, which is how ENA handles extras
like `bio_material`.

Two samples sharing an `isolate` stops the run, naming both rows, since ENA
rejects a submission carrying the same alias twice.

ENflorA does not check value formats or controlled vocabularies. ENA's own
validator does that when you submit, so a table ENflorA accepts can still be
rejected by ENA.

### Keeping your own column names and rules

Three optional config keys let a group keep its own conventions without
touching the code. All three are how the bundled plant setup works, so
`config.yaml` doubles as a worked example.

```yaml
extra_mandatory:                  # required by you, though ENA calls them optional
  - bio_material
  - altitude

defaults:                         # used when a cell is left empty
  plant growth medium: soil

column_aliases:                   # your column name -> checklist field
  latitude:  geographic location (latitude)
  longitude: geographic location (longitude)
  country:   geographic location (country and/or sea)
  locality:  geographic location (region and locality)
  region:    geographic location (region and locality)
```

A field with a `defaults` entry never blocks a submission, even if it is
mandatory and even if its column is missing entirely. Two aliases may point at
the same checklist field, in which case the values are joined with `", "` in
the order listed, which is how `locality` and `region` become one ENA field.
Aliases only apply when the source column is actually present, so leaving a
stale block in the config after switching checklists does no harm.

If your group will submit under the same checklist for years, consider building
a proper template by hand, as `BiosampleList.xlsx` is: descriptions, examples,
and column names your people already recognise, kept stable through
`column_aliases`. A generated table is the fast path, not necessarily the best
long-term one.

### One other plant default

`analysis.py` assumes a single circular plastid chromosome for chromosome-level
submissions. For nuclear or other chromosomes, add `CHR_NAME`, `CHR_TYPE` and
`CHR_LOCATION` columns to your analysis table; their values are written
straight into `chr_list.txt`.

`runs.py` needs no changes for any organism.


## Passing accessions between steps

A run has to point at a registered sample, and an analysis at a registered
sample and run. When you submit everything from scratch, those accessions only
exist once the previous step is done, so you can't type them into the next
table in advance. Instead, write a **temporary stand-in** and let
`resolve_accessions.py` swap in the real accession afterwards.

This is optional. If the samples or reads you are referencing are already in
ENA, just type their accessions in as usual.

### What to write as a stand-in

| Column | Before the accession exists, write… | Filled in from |
|---|---|---|
| `SAMPLE` (runs and analysis tables) | the sample's `isolate`, exactly as in your biosamples table | `biosamples/submission/biosample_accessions.txt` |
| `RUN_REF` (analysis table) | the read set's `NAME`, exactly as in your runs table | `runs/submission/run_accessions.txt` |

Anything that already looks like an ENA accession (`SAMEA…`, `ERS…`, `ERR…`,
and so on) is left untouched, so one table can mix new samples with ones that
were already in ENA. `RUN_REF` may list several read sets separated by commas;
each is resolved on its own. Matching ignores upper/lower case and spaces
around the value.

The accession files sit in each script's submission folder: `submission/` by
default, whatever you set as `sub_dir_biosamples` / `sub_dir_runs` otherwise,
or `demo_submission/` in demo mode.

### Running it on your own machine

Run everything from the ENflorA folder, with the environment active
(`python set_env.py -r`, see [Requirements](#requirements)):

```bash
# 1. Register the samples
cd biosamples && python biosamples.py && cd ..

# 2. Fill the new sample accessions into the runs table
python resolve_accessions.py \
    --table runs/ExperimentList.xlsx \
    --accessions biosamples/submission/biosample_accessions.txt
```

Your table is never changed. The script writes a copy next to it,
`runs/ExperimentList_resolved.xlsx`, and ends by telling you what to change in
`config.yaml`:

```
Updated table written to: runs/ExperimentList_resolved.xlsx
Next: in config.yaml, set
    data_runs: ExperimentList_resolved.xlsx
then run runs.py as usual.
```

Change that line in `config.yaml` rather than passing the file with `-c`,
since a path set in the config takes precedence over `-c`. Then carry on:

```bash
# 3. Submit the reads (data_runs now points at the resolved table)
cd runs && python runs.py && cd ..

# 4. The analysis table needs both files: samples for SAMPLE, runs for RUN_REF
python resolve_accessions.py \
    --table analysis/AnalysisList.xlsx \
    --accessions biosamples/submission/biosample_accessions.txt \
    --accessions runs/submission/run_accessions.txt

# 5. Set data_analysis: AnalysisList_resolved.xlsx in config.yaml, then
cd analysis && python analysis.py
```

### Running it on the HPC

With `hpc.sh`, set these at the top of the script:

```bash
ena_object="resolve_accessions"
demo="false"
resolve_table="runs/ExperimentList.xlsx"
resolve_accession_files="biosamples/submission/biosample_accessions.txt"
```

For an analysis table, list both files, separated by a space:

```bash
resolve_table="analysis/AnalysisList.xlsx"
resolve_accession_files="biosamples/submission/biosample_accessions.txt runs/submission/run_accessions.txt"
```

Then `sbatch hpc.sh` as for the other scripts. The "Next: in config.yaml, set…"
line ends up in `logs/ENflorA_<jobid>.out`. Since it takes seconds and never
contacts ENA, you can also run it straight from the login node with
`bash hpc.sh`, and the output appears in your terminal.

To run it by hand on Curta instead, load Python first (it is not available
until you do), then activate the environment:

```bash
module load Python/3.11.3-GCCcore-12.3.0
python set_env.py -s -H          # only needed the first time
source env/bin/activate
python resolve_accessions.py --table runs/ExperimentList.xlsx \
    --accessions biosamples/submission/biosample_accessions.txt
```

### Test and live accessions

Accessions from ENA's test server don't exist on the live one, so the
accession files record which server each one came from. `resolve_accessions.py`
only uses the ones matching `live` in `config.yaml`: test accessions unless
`live: True`. Pass `--server test` or `--server live` to choose explicitly.
With `hpc.sh`, `demo="true"` always uses test accessions.

### When something can't be resolved

The script stops and writes nothing, listing every value it couldn't match,
for example:

```
SAMPLE, row 3: 'AST1235' is neither an ENA accession nor in the accession file(s)
SAMPLE, row 4: 'AST1236' was registered on the live server (as ERS123), not on test
```

Usually the previous step hasn't been submitted yet, or the stand-in is spelled
differently from the `isolate` or `NAME` it was submitted under. A failed
submission never adds anything to the accession files, so a rejected sample
can't slip through. To write the table anyway, leaving those cells as they
are, add `--allow-missing`.

### Options

| Flag | Meaning |
|---|---|
| `--table` | Runs or analysis table to fill in (`.xlsx`, `.csv`, `.tsv`/`.tab`/`.txt`) |
| `--accessions` | Accession file from the previous step; give it twice to use both |
| `-o`, `--output` | Where to write the result (default: next to the input, `_resolved` added) |
| `--server` | `test` or `live` (default: from `live` in `config.yaml`) |
| `--allow-missing` | Write the table even if some values couldn't be resolved |
| `--force` | Overwrite an existing output file |
| `--column` | Fill a different column than `SAMPLE` / `RUN_REF` (one accession file only) |

## Logs and receipts

All scripts write logs to a `logs/` directory and assigned accessions to a
plain-text file inside their `submission/` folder. The accessions files are
the easiest way to find the IDs you'll need for the next step:

- `biosamples/submission/biosample_accessions.txt` — SAMEA accessions
- `runs/submission/run_accessions.txt` — ERX (experiment) and ERR (run) accessions
- `analysis/submission/analysis_accessions.txt` — ERZ accessions

All three files are appended to across runs (not overwritten), with
deduplication and a `(test)` suffix for test-server submissions. Accessions
are also printed to the terminal as soon as they're assigned, so on the HPC
you'll see them in `logs/ENflorA_<jobid>.out`.

`runs.py` and `analysis.py` additionally create per-sample subfolders under
`logs/` (containing the full Webin-CLI report and validation files) and
automatically clean stale validation caches before each submission.

It's safe to delete `logs/` entirely to start fresh.
