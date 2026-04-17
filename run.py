"""
Edit the variables below, then run: python3 run.py
"""

# ── Configuration ──────────────────────────────────────────────────────────────

# Process all labels starting with this prefix (leave blank to use LABELS list)
LABEL_PREFIX = "APPID"

# Or specify exact labels as a list (only used if LABEL_PREFIX is blank)
# Example: LABELS = ["APPID:11945", "APPID:11946", "APPID:11947"]
LABELS = []

# Or point to a text file with one label per line (only used if LABEL_PREFIX and LABELS are both blank)
# Place the file in the same folder as run.py and just use the filename, e.g. "labels.txt"
# Or use a full path, e.g. "/Users/yourname/Desktop/labels.txt"
LABELS_FILE = ""

# Group and sub-group to nest projects under (leave blank to skip)
GROUP = "My Test Group by API"
SUBGROUP = "My Test Subgroup by API"

# Business impact for new projects: Low, Medium, or High
BUSINESS_IMPACT = "Medium"

# Type categorization for new projects (leave blank to skip)
# Options: Application, Business Unit, Department, Product Area, Product Family, Team, Other
PROJECT_TYPE = "Application"

# Type categorization for new groups/sub-groups (leave blank to skip)
# Options: Application, Business Unit, Department, Product Area, Product Family, Team, Other
GROUP_TYPE = ""

# Set to True to preview without writing anything to Cycode, False to make actual changes
DRY_RUN = False

# Save log output to a file (leave blank to skip)
# Example: "run.log" will save a log file in the root of this app
LOG_FILE = "run.log"

# ── Run ────────────────────────────────────────────────────────────────────────

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

args = []

if LABEL_PREFIX:
    args += ["--label-prefix", LABEL_PREFIX]
elif LABELS:
    args += ["--labels", ",".join(LABELS)]
elif LABELS_FILE:
    args += ["--labels-file", LABELS_FILE]

if GROUP:
    args += ["--group", GROUP]
if SUBGROUP:
    args += ["--subgroup", SUBGROUP]
if BUSINESS_IMPACT:
    args += ["--business-impact", BUSINESS_IMPACT]
if PROJECT_TYPE:
    args += ["--project-type", PROJECT_TYPE]
if GROUP_TYPE:
    args += ["--group-type", GROUP_TYPE]
if LOG_FILE:
    args += ["--log-file", LOG_FILE]
if DRY_RUN:
    args += ["--dry-run"]

sys.argv = ["create_groups_by_label.py"] + args

from create_groups_by_label import main
main()
