# Downloadable libraries
import pandas as pd
import yaml

# Standard libraries
import xml.etree.ElementTree as ET
import xml.dom.minidom as minidom
import argparse  # Manage arguments
import sys
import os        # Paths, folders, etc
import datetime  # For conversion of date
import subprocess  # For curl
import re       # For date pattern matching
import tempfile
import shlex

# --- Verbose logging (enabled by --verbose or --demo) ---
VERBOSE = False
def vlog(msg):
    """Print a verbose/debug message when running in verbose or demo mode."""
    if VERBOSE:
        print(f"  [info] {msg}")


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

# Default ENA endpoints
TEST_ENDPOINT = "https://wwwdev.ebi.ac.uk/ena/submit/drop-box/submit/"
LIVE_ENDPOINT = "https://www.ebi.ac.uk/ena/submit/drop-box/submit/"

# Columns every submission needs, whatever the checklist. They are not
# checklist fields: ENA takes them from the <SAMPLE> element itself, not
# from <SAMPLE_ATTRIBUTES>.
CORE_COLUMNS = ["isolate", "organism", "taxon_id"]

# Companion columns that carry a unit for another column, e.g.
# "depth [unit]" gives the unit for the "depth" column.
UNIT_SUFFIX_RE = re.compile(r"^(?P<base>.+?)\s*\[unit\]$", re.IGNORECASE)

# The one checklist field whose value is reformatted before submission.
# The label is stable across ENA sample checklists.
DATE_TAG = "collection date"

# The attribute telling ENA which checklist to validate against. ENflorA writes
# it from the checklist it was given, so a user column of this name is ignored.
CHECKLIST_TAG = "ENA-CHECKLIST"

# Header of submission/biosample_accessions.txt. 'server' records whether the
# accession came from the test or the live endpoint; test accessions do not
# exist on the live server, so the two must never be confused.
BIOSAMPLE_ACCESSION_HEADER = "accession\talias\tserver"


def load_table(path: str, case: str = "lower"):
    """
    Read a data table into a DataFrame. Accepts Excel (.xlsx/.xls),
    comma-separated (.csv) and tab-separated (.tsv/.tab/.txt) files.

    case: "lower" or "upper" to normalise the header, anything else
    (e.g. None) keeps the header exactly as written.
    """
    vlog(f"Reading table: {os.path.abspath(path)}")
    ext = os.path.splitext(path)[1].lower()
    if ext in {".xlsx", ".xls"}:
        df = pd.read_excel(path, sheet_name=0)
    elif ext == ".csv":
        df = pd.read_csv(path, sep=",")
    elif ext in {".tsv", ".tab", ".txt"}:
        df = pd.read_csv(path, sep="\t")
    else:
        sys.exit(
            f"Unsupported table extension '{ext}'. "
            "Use .xlsx/.xls, .csv, or .tsv/.tab/.txt"
        )
    df.columns = df.columns.str.strip()
    if case == "upper":
        df.columns = df.columns.str.upper()
    elif case == "lower":
        df.columns = df.columns.str.lower()
    vlog(f"Table loaded: {len(df)} rows, columns: {list(df.columns)}")
    return df


# --------------------------------------------------------------------------
# ENA checklist parsing
# --------------------------------------------------------------------------

def _norm(text) -> str:
    """Normalise a column name or field label for case-insensitive matching."""
    return str(text).strip().lower()


