# Source from Bash: source tools/deployment/shell.sh
# Familiar SEED VM commands, usable with any Compose project.
alias dcbuild='docker compose build'
alias dcup='docker compose up'
alias dcdown='docker compose down'
alias dockps='docker ps --format "{{.ID}}  {{.Names}}" | sort -k 2'
docksh() { docker exec -it "$1" /bin/bash; }

_skyseed_tools_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
skyseed() {
    conda activate skyseed_dev || return
    cd -- "$_skyseed_tools_root"
}

# S4 manual example queries; no Docker scan or container mutation.
skytime() { python "$_skyseed_tools_root/examples/s4_handover/experiment/console.py"; }
skynodes() { python "$_skyseed_tools_root/examples/s4_handover/experiment/console.py" nodes; }
