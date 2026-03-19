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
# Possible objects: "biosamples", "analysis", or "runs"
ena_object=""
# Set to "true" to run in demo mode (uses bundled test data + test server)
demo="true"
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
  local extra_flags=""
  if [ "$demo" = "true" ]; then
    extra_flags="--demo"
  fi
  echo "--- Running ${dir}/${ena_object} ${extra_flags} ---"
  ( cd "$dir" && python "$ena_object" "$extra_flags" )
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
  *)
    echo "Error: Unknown script '$ena_object'."
    echo "Usage: $0 {biosamples|analysis|runs}"
    exit 1
    ;;
esac

# Clean up
module purge

echo "'$ena_object' script completed."
