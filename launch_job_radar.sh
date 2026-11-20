#!/usr/bin/env bash

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
REPO_DIR="$SCRIPT_DIR"
PDEF_VENV_PYTHON="$REPO_DIR/.venv/bin/python"

if ! cd "$REPO_DIR"; then
    printf '[ERROR] The Job Radar repository directory could not be opened.\n' >&2
    exit 1
fi

if [[ -x "$PDEF_VENV_PYTHON" ]]; then
    if [[ "${PDEF_DRY_RUN:-}" == "1" ]]; then
        printf '%q -m streamlit run docs/job/job_radar_app.py\n' "$PDEF_VENV_PYTHON"
        exit 0
    fi

    if "$PDEF_VENV_PYTHON" -m streamlit run docs/job/job_radar_app.py; then
        exit 0
    fi

    printf '\n[ERROR] Job Radar did not start correctly.\n' >&2
    exit 1
fi

printf '[ERROR] No local .venv Python was found at:\n' >&2
printf '        %s\n' "$PDEF_VENV_PYTHON" >&2
printf 'Rebuild the local environment first, then relaunch Job Radar.\n' >&2
exit 1
