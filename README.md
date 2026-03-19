# ENflorA – Bulk submission of sequencing data to ENA

ENflorA is a small set of Python scripts that help you submit biological
sequencing data to the [European Nucleotide Archive (ENA)](https://www.ebi.ac.uk/ena/browser/home).
You fill in spreadsheets (Excel or TSV) with your metadata, point the scripts
at your sequence files, and ENflorA handles XML generation, file compression,
manifest creation, and submission.

It was originally built for plastid genome projects, but works for any organism
with minor adjustments (see [Adapting to non-plant data](#adapting-to-non-plant-data)).

For background on ENA's object types and metadata model, see the
[ENA submission documentation](https://ena-docs.readthedocs.io/en/latest/submit/general-guide/metadata.html).


## Index

- [How it works](#how-it-works)
- [Try it first: demo mode](#try-it-first-demo-mode)
- [Requirements](#requirements)
- [Installation and setup](#installation-and-setup)
- [Configuration (`config.yaml`)](#configuration-configyaml)
- [Folder layout](#folder-layout)
- [Script reference](#script-reference)
  - [`biosamples.py`](#biosamplespy)
  - [`runs.py`](#runspy)
  - [`analysis.py`](#analysispy)
  - [`hpc.sh`](#hpcsh)
  - [`lftp_sub.sh` (optional)](#lftp_subsh-optional)
- [Adapting to non-plant data](#adapting-to-non-plant-data)
- [Logs and receipts](#logs-and-receipts)


## How it works

ENflorA mirrors ENA's own data model. There are three scripts, each handling
one type of ENA object, and **they must be run in this order** because each
step produces accession IDs that the next step needs:

```
 1. Create a Study on ENA         (manual, one-time, via the Webin Portal)
         ↓ study accession
 2. biosamples.py                 → registers your samples, returns SAMEA* accessions
         ↓ sample accessions
 3. runs.py                       → uploads your raw reads, returns ERR* accessions
         ↓ run accessions
 4. analysis.py                   → uploads your assemblies/annotations
```

You don't have to run all three. If your samples are already registered on ENA,
skip `biosamples.py` and put the existing SAMEA accessions directly into your
runs spreadsheet. Same for analysis — if your reads are already in ENA, just
reference those run accessions.

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
an `INFO` sheet explaining every column. If you prefer plain text, the scripts
also accept tab-separated files (`.tsv`, `.tab`, or `.txt`) with the same
column headers.


## Try it first: demo mode

Before using real data, you can do a complete dry run with bundled synthetic
sequences. This verifies that your setup — credentials, Python, Java,
Webin-CLI — is working end-to-end.

```bash
cd biosamples && python biosamples.py --demo
cd runs      && python runs.py --demo
cd analysis  && python analysis.py --demo
```

Demo mode submits to ENA's test server (data gets auto-deleted after a few
days), prints verbose `[demo]` trace lines so you can see exactly what's
happening, and is hardcoded to never touch the live server.

See [`demo/README.md`](demo/README.md) for the full step-by-step walkthrough,
including how to run it from the HPC.


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
cd biosamples && python biosamples.py
```
On another HPC with `module` support, add `-H` to load Java:
`python set_env.py -s -r -H`
(You may need to edit the `module add Java/21.0.5` line in `set_env.py` to
match your cluster.)

**Option C — Manual:**
```bash
pip install pandas openpyxl biopython pyyaml
cd biosamples && python biosamples.py
```

### Credentials

Put your Webin login in `credentials.txt` at the repo root (two lines:
username, then password). The file ships with placeholder values — replace
them with your own. You can also pass credentials via `-u` / `-p` flags.


## Configuration (`config.yaml`)

All three scripts share a single YAML config in the repo root:

```yaml
credentials: ../credentials.txt
jar: ../webin-cli-8.2.0.jar

# Paths to input tables. Can be .xlsx, .xls, .tsv, .tab, or .txt
data_biosamples: BiosampleList.xlsx
data_runs:       ExperimentList.xlsx
data_analysis:   AnalysisList.xlsx

submit: True
live:   False

sub_dir_biosamples:               # where biosamples XMLs and accessions go
sub_dir_runs:                     # where runs submission folders go
sub_dir_analysis:                 # where analysis submission folders go

assembly_level: chromosome        # contig | scaffold | chromosome
mingaplength: 50                  # used only if scaffold & no AGP
```

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
│   └── BiosampleList.xlsx       # template — fill in DATA sheet
├── runs/
│   ├── runs.py
│   └── ExperimentList.xlsx      # template
├── analysis/
│   ├── analysis.py
│   └── AnalysisList.xlsx        # template
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
| **Config keys** | `data_biosamples`, `sub_dir_biosamples`, `credentials`, `submit`, `live` |
| **Input** | `BiosampleList.xlsx` or `.tsv`/`.tab`/`.txt` — one row per sample |
| **Outputs** | `submission/biosamples.xml`, `submission/submission.xml`, `submission/biosample_accessions.txt` |
| **Submits via** | `curl` to ENA's REST API |

The biosamples spreadsheet uses ENA's plant checklist
[ERC000037](https://www.ebi.ac.uk/ena/browser/view/ERC000037). See [Adapting
to non-plant data](#adapting-to-non-plant-data) if you're working with other
organisms.

Accessions are appended to `biosample_accessions.txt` across runs (not
overwritten), with deduplication and a `(test)` suffix for test-server
submissions.

### `runs.py`

| | |
|---|---|
| **Config keys** | `data_runs`, `sub_dir_runs`, `credentials`, `jar`, `submit`, `live` |
| **Input** | `ExperimentList.xlsx` or `.tsv`/`.tab`/`.txt` — one row per read set, with paths to FASTQ/BAM/CRAM files |
| **Outputs** | `submission/<SAMPLE>/manifest.txt` + compressed read files per sample |
| **Submits via** | Webin-CLI (`-context reads`) |

Handles paired-end reads (FASTQ1 + FASTQ2 columns), single-end, BAM, and CRAM.
Already-compressed files are symlinked rather than re-compressed.

### `analysis.py`

| | |
|---|---|
| **Config keys** | `data_analysis`, `sub_dir_analysis`, `credentials`, `jar`, `submit`, `live`, `assembly_level`, `mingaplength` |
| **Input** | `AnalysisList.xlsx` or `.tsv`/`.tab`/`.txt` — one row per assembly, with either a `FLATFILE` (.embl/.gb) or `FASTA` column |
| **Outputs** | `submission/<SAMPLE>/manifest.txt` + compressed sequence files (+ `chr_list.txt` for chromosome-level) |
| **Submits via** | Webin-CLI (`-context genome`) |

Assembly level handling:
- **Chromosome:** generates `chr_list.txt`. Defaults to a single circular
  plastid chromosome; override with `CHR_NAME`, `CHR_TYPE`, `CHR_LOCATION`
  columns in your spreadsheet.
- **Scaffold:** requires either an `AGP` column or `MINGAPLENGTH` (set per-row
  or globally in config).
- **Contig:** no extra files needed.

### `hpc.sh`

SLURM job script for FU Berlin's Curta cluster. Set `ena_object` to
`biosamples`, `runs`, or `analysis` inside the script, then `sbatch hpc.sh`.
For demo mode, also set `demo="true"`.

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


## Adapting to non-plant data

Most of the pipeline is organism-agnostic. Two things have plant-specific
defaults:

1. **`biosamples.py`** uses ENA checklist
   [ERC000037](https://www.ebi.ac.uk/ena/browser/view/ERC000037) and expects
   plant-specific columns (`plant structure`, `plant developmental stage`,
   etc.). To adapt, modify the `expected_fields`, `mandatory`, and
   `recommended` lists in `biosamples.py`, and change the `ENA-CHECKLIST`
   value to match your target checklist.

2. **`analysis.py`** defaults to a single circular plastid chromosome for
   chromosome-level submissions. To use nuclear or other chromosomes, add
   `CHR_NAME`, `CHR_TYPE`, and `CHR_LOCATION` columns to your analysis
   spreadsheet — their values are written directly into `chr_list.txt`.

`runs.py` requires no changes for any organism.


## Logs and receipts

All scripts write logs to a `logs/` directory:

- `biosamples.py` saves XML receipts and appends accessions to
  `submission/biosample_accessions.txt`.
- `runs.py` and `analysis.py` create per-sample subfolders under `logs/`
  and automatically clean stale validation caches before each submission.

It's safe to delete `logs/` entirely to start fresh.
