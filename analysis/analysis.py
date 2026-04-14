# Downloadable libraries
import pandas as pd
import yaml

# Standard libraries
import os
import sys
import argparse
import gzip
import subprocess
import shutil
import glob
from collections import defaultdict
from typing import Optional
import xml.etree.ElementTree as ET

# --- Verbose logging (enabled by --demo) ---
VERBOSE = False
def vlog(msg):
    """Print a verbose/debug message when running in demo mode."""
    if VERBOSE:
        print(f"  [demo] {msg}")

def load_config(cfg_path: str = "../config.yaml") -> dict:
    """
    Return a dict with the YAML content or an empty dict if the file is absent.
    """
    if os.path.exists(cfg_path):
        vlog(f"Loading config from: {os.path.abspath(cfg_path)}")
        with open(cfg_path, "r") as fh:
            cfg = yaml.safe_load(fh) or {}
        for k, v in cfg.items():
            vlog(f"  {k}: {v}")
        return cfg
    vlog(f"Config file not found at {cfg_path}, using defaults")
    return {}

def load_table(path: str, case: str = "upper"):
    """
    Read first-sheet Excel (.xlsx/.xls) or TSV (.tsv/.tab/.txt) into a DataFrame.
    Normalizes header whitespace and case.
    """
    vlog(f"Reading table: {os.path.abspath(path)}")
    ext = os.path.splitext(path)[1].lower()
    if ext in {".xlsx", ".xls"}:
        df = pd.read_excel(path, sheet_name=0)
    elif ext in {".tsv", ".tab", ".txt"}:
        df = pd.read_csv(path, sep="\t")
    else:
        sys.exit(f"Unsupported table extension '{ext}'. Use .xlsx/.xls or .tsv/.tab/.txt")

    df.columns = df.columns.str.strip()
    if case == "upper":
        df.columns = df.columns.str.upper()
    elif case == "lower":
        df.columns = df.columns.str.lower()
    vlog(f"Table loaded: {len(df)} rows, columns: {list(df.columns)}")
    return df

def stage_file(src_path: str, dest_dir: str, mode: str = "cp") -> str:
    """
    Stage `src_path` into `dest_dir`.

    mode = "cp"  -> copy into dest_dir as <basename>; returns <basename>
    mode = "cmp" -> gzip into dest_dir as <basename>.gz; returns <basename>.gz
    """
    if mode not in {"cp", "cmp"}:
        sys.exit(f"stage_file: invalid mode '{mode}', use 'cp' or 'cmp'")

    src = os.path.abspath(src_path)
    if not os.path.exists(src):
        sys.exit(f"File not found: {src}")

    os.makedirs(dest_dir, exist_ok=True)

    if mode == "cp":
        dst = os.path.join(dest_dir, os.path.basename(src))
        if os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy(src, dst)
        vlog(f"  Staged (copy): {src} -> {dst}")
        return os.path.basename(dst)

    # mode == "cmp"
    dst_gz = os.path.join(dest_dir, os.path.basename(src) + ".gz")
    with open(src, "rb") as f_in, gzip.open(dst_gz, "wb") as f_out:
        shutil.copyfileobj(f_in, f_out, length=1024 * 1024)
    vlog(f"  Staged (compress): {src} -> {dst_gz}")
    return os.path.basename(dst_gz)

def extract_first_accession(path):
    opener = gzip.open if path.lower().endswith(".gz") else open
    with opener(path, "rt") as f:
        for line in f:
            if line.startswith(">"):
                return line[1:].strip().split()[0]
            if line.startswith("AC"):
                body = line[2:].strip().rstrip(";")
                parts = body.split()
                if parts and parts[0] == "*" and len(parts) > 1:
                    return parts[1]
                return parts[0]
    sys.exit(f"Error: no accession line ('AC' or '>') found in {path}")