def parse_checklist(path: str) -> dict:
    """
    Parse an ENA sample checklist XML into a specification dictionary.

    Checklist XMLs are downloaded from
    https://www.ebi.ac.uk/ena/browser/checklists

    Returns:
        {
          "accession": "ERC000037",
          "name": "ENA Plant Sample Checklist",
          "fields": {<lowercased label>: {
                "label": str,        # the tag ENA expects in the XML
                "name": str,         # ENA's machine-readable field name
                "description": str,
                "status": "mandatory" | "recommended" | "optional",
                "units": [str, ...],
                "choices": [str, ...],
                "group": str,
          }},
          "order": [<lowercased label>, ...],   # checklist order
        }
    """
    if not os.path.exists(path):
        sys.exit(
            f"Checklist file not found: {path}\n"
            "Download the XML for your checklist from "
            "https://www.ebi.ac.uk/ena/browser/checklists and point the "
            "'checklist' key of config.yaml at it."
        )

    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        sys.exit(f"Could not parse checklist XML '{path}': {exc}")

    # The browser API returns a CHECKLIST_SET wrapper; accept a bare
    # CHECKLIST too, in case the file was trimmed by hand.
    if root.tag == "CHECKLIST":
        checklists = [root]
    else:
        checklists = root.findall("CHECKLIST")

    if len(checklists) != 1:
        sys.exit(
            f"Expected exactly one CHECKLIST in '{path}', found {len(checklists)}. "
            "Download a single checklist, not a set."
        )
    checklist = checklists[0]

    accession = checklist.get("accession") or checklist.findtext("IDENTIFIERS/PRIMARY_ID")
    if not accession:
        sys.exit(f"Could not find the checklist accession (ERC number) in '{path}'.")
    accession = accession.strip()

    checklist_type = (checklist.get("checklistType") or "").strip()
    if checklist_type and checklist_type.lower() != "sample":
        print(
            f"WARNING: '{path}' is a '{checklist_type}' checklist, not a Sample "
            "checklist. biosamples.py registers samples, so this is probably "
            "the wrong file."
        )

    descriptor = checklist.find("DESCRIPTOR")
    if descriptor is None:
        sys.exit(f"Malformed checklist '{path}': no DESCRIPTOR element.")

    name = descriptor.findtext("LABEL") or descriptor.findtext("NAME") or accession

    fields, order = {}, []
    for group in descriptor.findall("FIELD_GROUP"):
        group_name = (group.findtext("NAME") or "").strip()
        for field in group.findall("FIELD"):
            label = (field.findtext("LABEL") or field.findtext("NAME") or "").strip()
            if not label:
                continue
            key = _norm(label)
            if key in fields:
                vlog(f"Duplicate field '{label}' in checklist, keeping the first one")
                continue

            status = _norm(field.findtext("MANDATORY") or "optional")
            if status not in {"mandatory", "recommended", "optional"}:
                status = "optional"

            units = [
                unit.text.strip()
                for unit in field.findall("UNITS/UNIT")
                if unit.text and unit.text.strip()
            ]
            choices = [
                value.text.strip()
                for value in field.findall(
                    "FIELD_TYPE/TEXT_CHOICE_FIELD/TEXT_VALUE/VALUE"
                )
                if value.text and value.text.strip()
            ]

            fields[key] = {
                "label": label,
                "name": (field.findtext("NAME") or "").strip(),
                "description": " ".join((field.findtext("DESCRIPTION") or "").split()),
                "status": status,
                "units": units,
                "choices": choices,
                "group": group_name,
            }
            order.append(key)

    if not fields:
        sys.exit(f"Checklist '{path}' declares no fields.")

    counts = {s: sum(1 for f in fields.values() if f["status"] == s)
              for s in ("mandatory", "recommended", "optional")}
    print(
        f"Checklist {accession} ({name}): {len(fields)} fields "
        f"({counts['mandatory']} mandatory, {counts['recommended']} recommended, "
        f"{counts['optional']} optional)"
    )

    return {"accession": accession, "name": name, "fields": fields, "order": order}


# --------------------------------------------------------------------------
# Mapping the data table onto the checklist
# --------------------------------------------------------------------------

