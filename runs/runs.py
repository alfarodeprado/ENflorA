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

def convert_manifests(table_file, submission_dir="submission"):
    # Load table (UPPERCASE headers expected)
    df = load_table(table_file, case="upper")
    sample_counts = defaultdict(int)

    # Required columns for raw reads submission
    required = [
        "STUDY", "SAMPLE", "NAME", "INSTRUMENT", "INSERT_SIZE",
        "LIBRARY_NAME", "LIBRARY_SOURCE", "LIBRARY_SELECTION", "LIBRARY_STRATEGY",
        "DESCRIPTION"
    ]
    missing = [c for c in required if c not in df.columns]
    if missing:
        sys.exit(f"Missing columns in table: {', '.join(missing)}")

    # Identify file columns (BAM, CRAM, FASTQ, including duplicates like FASTQ1)
    file_cols = [c for c in df.columns if c.upper() in ("BAM", "CRAM") or c.upper().startswith("FASTQ")]
    if not file_cols:
        sys.exit("No file columns (BAM, CRAM, FASTQ) found in table header")
    vlog(f"File columns detected: {file_cols}")
    vlog(f"Submission directory: {os.path.abspath(submission_dir)}")

    os.makedirs(submission_dir, exist_ok=True)
    manifest_paths = []

    for idx, row in df.iterrows():
        n = idx + 1
        # In case there are more than one objects associated with the same sample.
        raw_id = str(row["SAMPLE"]).strip()
        sample_counts[raw_id] += 1
        # first occurrence → use raw_id; subsequent → raw_id_2, raw_id_3, ...
        if sample_counts[raw_id] == 1:
            sample_id = raw_id
        else:
            sample_id = f"{raw_id}_{sample_counts[raw_id]}"
        samp_dir = os.path.join(submission_dir, sample_id)
        os.makedirs(samp_dir, exist_ok=True)
        vlog(f"Row {n}: sample={sample_id}, study={row['STUDY']}, dir={samp_dir}")

        # Collect file entries
        entries = []
        for col in file_cols:
            val = row.get(col)
            if pd.notnull(val) and str(val).strip().lower() != "nan":
                entries.append((col, str(val).strip()))
        if not entries:
            sys.exit(f"Row {n}: no files specified in any of {', '.join(file_cols)}")
        vlog(f"  Files: {[rel for _, rel in entries]}")


        # Determine file type in case of paired reads, or more than one type, based on paths' extensions
        types = set()
        for _, rel in entries:
            low = rel.lower()
            if low.endswith(".bam") or low.endswith(".bam.gz"):
                types.add("BAM")
            elif low.endswith(".cram") or low.endswith(".cram.gz"):
                types.add("CRAM")
            elif low.endswith(".fastq") or low.endswith(".fq") \
                or low.endswith(".fastq.gz") or low.endswith(".fq.gz"):
                types.add("FASTQ")
            else:
                sys.exit(f"Row {n}: unrecognized file extension in '{rel}'")
        if len(types) != 1:
            sys.exit(f"Row {n}: mixed file types in one row: {types}")
        filetype = types.pop()
        vlog(f"  Detected file type: {filetype}")

        # Validate counts
        if filetype in ("BAM", "CRAM") and len(entries) != 1:
            sys.exit(f"Row {n}: {filetype} requires exactly one file entry, got {len(entries)}")
        if filetype == "FASTQ" and len(entries) < 1:
            sys.exit(f"Row {n}: at least one FASTQ entry required")
        
        # Handling of files, check for compression too
        compressed_files = []
        # Handle each file: copy into samp_dir, compress if needed
        for _, rel in entries:
            src = os.path.abspath(rel)
            if not os.path.exists(src):
                sys.exit(f"Row {n}: file not found: {src}")
            # if file is already in samp_dir, just add to manifest
            if os.path.dirname(src) == os.path.abspath(samp_dir):
                vlog(f"  '{rel}': already in submission dir, using as-is")
                compressed_files.append(os.path.basename(src))
                continue
            # if already .gz then soft-link into samp_dir
            if src.endswith(".gz"):
                link_path = os.path.join(samp_dir, os.path.basename(src))
                if not os.path.exists(link_path):
                    try:
                        os.symlink(src, link_path)         # relative link
                    except OSError:
                            raise
                vlog(f"  '{rel}': already compressed, symlinking to {link_path}")
                compressed_files.append(os.path.basename(link_path))
                continue

            # if not .gz then stream-compress right into samp_dir
            gz_name = os.path.basename(src) + ".gz"
            dst = os.path.join(samp_dir, gz_name)
            vlog(f"  '{rel}': compressing to {dst}")

            with open(src, "rb") as f_in, gzip.open(dst, "wb", compresslevel=6) as f_out:
                shutil.copyfileobj(f_in, f_out, length=1024 * 1024)  # 1 MiB chunks

            compressed_files.append(gz_name)

        # Write manifest.txt
        mf = os.path.join(samp_dir, "manifest.txt")
        with open(mf, "w") as fh:
            fh.write(f"STUDY\t{row['STUDY']}\n")
            fh.write(f"SAMPLE\t{row['SAMPLE']}\n")
            fh.write(f"NAME\t{row['NAME']}\n")
            fh.write(f"INSTRUMENT\t{row['INSTRUMENT']}\n")
            fh.write(f"INSERT_SIZE\t{row['INSERT_SIZE']}\n")
            fh.write(f"LIBRARY_NAME\t{row['LIBRARY_NAME']}\n")
            fh.write(f"LIBRARY_SOURCE\t{row['LIBRARY_SOURCE']}\n")
            fh.write(f"LIBRARY_SELECTION\t{row['LIBRARY_SELECTION']}\n")
            fh.write(f"LIBRARY_STRATEGY\t{row['LIBRARY_STRATEGY']}\n")
            fh.write(f"DESCRIPTION\t{row['DESCRIPTION']}\n")
            for fn in compressed_files:
                fh.write(f"{filetype}\t{fn}\n")
        print(f"[Row {n}] Wrote manifest → {mf}")
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
    Delete stale validate.json files inside logs/<sample_id>/reads/*/

    Removing these forces Webin-CLI to recalculate MD5s on the next run, without throwing away the whole log directory.
    """
    pattern = os.path.join(log_subdir, "reads", "*", "validate.json")
    for path in glob.glob(pattern):
        try:
            os.remove(path)
            print(f"  Removed cached validation → {path}")
        except OSError as exc:
            print(f"  Could not remove {path}: {exc}")

def submit_manifests(manifests, jar, user, pwd, live, logs_dir):
    vlog(f"Submitting {len(manifests)} manifest(s) via Webin-CLI (context=reads, live={live})")
    for mf in manifests:
        inp = os.path.dirname(mf)
        sample_id = os.path.basename(inp)
        log_subdir = os.path.join(logs_dir, sample_id)
        os.makedirs(log_subdir, exist_ok=True)
        drop_cached_validation(log_subdir)
        cmd = [
            "java", "-jar", jar,
            "-context", "reads",
            "-manifest", mf,
            "-inputDir", inp,
            "-outputDir", log_subdir,
            "-submit",
            "-username", user,
            "-password", pwd
        ]
        if not live:
            cmd.insert(cmd.index("-submit"), "-test")
        #print(f"→ Running: {' '.join(cmd)}")

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
    
    # remove compressed files?

def main():
    p = argparse.ArgumentParser(
        description="reads.py → per‐sample raw‐reads submission folders + Webin-CLI")
    
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
    table_path = cfg.get("data_runs")
    if not table_path:
        table_path = args.convert
    # 2. Submit
    submit = cfg.get("submit")
    if not submit:
        submit = args.submit
    # 3. Submission folder path
    sub_dir = cfg.get("sub_dir_runs")
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

    vlog(f"Resolved parameters:")
    vlog(f"  table_path = {table_path}")
    vlog(f"  submit     = {submit}")
    vlog(f"  sub_dir    = {sub_dir}")
    vlog(f"  cred_path  = {cred_path}")
    vlog(f"  jar_path   = {jar_path}")
    vlog(f"  live       = {live}")

    manifests = []
    if table_path:
        manifests = convert_manifests(table_path, sub_dir)

    if submit:
        user, pwd = load_credentials(cred_path)
        jar = find_jar(jar_path)
        logs = prepare_logs_dir(args.logs_dir)
        if not manifests: #discover
            manifests = sorted(
                glob.glob(os.path.join(sub_dir, "*", "manifest.txt"))
            )
            if not manifests:
                sys.exit("No manifests found; run with -c your.xlsx first.")
        submit_manifests(manifests, jar, user, pwd, live, logs)

    if not table_path and not submit:
        p.print_help()

if __name__ == "__main__":
    main()
