#!/usr/bin/env bash
#SBATCH --job-name=ENflorA
#SBATCH --mail-user=xxx@zedat.fu-berlin.de
#SBATCH --output=logs/ENflorA_%j.out
#SBATCH --error=logs/ENflorA_%j.err
#SBATCH --time=1-10:00:00
#SBATCH --cpus-per-task=1
#SBATCH --qos=standard
#SBATCH --mem=4G

set -euo pipefail

# Helper to run a selected BO pipeline on FU‑Berlin HPC.
# Usage: sbatch /hpc.sh

######################################################################
######################################################################
# Possible objects: "biosamples", "analysis", "runs", "make_table", or
# "resolve_accessions"
# ("make_table" is the one-off helper that builds a blank metadata table from
#  an ENA checklist XML; it never contacts ENA and ignores the demo setting)
ena_object=""
# Set to "true" to run in demo mode (uses bundled test data + test server)
demo="true"

# Only used with ena_object="resolve_accessions": the runs or analysis table to
# fill in, and the accession file(s) written by the previous step. Paths are
# relative to this folder. For several accession files, separate them with a
# space. In demo mode, test-server accessions are always used.
resolve_table=""
resolve_accession_files=""
######################################################################
######################################################################


# Load environment (update if needed in the future)
module purge
module load Python/3.11.3-GCCcore-12.3.0
module load Java/21.0.5

# 1) Set / refresh project environment WITHOUT spawning a sub‑shell
python set_env.py -s -H

# 2) Activate the environment created by set_env.py
source env/bin/activate

# Helper to run a given ena_object
run_script() {
  local dir="$1"
  local ena_object="$2"
  if [ "$demo" = "true" ]; then
    echo "--- Running ${dir}/${ena_object} --demo ---"
    ( cd "$dir" && python "$ena_object" --demo )
  else
    echo "--- Running ${dir}/${ena_object} ---"
    ( cd "$dir" && python "$ena_object" )
  fi
}

# Dispatch based on ena_object
case "$ena_object" in
  biosamples)
    run_script "biosamples" "biosamples.py"
    ;;
  analysis)
    run_script "analysis" "analysis.py"
    ;;
  runs)
    run_script "runs" "runs.py"
    ;;
  make_table)
    # One-off helper, no submission and no demo mode.
    echo "--- Running biosamples/make_table.py ---"
    ( cd "biosamples" && python "make_table.py" )
    ;;
  resolve_accessions)
    # Fills real accessions into a runs/analysis table. Never contacts ENA.
    if [ -z "$resolve_table" ] || [ -z "$resolve_accession_files" ]; then
      echo "Error: set resolve_table and resolve_accession_files at the top of hpc.sh."
      exit 1
    fi
    resolve_args=(--table "$resolve_table")
    for accession_file in $resolve_accession_files; do
      resolve_args+=(--accessions "$accession_file")
    done
    if [ "$demo" = "true" ]; then
      resolve_args+=(--server test)
    fi
    echo "--- Running resolve_accessions.py ${resolve_args[*]} ---"
    python resolve_accessions.py "${resolve_args[@]}"
    ;;
  *)
    echo "Error: Unknown script '$ena_object'."
    echo "Usage: $0 {biosamples|analysis|runs|make_table|resolve_accessions}"
    exit 1
    ;;
esac

# Clean up
module purge

echo "'$ena_object' script completed."