def resolve_columns(df, spec: dict, column_aliases: dict = None) -> dict:
    """
    Work out which table column feeds which ENA attribute tag.

    Column names are matched against the checklist labels case-insensitively.
    A column matching no checklist field is still submitted, as a free sample
    attribute under its own name.

    'column_aliases' maps a house column name onto a checklist field, e.g.
        latitude: geographic location (latitude)
    Two columns may point at the same field; their values are then joined
    with ", " in the order the aliases are listed in config.yaml.

    Returns a dict with:
        core        {core column name: actual column in the table}
        sources     {canonical tag: [table columns feeding it]}
        units       {lowercased canonical tag: table column holding the unit}
        emit_order  [canonical tags, in table column order]
        unknown     [table columns matching no checklist field]
    """
    column_aliases = column_aliases or {}
    alias_map, alias_order, alias_text = {}, {}, {}
    for position, (source, target) in enumerate(column_aliases.items()):
        alias_map[_norm(source)] = _norm(target)
        alias_text[_norm(source)] = str(target).strip()
        alias_order[_norm(source)] = position

    core, sources, units, emit_order, unknown = {}, {}, {}, [], []
    column_position = {col: i for i, col in enumerate(df.columns)}

    def canonical(column_name) -> str:
        """Table column -> the tag it is submitted under."""
        normalised = _norm(column_name)
        if normalised in alias_map:
            field = spec["fields"].get(alias_map[normalised])
            # An alias may also point at a field the checklist does not define;
            # ENA accepts those as free attributes, so honour the target name.
            return field["label"] if field else alias_text[normalised]
        field = spec["fields"].get(normalised)
        return field["label"] if field else str(column_name).strip()

    for column in df.columns:
        normalised = _norm(column)

        # Blank or unnamed columns, which spreadsheets produce easily
        if not normalised or normalised == "nan":
            continue

        # Unit companion column, e.g. "depth [unit]"
        unit_match = UNIT_SUFFIX_RE.match(normalised)
        if unit_match:
            base = unit_match.group("base").strip()
            units[_norm(canonical(base))] = column
            continue

        # Core columns are handled at the <SAMPLE> level, not as attributes.
        # An alias may point a house column name at one of them.
        target = alias_map.get(normalised, normalised)
        if target in CORE_COLUMNS:
            core[target] = column
            continue

        tag = canonical(column)

        # ENflorA writes this attribute itself, from the checklist accession
        if _norm(tag) == _norm(CHECKLIST_TAG):
            print(
                f"WARNING: column '{column}' is ignored. ENflorA writes "
                f"{CHECKLIST_TAG} itself, from the checklist it was given."
            )
            continue

        if _norm(tag) not in spec["fields"]:
            unknown.append(tag)
        sources.setdefault(tag, []).append(column)
        if tag not in emit_order:
            emit_order.append(tag)

    # Columns aliased onto the same tag are joined in config order; a column
    # matching the checklist directly comes first.
    def sort_key(column):
        normalised = _norm(column)
        if normalised in alias_order:
            return (1, alias_order[normalised])
        return (0, column_position[column])

    for tag in sources:
        sources[tag].sort(key=sort_key)

    return {
        "core": core,
        "sources": sources,
        "units": units,
        "emit_order": emit_order,
        "unknown": unknown,
    }


def format_value(value) -> str:
    """
    Render a cell for the XML.

    Whole numbers read from a spreadsheet come back as floats, which would
    otherwise be written as "3702.0" instead of "3702".
    """
    if pd.isnull(value):
        return ""
    if isinstance(value, datetime.datetime):
        if (value.hour, value.minute, value.second) == (0, 0, 0):
            return value.strftime("%Y-%m-%d")
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def normalise_date(raw) -> str:
    """
    Convert a collection date to ISO 8601. Accepts spreadsheet dates, a bare
    year, and the German DD.MM.YYYY notation. Anything else is passed through
    untouched, so ENA's missing-value vocabulary ("not collected",
    "missing: lab stock", ...) survives.
    """
    if isinstance(raw, (datetime.datetime, datetime.date)):
        return format_value(raw)

    text = str(raw).strip()
    if re.fullmatch(r"\d{4}", text):
        return text
    try:
        return datetime.datetime.strptime(text, "%d.%m.%Y").strftime("%Y-%m-%d")
    except ValueError:
        return text


