# create_groups_by_label

Creates Cycode Groups, Sub-groups, and Projects at scale by api from repository labels (with dry run option).

## How it works

1. Reads label names from Cycode (by prefix or explicit list)
2. Optionally creates a Group and/or Sub-group hierarchy
3. Creates one Project per label, named after the label (e.g. `APPID:11945`), dynamically linked to the label — all repos tagged with that label are automatically included, and new repos tagged later are picked up automatically
4. Skips anything that already exists — safe to re-run at any time
5. Complete the variables in the run.py file to execute

## Setup

### 1. Install dependencies

First, check which situation applies to you:

```bash
pip3 install requests python-dotenv
```

**If that works — you're done.** Use `pip3` and `python3` for all commands.

---

**If you see `error: externally-managed-environment`**, your system Python is protected from global pip installs (common on macOS with Python 3.12+). Use a virtual environment instead:

```bash
cd "/path/to/create groups by label"
python3 -m venv .venv
source .venv/bin/activate
pip install requests python-dotenv
```
Your terminal prompt will change to show `(.venv)` — that means it's active and you're good to run the script.

> **Each new terminal session**, re-activate the venv before running the script:
> ```bash
> source "/path/to/create groups by label/.venv/bin/activate"
> ```

### 2. Add credentials to `.env`

Open `.env` and fill in your Cycode client ID and secret:

```
CYCODE_CLIENT_ID=your_client_id_here
CYCODE_CLIENT_SECRET=your_client_secret_here
```

Your credentials are in the Cycode UI under your API access token settings. The script fetches a JWT automatically at startup — no manual token copying needed.

## Usage

### Dry run (no changes written to Cycode)

Always a good idea to run this first to preview what will be created:

```bash
python3 create_groups_by_label.py --label-prefix "EAI:" --dry-run
```

> **Note on dry-run output:** If you include `--group` or `--subgroup`, the log will show `parent_group_id=None` for the projects and `parent_id=None` for the sub-group. This is expected — in dry-run mode no real IDs are assigned, so there is nothing to pass down the hierarchy. In a live run the IDs chain correctly and projects will be properly nested under the group and sub-group.

### Create a project for one specific label

```bash
python3 create_groups_by_label.py --labels "EAI:11945"
```

### Create projects for a few specific labels

```bash
python3 create_groups_by_label.py --labels "EAI:11945,EAI:11946,EAI:11947"
```

### Create projects for all labels matching a prefix (bulk)

```bash
python3 create_groups_by_label.py --label-prefix "EAI:"
```

### Create projects from a file of label names

One label per line in a plain text file:

```bash
python3 create_groups_by_label.py --labels-file my_labels.txt
```

### Create projects under a Group / Sub-group hierarchy

Groups and Sub-groups act like folders. If they don't exist yet, the script creates them first.

```bash
python3 create_groups_by_label.py \
    --label-prefix "EAI:" \
    --group "ACME Applications" \
    --subgroup "Security Team"
```

### Set business impact

Default is `Medium` (set in `.env` as `DEFAULT_BUSINESS_IMPACT`). Override per run:

```bash
python3 create_groups_by_label.py --label-prefix "EAI:" --business-impact High
```

### Set project type

Categorize new projects with a type. EAI labels represent application IDs, so `Application` is the natural default (also set as default in `run.py`):

```bash
python3 create_groups_by_label.py --label-prefix "EAI:" --project-type Application
```

Valid values: `Application`, `Business Unit`, `Department`, `Product Area`, `Product Family`, `Team`, `Other`

You can also set a default in `.env`:

```
DEFAULT_PROJECT_TYPE=Application
```

### Set group type

Categorize new groups and sub-groups with a type:

```bash
python3 create_groups_by_label.py --label-prefix "EAI:" --group "Security" --group-type Department
```

Valid values: `Application`, `Business Unit`, `Department`, `Product Area`, `Product Family`, `Team`, `Other`

You can also set a default in `.env`:

```
DEFAULT_GROUP_TYPE=Department
```

### Save output to a log file

```bash
python3 create_groups_by_label.py --label-prefix "EAI:" --log-file run.log
```

## All options

| Flag | Description |
|------|-------------|
| `--labels "A,B,C"` | Comma-separated list of specific label names |
| `--labels-file FILE` | Path to a file with one label name per line |
| `--label-prefix PREFIX` | Process all labels starting with PREFIX |
| `--group NAME` | Top-level Group to create or reuse |
| `--subgroup NAME` | Sub-group to create or reuse under `--group` |
| `--business-impact` | `Low`, `Medium`, or `High` (default: `Medium`) |
| `--project-type TYPE` | Type for new projects: `Application`, `Business Unit`, `Department`, `Product Area`, `Product Family`, `Team`, `Other` |
| `--group-type TYPE` | Type for new groups/sub-groups: same values as `--project-type` |
| `--log-file FILE` | Also write log output to a file |
| `--dry-run` | Simulate everything — no changes written to Cycode |
| `--repo-assets` | Attach individual repos as static assets instead of the default dynamic label asset |

## Idempotency

The script checks for existing Groups and Projects by name before creating anything. If they already exist, it logs `SKIP` and moves on. This means you can re-run the same command safely — duplicates will never be created.

## Rate limiting & retries

The script automatically retries on HTTP 429 (rate limited) and server errors (5xx) with exponential backoff: 2s, 4s, 8s, 16s, 32s.

## Security

- `.env` is listed in `.gitignore` — credentials will not be committed to git
- `.env` is listed in `.claudeignore` — credentials will not be read by AI tools
