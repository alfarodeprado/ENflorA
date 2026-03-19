# Demo data for ENflorA

This folder has everything you need to do a dry run of the full ENflorA
workflow using synthetic data. The idea is simple: run all three scripts with
`--demo`, check that nothing breaks, and then you know your setup (credentials,
Java, Webin-CLI, Python dependencies) is good to go for real data.

All sequence files here are random synthetic DNA. The ENA test server checks
that the format is valid but doesn't care about biological meaning, and it
automatically deletes test submissions after a few days.

## Before you start

You'll need:

- Valid Webin credentials in `credentials.txt` at the repo root. The test
  server uses the same login as the live one.
- A **test study** created on the ENA test Webin Portal:
  https://wwwdev.ebi.ac.uk/ena/submit/webin/

  Note the study accession you get (something like `PRJEB99999`).

## Step-by-step walkthrough

The steps **must be run in this order**, because each one produces accession
IDs that the next step needs. This is the same as a real submission.

### Step 1 — Register demo biosamples

Open `demo/DemoBiosampleList.xlsx` and change the `isolate` field (cell A2) to
something unique — ENA rejects duplicate aliases even on the test server.
Appending today's date works fine (e.g. `ENflorA_demo_20260319`).

Then run:

```bash
cd biosamples
python biosamples.py --demo
```

Or, if you're on the HPC, set `ena_object="biosamples"` and `demo="true"` in
`hpc.sh`, then `sbatch hpc.sh`.

Look for the SAMEA accession in the output. You'll need it for the next two
steps: open `demo/DemoExperimentList.xlsx` and `demo/DemoAnalysisList.xlsx`
and paste it into the SAMPLE column (cell B2; for the analysis sheet, paste
it into B2 **and** B3, since it has two rows).

### Step 2 — Submit demo reads

Make sure you've pasted your test study accession into cell A2 of
`demo/DemoExperimentList.xlsx` (and the SAMEA from step 1 into B2).

```bash
cd runs
python runs.py --demo
```

Or HPC: `ena_object="runs"`, `demo="true"`.

Look for the ERR accession in the output. Open `demo/DemoAnalysisList.xlsx`
and paste it into the RUN_REF column (cells C2 and C3).

### Step 3 — Submit demo assemblies

This step needs accessions from **both** previous steps (SAMEA from step 1,
ERR from step 2), so make sure you've filled those in first.

The demo analysis spreadsheet has two rows to test both supported file types:
row 1 submits an unannotated FASTA, row 2 submits an annotated EMBL flat file.

```bash
cd analysis
python analysis.py --demo
```

Or HPC: `ena_object="analysis"`, `demo="true"`.

If all three steps complete without ENA errors, your setup works. You're
ready to plug in real data.

## About the verbose output

When you use `--demo`, the scripts print detailed `[demo]` trace lines showing
what's happening at each step: which config file was loaded, what the
spreadsheet contains, how files are being staged and compressed, what endpoint
is being used, and so on. This can look like a lot of text, but that's the
point — it's there to help you see exactly where things go wrong if they do.

None of these `[demo]` lines appear during normal (non-demo) operation.

## What's in this folder

```
demo/
├── config.yaml                  # Config used by --demo (don't edit unless you know what you're doing)
├── DemoBiosampleList.xlsx       # One Arabidopsis thaliana sample
├── DemoExperimentList.xlsx      # One paired-end Illumina MiSeq read set
├── DemoAnalysisList.xlsx        # Two rows: one FASTA + one EMBL flat file
├── sequences/
│   ├── demo_reads_1.fastq.gz   # 100 synthetic forward reads (150 bp)
│   ├── demo_reads_2.fastq.gz   # 100 synthetic reverse reads (150 bp)
│   ├── demo_assembly.fasta     # 2 synthetic contigs (~500 bp each)
│   └── demo_annotation.embl    # Same 2 contigs as annotated EMBL flat file
└── README.md                   # You are here
```

## Spreadsheet details

**DemoBiosampleList.xlsx** — One sample, all mandatory fields for the ENA
plant checklist (ERC000037) pre-filled. The only thing you *must* change is
the `isolate` (cell A2) to avoid alias collisions.

**DemoExperimentList.xlsx** — One paired-end read row. Yellow-highlighted
cells (STUDY, SAMPLE) are placeholders that need real accessions. The FASTQ
paths already point to the bundled files.

**DemoAnalysisList.xlsx** — Two rows testing both analysis input types:

- Row 1 uses the FASTA file (unannotated contigs)
- Row 2 uses the EMBL flat file (annotated contigs)

Both rows reference the same STUDY, SAMPLE and RUN_REF accessions (yellow
placeholders). The script automatically handles the duplicate sample ID by
creating a `_2` suffix for the second row's submission folder.

The demo config sets `assembly_level: contig`, so no chromosome list file is
generated. Both the FASTA and EMBL files contain 2 sequences, which is the
minimum ENA requires for contig-level submissions.