def unit_for(tag: str, row, mapping: dict, spec: dict):
    """
    Unit for one attribute: the "<field> [unit]" column if the user filled it
    in, otherwise the checklist's unit when it declares exactly one. Fields
    offering several units and left blank are submitted without one.
    """
    column = mapping["units"].get(_norm(tag))
    if column is not None and column in row.index:
        written = format_value(row[column])
        if written:
            return written

    field = spec["fields"].get(_norm(tag))
    if field and len(field["units"]) == 1:
        return field["units"][0]
    return None


# --------------------------------------------------------------------------
# XML generation
# --------------------------------------------------------------------------

def add_attribute(parent, tag, value, units=None):
    attr_elem = ET.SubElement(parent, "SAMPLE_ATTRIBUTE")
    ET.SubElement(attr_elem, "TAG").text = str(tag)
    ET.SubElement(attr_elem, "VALUE").text = str(value)
    if units:
        ET.SubElement(attr_elem, "UNITS").text = str(units)


def table_to_xml(table_file, spec, submission_dir="submission",
                 output_xml="biosamples.xml", extra_mandatory=None,
                 defaults=None, column_aliases=None):
    """
    Convert a filled data table into an ENA sample XML, validated against the
    checklist in 'spec'.

    extra_mandatory  fields your group requires even when ENA does not
    defaults         values used when a cell is left empty
    column_aliases   house column names mapped onto checklist fields
    """
    os.makedirs(submission_dir, exist_ok=True)
    output_path = os.path.join(submission_dir, output_xml)
    vlog(f"Output XML will be written to: {os.path.abspath(output_path)}")

    try:
        df = load_table(table_file, case=None)
    except SystemExit:
        raise
    except Exception as exc:
        sys.exit(f"Error reading table: {exc}")

    if df.empty:
        sys.exit(f"No data rows found in '{table_file}'.")

    mapping = resolve_columns(df, spec, column_aliases)

    # --- Defaults, keyed by checklist field or by house column name ---
    alias_map = {_norm(k): _norm(v) for k, v in (column_aliases or {}).items()}

    def canonical_key(entry) -> str:
        """Config entry -> lowercased canonical tag, or a core column name."""
        target = alias_map.get(_norm(entry), _norm(entry))
        if target in CORE_COLUMNS:
            return target
        field = spec["fields"].get(target)
        return _norm(field["label"]) if field else target

    def label_of(key) -> str:
        """Lowercased canonical tag -> the tag as written in the XML."""
        field = spec["fields"].get(key)
        return field["label"] if field else key

    # isolate, organism and taxon_id are filled in on the <SAMPLE> element, so a
    # default for one of them sets that value rather than adding an attribute.
    core_defaults, resolved_defaults = {}, {}
    for key, value in (defaults or {}).items():
        target = canonical_key(key)
        if target in CORE_COLUMNS:
            core_defaults[target] = value
        else:
            resolved_defaults[target] = value
    if resolved_defaults or core_defaults:
        vlog(f"Defaults in use: {dict(resolved_defaults, **core_defaults)}")

    # --- Which tags are mandatory: the checklist's, plus your own ---
    mandatory = {key for key, field in spec["fields"].items()
                 if field["status"] == "mandatory"}
    for entry in (extra_mandatory or []):
        target = canonical_key(entry)
        if target in CORE_COLUMNS:
            continue          # already required of every submission
        mandatory.add(target)
    recommended = {key for key, field in spec["fields"].items()
                   if field["status"] == "recommended"}

    present = {_norm(tag) for tag in mapping["sources"]}

    # --- Column-level validation ---
    missing_core = [c for c in CORE_COLUMNS
                    if c not in mapping["core"] and c not in core_defaults]
    if missing_core:
        sys.exit(
            f"Missing required column(s) in '{table_file}': {', '.join(missing_core)}.\n"
            "These are needed for every submission, whatever the checklist: "
            "'isolate' becomes the sample alias, 'organism' the title, and "
            "'taxon_id' the NCBI taxon."
        )

    missing_mandatory = sorted(
        tag for tag in mandatory
        if tag not in present and tag not in resolved_defaults
    )
    if missing_mandatory:
        sys.exit(
            f"Missing mandatory column(s) in '{table_file}': "
            f"{', '.join(label_of(t) for t in missing_mandatory)}.\n"
            f"Checklist {spec['accession']} requires them. Add the column(s), or "
            "generate a fresh template with make_table.py."
        )

    missing_recommended = sorted(tag for tag in recommended if tag not in present)
    if missing_recommended:
        print(
            f"Note: {len(missing_recommended)} recommended field(s) of checklist "
            f"{spec['accession']} are absent from the table. This is allowed; "
            "run with --verbose to list them."
        )
        for tag in missing_recommended:
            vlog(f"  recommended, not submitted: {label_of(tag)}")

    if mapping["unknown"]:
        print(
            f"Note: {len(mapping['unknown'])} attribute(s) are not fields of "
            f"{spec['accession']} and will be submitted as free sample "
            f"attributes: {', '.join(str(c) for c in mapping['unknown'])}"
        )

    # Defaults may cover a field that has no column at all
    emit_order = list(mapping["emit_order"])
    for key in resolved_defaults:
        if label_of(key) not in emit_order:
            emit_order.append(label_of(key))

    # --- Build the XML ---
    root = ET.Element("SAMPLE_SET")
    seen_aliases = {}

    for index, row in df.iterrows():
        row_number = index + 1

        # Resolve every attribute once, joining any aliased columns
        values = {}
        for tag, columns in mapping["sources"].items():
            parts = [format_value(row[c]) for c in columns]
            values[tag] = ", ".join(p for p in parts if p)

        # Defaults fill in blanks, including for absent columns
        for key, default in resolved_defaults.items():
            if not values.get(label_of(key)):
                values[label_of(key)] = format_value(default)

        # Core values, falling back to a configured default
        core = {}
        for name in CORE_COLUMNS:
            column = mapping["core"].get(name)
            value = format_value(row[column]) if column is not None else ""
            if not value and name in core_defaults:
                value = format_value(core_defaults[name])
            if not value:
                sys.exit(
                    f"Error: Required field '{name}' is empty for sample number {row_number}"
                )
            core[name] = value

        # ENA rejects a submission carrying the same alias twice
        if core["isolate"] in seen_aliases:
            sys.exit(
                f"Error: isolate '{core['isolate']}' is used by both sample number "
                f"{seen_aliases[core['isolate']]} and sample number {row_number}. "
                "Each sample needs its own alias, ENA rejects duplicates."
            )
        seen_aliases[core["isolate"]] = row_number

        vlog(
            f"Processing row {row_number}: isolate='{core['isolate']}', "
            f"organism='{core['organism']}', taxon_id={core['taxon_id']}"
        )

        # Mandatory attributes must carry a value
        for key in sorted(mandatory):
            if not values.get(label_of(key)):
                sys.exit(
                    f"Error: Mandatory field '{label_of(key)}' is empty for "
                    f"sample number {row_number}"
                )

        # Collection date to ISO 8601
        for tag in list(values):
            if _norm(tag) == DATE_TAG and values[tag]:
                converted = normalise_date(values[tag])
                if converted != values[tag]:
                    vlog(f"  Date: '{values[tag]}' -> '{converted}'")
                values[tag] = converted

        sample = ET.SubElement(root, "SAMPLE", attrib={
            "alias": core["isolate"],
            "center_name": ""
        })
        ET.SubElement(sample, "TITLE").text = core["organism"]
        sample_name = ET.SubElement(sample, "SAMPLE_NAME")
        ET.SubElement(sample_name, "TAXON_ID").text = core["taxon_id"]

        sample_attributes = ET.SubElement(sample, "SAMPLE_ATTRIBUTES")
        for tag in emit_order:
            value = values.get(tag)
            if not value:
                continue      # empty recommended and optional fields are skipped
            add_attribute(sample_attributes, tag, value,
                          unit_for(tag, row, mapping, spec))

        # Tells ENA which checklist to validate this sample against
        add_attribute(sample_attributes, CHECKLIST_TAG, spec["accession"])

    # Pretty-print and write out
    vlog(f"Generated XML with {len(df)} sample(s), writing to {output_path}")
    rough = ET.tostring(root, encoding="utf-8")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    try:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(pretty)
        print(f"{output_path} file successfully written")
    except Exception as exc:
        sys.exit(f"Error writing {output_path}: {exc}")

    return submission_dir