def _norm_level(x: Optional[str]) -> str:
    if not x:
        return "chromosome" # backward-compatible default
    x = str(x).strip().lower()
    if x in {"chr", "chrom", "chromosome", "organellar"}:
        return "chromosome"
    if x in {"scaffold", "scaffolds", "scf"}:
        return "scaffold"
    if x in {"contig", "contigs"}:
        return "contig"
    raise SystemExit(f"Unknown ASSEMBLY_LEVEL '{x}'. Use contig|scaffold|chromosome.")

def has_n_gaps(fasta_path, n=50):
    prev = ""  # carryover across lines
    opener = gzip.open if fasta_path.endswith(".gz") else open
    with opener(fasta_path, "rt") as fh:
        for line in fh:
            if line.startswith(">"):
                prev = ""
                continue
            s = (prev + line.rstrip()).upper()
            if f"N{'N'*(n-1)}" in s:  # simple fast check
                return True
            # keep tail of last n-1 chars to bridge line breaks
            prev = s[-(n-1):]
    return False

def convert_manifests(table_file: str, submission_dir: str = "submission", default_level: str = "chromosome", default_mingaplength: Optional[int] = None,) -> list:
    """
    Convert the analysis table (Excel/TSV) to per-sample Webin-CLI submission folders.
    ...
    """
    # Load table (UPPERCASE headers expected by this script)
    df = load_table(table_file, case="upper")
    sample_counts = defaultdict(int)

    # Required metadata fields (stay strict to keep templates consistent)
    required = [
        "STUDY", "SAMPLE", "RUN_REF", "ASSEMBLYNAME", "ASSEMBLY_TYPE",
        "COVERAGE", "PROGRAM", "PLATFORM", "MOLECULETYPE", "DESCRIPTION",
        # one of FLATFILE | FASTA will be used below
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        sys.exit(f"Missing columns in table: {', '.join(missing)}")

    # Optional columns the user may provide
    optional_cols = {c.upper() for c in df.columns}
    has_agp_col = "AGP" in optional_cols
    has_mingap_col = "MINGAPLENGTH" in optional_cols
    has_level_col = "ASSEMBLY_LEVEL" in optional_cols
    # Optional chromosome customisation
    has_chr_name = "CHR_NAME" in optional_cols
    has_chr_type = "CHR_TYPE" in optional_cols
    has_chr_loc  = "CHR_LOCATION" in optional_cols

    os.makedirs(submission_dir, exist_ok=True)
    vlog(f"Submission directory: {os.path.abspath(submission_dir)}")
    vlog(f"Default assembly level: {default_level}, mingaplength: {default_mingaplength}")
    manifest_paths = []

    for idx, row in df.iterrows():
        n = idx + 1

        # In case there is more than one object associated with the same sample
        raw_id = str(row["SAMPLE"]).strip()
        sample_counts[raw_id] += 1
        sample_id = raw_id if sample_counts[raw_id] == 1 else f"{raw_id}_{sample_counts[raw_id]}"

        samp_dir = os.path.join(submission_dir, sample_id)
        os.makedirs(samp_dir, exist_ok=True)
        vlog(f"Row {n}: sample={sample_id}, study={row['STUDY']}, run_ref={row['RUN_REF']}")

        # Data file: exactly one of FLATFILE or FASTA
        flat = str(row.get("FLATFILE", "")).strip()
        fasta = str(row.get("FASTA", "")).strip()
        has_flat = bool(flat) and flat.lower() != "nan"
        has_fasta = bool(fasta) and fasta.lower() != "nan"
        if has_flat == has_fasta:
            sys.exit(f"Row {n}: exactly one of FLATFILE or FASTA must be set")

        if has_flat:
            path_in = os.path.abspath(flat)
            ext = os.path.splitext(path_in)[1].lower()
            vlog(f"  Data source: FLATFILE={flat} (ext={ext})")
            if ext == ".gb":
                vlog(f"  Converting GenBank -> EMBL via Biopython")
                try:
                    from Bio import SeqIO  # lazy import
                except ImportError:
                    sys.exit(
                        "Error: converting .gb → .embl requires Biopython;\n"
                        " please install biopython or supply an .embl flatfile."
                    )
                stem = os.path.splitext(os.path.basename(flat))[0]
                embl_path = os.path.join(samp_dir, stem + ".embl")
                recs = SeqIO.parse(path_in, "genbank")
                count = SeqIO.write(recs, embl_path, "embl")
                if count == 0:
                    sys.exit(f"Row {n}: no records written converting {path_in}")
                print(f"[Row {n}] Converted {path_in} → {embl_path} ({count} recs)")
                seqname = extract_first_accession(embl_path)
                vlog(f"  Extracted accession from EMBL: {seqname}")
                data_field = ("FLATFILE", stage_file(embl_path, samp_dir, mode="cmp"))

            elif ext == ".embl":
                seqname = extract_first_accession(path_in)
                vlog(f"  Extracted accession from EMBL: {seqname}")
                data_field = ("FLATFILE", stage_file(path_in, samp_dir, mode="cmp"))
            else:
                sys.exit(f"Row {n}: FLATFILE must end in .gb or .embl, not '{ext}'")
        else:
            src = os.path.abspath(fasta)
            vlog(f"  Data source: FASTA={fasta}")
            seqname = extract_first_accession(src)            # read the original to get the first header
            vlog(f"  Extracted accession from FASTA header: {seqname}")
            data_field = ("FASTA", stage_file(src, samp_dir, mode="cmp"))

        # Determine assembly level
        level = _norm_level(row.get("ASSEMBLY_LEVEL") if has_level_col else default_level)
        vlog(f"  Assembly level: {level}")

        # Prepare optional files/fields depending on level
        agp_field = None
        chrlist_field = None
        mingap_value: Optional[int] = None

        if level == "chromosome":
            # Allow user-provided CHR_* columns, else fall back to plastid-friendly default
            chr_name = str(row.get("CHR_NAME", "1")).strip() if has_chr_name else "1"
            chr_type = str(row.get("CHR_TYPE", "Circular-Chromosome")).strip() if has_chr_type else "Circular-Chromosome"
            chr_loc  = str(row.get("CHR_LOCATION", "Plastid")).strip() if has_chr_loc else "Plastid"
            vlog(f"  Chromosome list: name={chr_name}, type={chr_type}, location={chr_loc}")

            chr_txt = os.path.join(samp_dir, "chr_list.txt")
            with open(chr_txt, "w") as f:
                f.write(f"{seqname}\t{chr_name}\t{chr_type}\t{chr_loc}")
            chr_gz = stage_file(chr_txt, samp_dir, mode="cmp")
            chrlist_field = ("CHROMOSOME_LIST", chr_gz)

        elif level == "scaffold":
            # Either AGP or MINGAPLENGTH must be provided
            agp_val = str(row.get("AGP", "")).strip() if has_agp_col else ""
            if agp_val and agp_val.lower() != "nan":
                agp_field = ("AGP", stage_file(agp_val, samp_dir, mode="cmp"))
            else:
                # No AGP: require MINGAPLENGTH either in Excel or default from config
                if has_mingap_col:
                    raw_mg = row.get("MINGAPLENGTH")
                    if pd.notnull(raw_mg) and str(raw_mg).strip() != "":
                        try:
                            mingap_value = int(raw_mg)
                        except ValueError:
                            sys.exit(f"Row {n}: MINGAPLENGTH must be an integer (got '{raw_mg}')")
                if mingap_value is None:
                    if default_mingaplength is None:
                        sys.exit(
                            f"Row {n}: scaffold-level requires AGP or MINGAPLENGTH (set column or config.default_mingaplength)."
                        )
                    mingap_value = int(default_mingaplength)

        elif level == "contig":
            pass  # nothing extra
        else:
            raise SystemExit(f"Internal error: unexpected level '{level}'")
        
        # Decide which source file to scan for Ns
        seq_src = None
        if has_fasta:
            seq_src = src  # scan the original FASTA

        # Only warn for scaffold-level with implicit Ns (no AGP)
        if level == "scaffold" and not agp_field:
            threshold = (mingap_value if mingap_value is not None else default_mingaplength or 50)
            if seq_src and not has_n_gaps(seq_src, threshold):
                print(f"[Row {n}] WARNING: no N runs ≥ {threshold} found; this looks contig-level.")

        # Write manifest.txt
        mf = os.path.join(samp_dir, "manifest.txt")
        with open(mf, "w") as fh:
            fh.write(f"STUDY\t{row['STUDY']}\n")
            fh.write(f"SAMPLE\t{row['SAMPLE']}\n")
            fh.write(f"RUN_REF\t{row['RUN_REF']}\n")
            fh.write(f"ASSEMBLYNAME\t{row['ASSEMBLYNAME']}\n")
            fh.write(f"ASSEMBLY_TYPE\t{row['ASSEMBLY_TYPE']}\n")
            fh.write(f"COVERAGE\t{row['COVERAGE']}\n")
            fh.write(f"PROGRAM\t{row['PROGRAM']}\n")
            fh.write(f"PLATFORM\t{row['PLATFORM']}\n")
            # MINGAPLENGTH is only meaningful for scaffold level when using Ns
            if mingap_value is not None:
                fh.write(f"MINGAPLENGTH\t{mingap_value}\n")
            fh.write(f"MOLECULETYPE\t{row['MOLECULETYPE']}\n")
            if pd.notnull(row.get("DESCRIPTION")) and str(row.get("DESCRIPTION")).strip().lower() != "nan":
                fh.write(f"DESCRIPTION\t{row['DESCRIPTION']}\n")
            # Data file
            fh.write(f"{data_field[0]}\t{data_field[1]}\n")
            # Optional files according to level
            if agp_field:
                fh.write(f"{agp_field[0]}\t{agp_field[1]}\n")
            if chrlist_field:
                fh.write(f"{chrlist_field[0]}\t{chrlist_field[1]}\n")
        print(f"[Row {n}] Wrote manifest → {mf} (level={level})")
        manifest_paths.append(mf)

    return manifest_paths

def prepare_logs_dir(logs_dir="logs"):
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
        print(f"Directory '{logs_dir}' created.")
    else:
        print(f"Directory '{logs_dir}' already exists.")
    return logs_dir

def load_credentials(path):
    vlog(f"Loading credentials from: {os.path.abspath(path)}")
    if not os.path.isfile(path):
        sys.exit(f"Credentials file not found: {path}")
    lines = [l.strip() for l in open(path) if l.strip()]
    if len(lines) < 2:
        sys.exit(f"{path} must have username on line 1 and password on line 2")
    return lines[0], lines[1]

def find_jar(jar_arg):
    if jar_arg:
        if os.path.isfile(jar_arg):
            vlog(f"Using Webin-CLI JAR: {os.path.abspath(jar_arg)}")
            return jar_arg
        sys.exit(f"JAR not found at {jar_arg}")
    webin = [m for m in glob.glob("*.jar") if "webin-cli" in m]
    if len(webin) == 1:
        return webin[0]
    sys.exit("Auto-detect failed; pass --jar /path/to/webin-cli.jar")

def drop_cached_validation(log_subdir: str):
    """
    Delete logs/<sample_id>/genome/*/validate.json without deleting the whole log dir, forcing Webin-CLI to recalculate MD5s on the next run.
    """
    pattern = os.path.join(log_subdir, "genome", "*", "validate.json")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
            print(f"  Removed cached validation → {path}")
        except OSError as exc:
            print(f"  Could not remove {path}: {exc}")

def parse_analysis_receipt(log_subdir):
    """
    Find and parse the Webin-CLI receipt.xml in a sample's log subdir.
    Returns (success, [(analysis_acc, alias), ...]) or (None, []) if not found.
    """
    receipts = glob.glob(os.path.join(log_subdir, "genome", "*", "submit", "receipt.xml"))
    if not receipts:
        return None, []
    try:
        tree = ET.parse(receipts[0])
        root = tree.getroot()
        success = root.attrib.get('success', 'false')
        records = []
        for a in root.findall('ANALYSIS'):
            records.append((a.attrib.get('accession'), a.attrib.get('alias')))
        return success, records
    except Exception as e:
        print(f"  Could not parse receipt {receipts[0]}: {e}")
        return None, []

def append_analysis_accessions(records, sub_dir, live):
    """
    Append analysis accessions to submission/analysis_accessions.txt.
    Deduplicates and adds a (test) suffix for test-server submissions.
    """
    if not records:
        return
    out_file = os.path.join(sub_dir, "analysis_accessions.txt")
    write_mode = "a+" if os.path.exists(out_file) else "w+"
    with open(out_file, write_mode) as out:
        out.seek(0)
        existing = {l.strip() for l in out if l.strip()}
        if not existing:
            out.write("analysis_accession\talias\n")
        for acc, alias in records:
            line = f"{acc}\t{alias}"
            if not live:
                line += " (test)"
            if line not in existing:
                out.write(line + "\n")
    print(f"Analysis accession(s) also saved to: {out_file}")

def submit_manifests(manifests, jar, user, pwd, live, logs_dir, sub_dir):
    vlog(f"Submitting {len(manifests)} manifest(s) via Webin-CLI (context=genome, live={live})")
    for mf in manifests:
        inp = os.path.dirname(mf)
        sample_id = os.path.basename(inp)
        log_subdir = os.path.join(logs_dir, sample_id)
        os.makedirs(log_subdir, exist_ok=True)
        drop_cached_validation(log_subdir)
        cmd = [
            "java", "-jar", jar,
            "-context", "genome",
            "-manifest", mf,
            "-inputDir", inp,
            "-outputDir", log_subdir,
            "-submit",
            "-username", user,
            "-password", pwd
        ]
        if not live:
            cmd.insert(cmd.index("-submit"), "-test")

        # redact user and password in the printed command
        safe_cmd = [
            ("******" if c in (user, pwd) else c)       # replace secrets
            for c in cmd
        ]
        print(f"→ Running: {' '.join(safe_cmd)}")

        res = subprocess.run(cmd, capture_output=True, text=True)
        print(f"  Return code: {res.returncode}")
        if res.stdout:
            print(res.stdout)
        if res.stderr:
            print(res.stderr, file=sys.stderr)
        
        # Parse receipt and report accessions
        if res.returncode == 0:
            success, records = parse_analysis_receipt(log_subdir)
            if success == 'true' and records:
                test_note = " (test submission)" if not live else ""
                print(f"\nAnalysis submission successful{test_note}.")
                print(f"The following accession(s) were assigned:")
                for acc, alias in records:
                    print(f"  ANALYSIS: {acc}\t(alias: {alias})")
                print()
                append_analysis_accessions(records, sub_dir, live)

def main():
    p = argparse.ArgumentParser(
        description="analysis.py → per‐sample submission folders + Webin-CLI")
    
    p.add_argument(
        "--config", default="../config.yaml",
        help="Path to YAML config file (default: config.yaml)")
    
    p.add_argument(
        "-c", "--convert", metavar="TABLE",
        help="Convert TABLE (.xlsx/.xls or .tsv/.tab/.txt) into per-sample submission/<SAMPLE>/…")
    
    p.add_argument(
        "-s", "--submit", action="store_true",
        help="Submit all submission/*/manifest.txt via Webin-CLI")
    
    p.add_argument(
        "--submission_dir", default="submission",
        help="Top‐level folder for per‐sample subdirs (default=submission/)")
    
    p.add_argument(
        "-j", "--jar", help="Path to webin-cli JAR (auto‐detect if omitted)")
    
    p.add_argument(
        "--cred_file", default="credentials.txt",
        help="File with username (line1) and password (line2)")
    
    p.add_argument(
        "--live", action="store_true",
        help="Use real submission (omit -test). Default=test")
    
    p.add_argument(
        "--logs_dir", default="logs",
        help="Where Webin-CLI writes its receipts")
    
    p.add_argument(
    "--assembly_level", choices=["contig", "scaffold", "chromosome"],
    help="Override default assembly level (contig|scaffold|chromosome)")

    p.add_argument(
    "--mingaplength", type=int,
    help="Default MINGAPLENGTH when submitting scaffolds without AGP")

    p.add_argument(
        "--demo", action="store_true",
        help="Run in demo mode: use bundled test data from demo/ "
             "and submit to the ENA test server")
    
    args = p.parse_args()

    # --- Demo mode override ---
    if args.demo:
        global VERBOSE
        VERBOSE = True
        args.config = "../demo/config.yaml"
        print("=" * 60)
        print("  DEMO MODE — using bundled test data")
        print("  Config: demo/config.yaml")
        print("  Submission target: ENA test server (live is disabled)")
        print("=" * 60)

    # pull YAML config
    cfg = load_config(args.config)

    # Now, get all arguments from the config file, if empty then from command line, and if empty, then defaults
    # 1. Excel or tsv file to convert
    table_path = cfg.get("data_analysis")
    if not table_path:
        table_path = args.convert
    # 2. Submit
    submit = cfg.get("submit")
    if not submit:
        submit = args.submit
    # 3. Submission folder path
    sub_dir = cfg.get("sub_dir_analysis")
    if not sub_dir:
        sub_dir = args.submission_dir
    # 4. Credentials file
    cred_path = cfg.get("credentials")
    if not cred_path:
        cred_path = args.cred_file
    # 5. JAR path
    jar_path = cfg.get("jar")
    if not jar_path:
        jar_path = args.jar
    # 6. live flag
    live = cfg.get("live")
    if not live:
        live = args.live

    # Safety: demo mode never goes to the live server
    if args.demo and live:
        print("WARNING: --demo overrides live=True → submitting to test server only.")
        live = False

    # 7. assembly level (chromosome, scaffold or contig) and mingaplength for scaffold and no agp file
    default_level = cfg.get("assembly_level")
    if not default_level:
        default_level = args.assembly_level or "chromosome"
    default_mingap = cfg.get("mingaplength")
    if not default_mingap:
        default_mingap = args.mingaplength

    vlog(f"Resolved parameters:")
    vlog(f"  table_path     = {table_path}")
    vlog(f"  submit         = {submit}")
    vlog(f"  sub_dir        = {sub_dir}")
    vlog(f"  cred_path      = {cred_path}")
    vlog(f"  jar_path       = {jar_path}")
    vlog(f"  live           = {live}")
    vlog(f"  assembly_level = {default_level}")
    vlog(f"  mingaplength   = {default_mingap}")

    manifests = []
    if table_path:
        manifests = convert_manifests(
            table_path,
            submission_dir=sub_dir,
            default_level=default_level,
            default_mingaplength=default_mingap,
        )

    if submit:
        user, pwd = load_credentials(cred_path)
        jar = find_jar(jar_path)
        logs = prepare_logs_dir(args.logs_dir)
        if not manifests:
            # discover all manifests under submission_dir
            manifests = sorted(
                glob.glob(os.path.join(sub_dir, "*", "manifest.txt"))
            )
            if not manifests:
                sys.exit("No manifests found; run with -c your.xlsx first.")
        submit_manifests(manifests, jar, user, pwd, live, logs, sub_dir)

    if not table_path and not submit:
        p.print_help()

if __name__ == "__main__":
    main()