# Has <hold> so they don't become immediately public in case it goes to "live"
def create_submission_xml(submission_dir="submission", submission_xml="submission.xml"):
    path = os.path.join(submission_dir, submission_xml)
    submission_xml_content = '''<?xml version="1.0" encoding="UTF-8"?>
    <SUBMISSION>
        <ACTIONS>
            <ACTION>
                <ADD/>
            </ACTION>
            <ACTION>
                <HOLD/>
            </ACTION>
        </ACTIONS>
    </SUBMISSION>
    '''
    os.makedirs(submission_dir, exist_ok=True)
    if not os.path.exists(path):
        with open(path, "w") as f:
            f.write(submission_xml_content)
        print(f"{path} created.")
    else:
        print(f"{path} already exists.")


def prepare_logs_dir(logs_dir="logs"):
    if not os.path.exists(logs_dir):
        os.makedirs(logs_dir)
        print(f"Directory '{logs_dir}' created.")
    else:
        print(f"Directory '{logs_dir}' already exists.")
    return logs_dir


# Uses the test submission as default, just in case
def submit_data(username, password, submission_dir="submission", logs_dir="logs", url=TEST_ENDPOINT):
    vlog(f"Preparing submission to: {url}")
    vlog(f"  Submission dir: {submission_dir}")
    vlog(f"  Logs directory: {logs_dir}")
    # Build submission and receipt filenames
    submission_file = os.path.join(submission_dir, "submission.xml")
    biosamples_file = os.path.join(submission_dir, "biosamples.xml")
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    receipt_file = os.path.join(logs_dir, f"biosample_receipt_{timestamp}.xml")
    vlog(f"  Submission XML: {submission_file}")
    vlog(f"  Sample XML: {biosamples_file}")

    # create a temporary netrc file for curl authentication
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0]
    else:
        host = url.split("/", 1)[0]

    with tempfile.NamedTemporaryFile("w", delete=False) as tf:
        tf.write(f"machine {host}\nlogin {username}\npassword {password}\n")
    netrc_path = tf.name
    os.chmod(netrc_path, 0o600)
    vlog(f"  Created temporary netrc for host: {host}")

    try:
        curl_command = [
            "curl",
            "--netrc-file", netrc_path,              # <— no -u flag
            "-F", f"SUBMISSION=@{submission_file}",
            "-F", f"SAMPLE=@{biosamples_file}",
            url,
            "-o", receipt_file
        ]

        # 3) print *safe* command (no secrets anywhere)
        print("→ Running:", " ".join(shlex.quote(a) for a in curl_command))

        result = subprocess.run(curl_command, capture_output=True, text=True)
    finally:
        os.remove(netrc_path)                        # ensure cleanup
    

    print("Curl exit code:", result.returncode)
    if result.stdout:
        print("Stdout:", result.stdout)
    if result.stderr:
        print("Stderr:", result.stderr)

    # Parse receipt XML, to look for accession codes, alias, and whether success or not
    if os.path.exists(receipt_file):
        try:
            tree = ET.parse(receipt_file)
            root = tree.getroot()
            success = root.attrib.get('success', 'false')
            print(f"Submission success: {success}")

            # Extract sample accession & alias
            records = []
            for samp in root.findall('SAMPLE'):
                acc = samp.attrib.get('accession')
                alias = samp.attrib.get('alias')
                records.append((acc, alias))

            # Print accessions to stdout (also visible in SLURM .out files on HPC)
            if success == 'true' and records:
                test_note = " (test submission)" if url == TEST_ENDPOINT else ""
                print(f"\nBiosample registration successful{test_note}.")
                print(f"The following accession(s) were assigned:")
                for acc, alias in records:
                    print(f"  {acc}\t(alias: {alias})")
                print()
            elif success != 'true':
                for msg in root.findall('.//ERROR'):
                    print(f"ERROR: {msg.text}")
                for msg in root.findall('.//INFO'):
                    print(f"INFO: {msg.text}")

            # Write to text file (appends, deduplicates). Only on success: when
            # ENA rejects a submission it still reports the accessions it would
            # have assigned, but those samples were never registered, and
            # resolve_accessions.py must not pick them up as real.
            if success == 'true' and records:
                out_file = os.path.join(submission_dir, 'biosample_accessions.txt')
                server = "test" if url == TEST_ENDPOINT else "live"
                rows = [f"{acc}\t{alias}\t{server}" for acc, alias in records]
                write_accession_file(out_file, BIOSAMPLE_ACCESSION_HEADER, rows)
                print(f"Accessions also saved to: {out_file}")
            elif success != 'true':
                print("Submission failed, so no accessions were saved.")
            else:
                print("The receipt lists no sample accessions, so nothing was saved.")
        except Exception as e:
            print(f"Error parsing receipt XML: {e}")
    else:
        print(f"Receipt file not found: {receipt_file}")


def write_accession_file(out_file, header, rows):
    """
    Append accession records to a tab-separated file, without duplicates.

    Which server an accession came from is a column ('test' or 'live'), not a
    suffix glued onto the alias. Accessions handed out by the test server do not
    exist on the live one, so anything reading this file back has to be able to
    tell them apart on sight.

    A file written by an older version of ENflorA, which had no server column
    and marked test rows with a ' (test)' suffix, is upgraded in place the first
    time it is written to.
    """
    existing = []
    if os.path.exists(out_file):
        with open(out_file) as fh:
            lines = [line.rstrip("\n") for line in fh if line.strip()]
        if lines and lines[0] == header:
            existing = lines[1:]
        elif lines:
            for line in lines[1:]:
                if line.endswith(" (test)"):
                    existing.append(line[: -len(" (test)")] + "\ttest")
                else:
                    existing.append(line + "\tlive")
            vlog(f"Upgraded {out_file} to the new server-column format")

    seen = set(existing)
    for row in rows:
        if row not in seen:
            existing.append(row)
            seen.add(row)

    with open(out_file, "w") as out:
        out.write(header + "\n")
        for row in existing:
            out.write(row + "\n")


def load_credentials(file_path):
    vlog(f"Loading credentials from: {os.path.abspath(file_path)}")
    try:
        with open(file_path) as cred:
            lines = [l.strip() for l in cred if l.strip()]
        if len(lines) < 2:
            raise ValueError("Credentials file must have at least two non-empty lines: \nusername \npassword.")
        return lines[0], lines[1]
    except Exception as e:
        sys.exit(f"Error loading credentials from {file_path}: {e}")


def main():
    parser = argparse.ArgumentParser(
        description="Biosamples submission tool to ENA, converts a data table "
                    "to XML and submits it."
    )

    parser.add_argument(
        "--config", default="../config.yaml",
        help="Path to YAML config file (default: config.yaml)")

    parser.add_argument("-c", "--convert", metavar="TABLE",
                        help="Convert TABLE (.xlsx/.xls, .csv or .tsv/.tab/.txt) "
                             "to biosamples.xml")

    parser.add_argument("--checklist", metavar="XML",
                        help="Path to the ENA sample checklist XML to validate "
                             "against (overrides the 'checklist' key of config.yaml)")

    parser.add_argument("-s", "--submit", action="store_true", default=None,
                        help="Submit the XML files using curl")

    parser.add_argument("-u", "--username",
                        help="Username for submission (optional if --cred_file is provided)")

    parser.add_argument("-p", "--password",
                        help="Password for submission (optional if --cred_file is provided)")

    parser.add_argument("--cred_file", default="credentials.txt",
                        help="Path to a text file with username on line 1 and password on line 2")

    parser.add_argument("--live", action="store_true", default=None,
                        help="Submit to the live ENA endpoint instead of test (DEV) endpoint")

    parser.add_argument("--submission_dir", default="submission",
                        help="Directory for generated XML and accession files (default: submission)")

    parser.add_argument("--logs_dir", default="logs",
                        help="Directory to store submission logs (default: logs)")

    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Print what the script is doing at every step")

    parser.add_argument("--demo", action="store_true",
                        help="Run in demo mode: use bundled test data from demo/ "
                             "and submit to the ENA test server")

    args = parser.parse_args()

    global VERBOSE
    VERBOSE = bool(args.verbose or args.demo)

    # --- Demo mode override ---
    if args.demo:
        args.config = "../demo/config.yaml"
        print("=" * 60)
        print("  DEMO MODE — using bundled test data")
        print("  Config: demo/config.yaml")
        print("  Submission target: ENA test server (live is disabled)")
        print("=" * 60)

    cfg = load_config(args.config)

    table_path = cfg.get("data_biosamples") or args.convert
    checklist_path = args.checklist or cfg.get("checklist")
    cred_path = cfg.get("credentials") or args.cred_file
    sub_dir = cfg.get("sub_dir_biosamples") or args.submission_dir

    # Same precedence as every other option: the config wins whenever the key is
    # set, the CLI flag is the fallback for a blank or absent key. Reading it this
    # way, rather than with "if not submit", is what makes an explicit
    # 'submit: False' or 'live: False' in the config actually take effect.
    submit = bool(cfg["submit"]) if cfg.get("submit") is not None else bool(args.submit)
    live = bool(cfg["live"]) if cfg.get("live") is not None else bool(args.live)

    # Checklist-related options, all optional
    extra_mandatory = cfg.get("extra_mandatory") or []
    defaults = cfg.get("defaults") or {}
    column_aliases = cfg.get("column_aliases") or {}
    if isinstance(extra_mandatory, str):
        extra_mandatory = [extra_mandatory]

    # Safety: demo mode never goes to the live server
    if args.demo and live:
        print("WARNING: --demo overrides live=True → submitting to test server only.")
        live = False

    vlog(f"Resolved parameters:")
    vlog(f"  table_path      = {table_path}")
    vlog(f"  checklist       = {checklist_path}")
    vlog(f"  submit          = {submit}")
    vlog(f"  sub_dir         = {sub_dir}")
    vlog(f"  cred_path       = {cred_path}")
    vlog(f"  live            = {live}")
    vlog(f"  extra_mandatory = {extra_mandatory}")
    vlog(f"  defaults        = {defaults}")
    vlog(f"  column_aliases  = {column_aliases}")

    if table_path:
        if not checklist_path:
            sys.exit(
                "No checklist given. Add a 'checklist' key to config.yaml pointing "
                "at an ENA sample checklist XML, or pass --checklist.\n"
                "Checklists can be downloaded from "
                "https://www.ebi.ac.uk/ena/browser/checklists"
            )
        spec = parse_checklist(checklist_path)
        table_to_xml(
            table_path,
            spec,
            submission_dir=sub_dir,
            extra_mandatory=extra_mandatory,
            defaults=defaults,
            column_aliases=column_aliases,
        )

    if submit:
        # Load or override credentials
        if args.username and args.password:
            user, pw = args.username, args.password
        else:
            user, pw = load_credentials(cred_path)

        create_submission_xml(submission_dir=sub_dir)
        logs = prepare_logs_dir(args.logs_dir)
        endpoint = LIVE_ENDPOINT if live else TEST_ENDPOINT
        print(f"Using endpoint: {endpoint}")
        submit_data(user, pw, submission_dir=sub_dir, logs_dir=logs, url=endpoint)

    if not table_path and not submit:
        parser.print_help()


if __name__ == "__main__":
    main()
