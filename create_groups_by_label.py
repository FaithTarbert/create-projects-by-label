"""
create_groups_by_label.py
─────────────────────────
Creates Cycode Groups, Sub-groups, and Projects from repository labels.

Workflow
────────
1. Read label names from --labels (CSV) or --labels-file (one per line).
2. For each label, fetch associated repository resource IDs via
   GET /v4/labels/resources.
3. Optionally create a top-level Group and/or Sub-group hierarchy (passed
   as --group and --subgroup).  Checks for existing groups first — skips
   and logs duplicates.
4. Create one Project per label named after the label value (e.g. "APPID:11945"),
   attaching all discovered repository assets and optionally linking it to a
   group / sub-group via parent_group_id.
5. All skipped-duplicate and created events are written to the console and
   optionally to --log-file.

Usage examples
──────────────
# Basic: create projects from every label matching "APPID:"
python create_groups_by_label.py --label-prefix "APPID:"

# With group/sub-group hierarchy
python create_groups_by_label.py \
    --label-prefix "APPID:" \
    --group "ACME Applications" \
    --subgroup "Security Team"

# From an explicit list of labels
python create_groups_by_label.py \
    --labels "APPID:11945,APPID:11946,APPID:11947"

# Dry run (no writes)
python create_groups_by_label.py --label-prefix "APPID:" --dry-run

Dependencies
────────────
pip install requests python-dotenv
"""

import argparse
import logging
import os
import sys
import time
from typing import Optional

import requests
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────────────────────────────
# Logging setup
# ──────────────────────────────────────────────────────────────────────────────

LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(message)s"
logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stdout)
log = logging.getLogger(__name__)


def add_file_handler(path: str) -> None:
    fh = logging.FileHandler(path)
    fh.setFormatter(logging.Formatter(LOG_FORMAT))
    logging.getLogger().addHandler(fh)


# ──────────────────────────────────────────────────────────────────────────────
# Cycode API client
# ──────────────────────────────────────────────────────────────────────────────

class CycodeClient:
    """Thin wrapper around the Cycode REST API v4."""

    MAX_PAGE_SIZE = 100
    # Retry behaviour for HTTP 429 / 5xx
    RETRY_STATUSES = {429, 500, 502, 503, 504}
    RETRY_DELAYS = [2, 4, 8, 16, 32]  # seconds (exponential backoff)

    TOKEN_PATH = "/api/v1/auth/api-token"

    def __init__(self, client_id: str, client_secret: str, base_url: str = "https://api.cycode.com"):
        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret
        self._token: Optional[str] = None
        self.session = requests.Session()
        self.session.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })
        self._authenticate()

    def _authenticate(self) -> None:
        """Fetch a fresh JWT and apply it to the session."""
        log.info("Authenticating with Cycode…")
        url = f"{self.base_url}{self.TOKEN_PATH}"
        resp = self.session.post(
            url,
            json={"clientId": self._client_id, "secret": self._client_secret},
        )
        if not resp.ok:
            raise RuntimeError(
                f"Authentication failed — HTTP {resp.status_code}\n{resp.text[:500]}"
            )
        data = resp.json()
        # The token is in the "access_token" or "token" field depending on the response shape
        token = data.get("access_token") or data.get("token") or data.get("jwt")
        if not token:
            raise RuntimeError(
                f"Could not find token in auth response. Keys returned: {list(data.keys())}"
            )
        self._token = token
        self.session.headers["Authorization"] = f"Bearer {token}"
        log.info("Authentication successful.")

    # ── Low-level request helper ──────────────────────────────────────────────

    def _request(self, method: str, path: str, **kwargs) -> dict:
        url = f"{self.base_url}{path}"
        for attempt, delay in enumerate([0] + self.RETRY_DELAYS, start=1):
            if delay:
                log.warning("Rate-limited or server error — retrying in %ss (attempt %d)…", delay, attempt)
                time.sleep(delay)
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                if attempt > len(self.RETRY_DELAYS):
                    raise
                log.warning("Request error (%s), will retry…", exc)
                continue

            # Re-authenticate once on 401 (expired JWT)
            if resp.status_code == 401 and attempt == 1:
                log.warning("Token expired — re-authenticating…")
                self._authenticate()
                continue

            if resp.status_code in self.RETRY_STATUSES and attempt <= len(self.RETRY_DELAYS):
                continue

            if not resp.ok:
                raise RuntimeError(
                    f"HTTP {resp.status_code} {method} {url}\n{resp.text[:500]}"
                )
            # 204 No Content or empty body
            if not resp.content:
                return {}
            return resp.json()

        raise RuntimeError(f"Exhausted retries for {method} {url}")

    # ── Pagination helper ─────────────────────────────────────────────────────

    def _paginate(self, path: str, params: Optional[dict] = None) -> list:
        """Collect all items across page-number–based paginated endpoints."""
        params = dict(params or {})
        params.setdefault("page_size", self.MAX_PAGE_SIZE)
        results = []
        page = 0
        while True:
            params["page_number"] = page
            data = self._request("GET", path, params=params)
            items = data.get("items", [])
            results.extend(items)
            # Stop when fewer items than page_size were returned
            if len(items) < params["page_size"]:
                break
            page += 1
        return results

    # ── Labels ────────────────────────────────────────────────────────────────

    def list_labels(self, prefix: Optional[str] = None) -> list[dict]:
        """Return all labels, optionally filtered to those starting with *prefix*."""
        labels = self._paginate("/v4/labels")
        if prefix:
            labels = [lb for lb in labels if lb.get("name", "").startswith(prefix)]
        return labels

    def get_label_resources(self, label_name: str, resource_type: str = "scm_repository") -> list[dict]:
        """Return all resources (repos) tagged with *label_name*."""
        return self._paginate(
            "/v4/labels/resources",
            {"label_name": label_name, "resource_type": resource_type},
        )

    # ── Groups ────────────────────────────────────────────────────────────────

    def list_groups(self, query: Optional[str] = None) -> list[dict]:
        params = {}
        if query:
            params["query"] = query
        return self._paginate("/v4/groups", params)

    def find_group_by_name(self, name: str, parent_id: Optional[int] = None) -> Optional[dict]:
        """Find an existing group/sub-group by exact name (and optional parent)."""
        groups = self.list_groups(query=name)
        for g in groups:
            if g.get("name") == name:
                if parent_id is None or g.get("parent_group_id") == parent_id:
                    return g
        return None

    def create_group(
        self,
        name: str,
        parent_group_id: Optional[int] = None,
        group_type: Optional[str] = None,
        dry_run: bool = False,
    ) -> dict:
        body: dict = {"name": name}
        if parent_group_id is not None:
            body["parent_group_id"] = parent_group_id
        if group_type:
            body["type"] = group_type
        if dry_run:
            log.info("[DRY-RUN] Would create group: %s (parent_id=%s, type=%s)", name, parent_group_id, group_type)
            return {"id": None, "name": name, "_dry_run": True}
        return self._request("POST", "/v4/groups", json=body)

    def add_projects_to_group(self, group_id: int, project_ids: list[int]) -> dict:
        return self._request(
            "PUT",
            f"/v4/groups/{group_id}",
            json={"projects_to_add": project_ids},
        )

    # ── Projects ──────────────────────────────────────────────────────────────

    def list_projects(self, query: Optional[str] = None) -> list[dict]:
        params = {}
        if query:
            params["query"] = query
        return self._paginate("/v4/projects", params)

    def find_project_by_name(self, name: str) -> Optional[dict]:
        projects = self.list_projects(query=name)
        for p in projects:
            if p.get("name") == name:
                return p
        return None

    def create_project(
        self,
        name: str,
        assets: list[dict],
        business_impact: str = "Medium",
        parent_group_id: Optional[int] = None,
        project_type: Optional[str] = None,
        label_names: Optional[list[str]] = None,
        dry_run: bool = False,
    ) -> dict:
        body: dict = {
            "name": name,
            "business_impact": business_impact,
            "assets": assets,
        }
        if parent_group_id is not None:
            body["parent_group_id"] = parent_group_id
        if project_type:
            body["project_type"] = project_type
        if label_names:
            body["labels"] = label_names
        if dry_run:
            log.info(
                "[DRY-RUN] Would create project: %s  assets=%d  labels=%s  parent_group_id=%s  project_type=%s",
                name, len(assets), label_names, parent_group_id, project_type,
            )
            return {"id": None, "name": name, "_dry_run": True}
        return self._request("POST", "/v4/projects", json=body)


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────

def ensure_group(
    client: CycodeClient,
    name: str,
    parent_id: Optional[int],
    dry_run: bool,
    group_type: Optional[str] = None,
) -> Optional[int]:
    """Return the ID of an existing or newly-created group/sub-group."""
    existing = client.find_group_by_name(name, parent_id)
    if existing:
        gid = existing.get("id")
        log.info("SKIP  group already exists: '%s' (id=%s)", name, gid)
        return gid

    result = client.create_group(name, parent_group_id=parent_id, group_type=group_type, dry_run=dry_run)
    gid = result.get("id")
    if not result.get("_dry_run"):
        log.info("CREATED  group: '%s' (id=%s)", name, gid)
    return gid


def run(
    client: CycodeClient,
    *,
    label_names: list[str],
    label_prefix: Optional[str],
    group_name: Optional[str],
    subgroup_name: Optional[str],
    business_impact: str,
    project_type: Optional[str],
    group_type: Optional[str],
    dry_run: bool,
    use_label_asset: bool = True,
) -> None:
    # ── Resolve labels ────────────────────────────────────────────────────────
    if label_prefix and not label_names:
        log.info("Fetching labels with prefix '%s'…", label_prefix)
        labels = client.list_labels(prefix=label_prefix)
        label_names = [lb["name"] for lb in labels]
        log.info("Found %d matching labels.", len(label_names))

    if not label_names:
        log.error("No labels to process. Use --labels or --label-prefix.")
        sys.exit(1)

    # ── Ensure Group / Sub-group ──────────────────────────────────────────────
    parent_group_id: Optional[int] = None

    if group_name:
        parent_group_id = ensure_group(client, group_name, None, dry_run, group_type=group_type)

    if subgroup_name:
        parent_group_id = ensure_group(client, subgroup_name, parent_group_id, dry_run, group_type=group_type)

    # ── Process each label ────────────────────────────────────────────────────
    created_count = 0
    skipped_count = 0
    error_count = 0

    for label in label_names:
        log.info("─── Processing label: %s", label)

        # 1. Build asset list for project creation
        label_names_for_project: Optional[list[str]] = None
        if use_label_asset:
            log.info("  Using dynamic label asset (label name as asset_id).")
            assets = [
                {
                    "id": 0,
                    "asset_type": "Label",
                    "asset_id": label,
                    "collisions_count": 0,
                }
            ]
            label_names_for_project = [label]
        else:
            resources = client.get_label_resources(label)
            if not resources:
                log.warning("  No repositories found for label '%s' — skipping.", label)
                skipped_count += 1
                continue
            log.info("  Found %d repository resource(s).", len(resources))
            assets = [
                {
                    "id": 0,
                    "asset_type": "Repository",
                    "asset_id": r["resource_id"],
                    "collisions_count": 0,
                }
                for r in resources
            ]

        # 3. Check for existing project
        existing_project = client.find_project_by_name(label)
        if existing_project:
            log.info("  SKIP  project already exists: '%s' (id=%s)", label, existing_project.get("id"))
            skipped_count += 1
            continue

        # 4. Create project
        try:
            result = client.create_project(
                name=label,
                assets=assets,
                business_impact=business_impact,
                parent_group_id=parent_group_id,
                project_type=project_type,
                label_names=label_names_for_project,
                dry_run=dry_run,
            )
            if not result.get("_dry_run"):
                log.info("  CREATED  project: '%s' (id=%s)", label, result.get("id"))
            created_count += 1
        except RuntimeError as exc:
            log.error("  ERROR creating project '%s': %s", label, exc)
            error_count += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    log.info("═" * 60)
    log.info("Done.  Created: %d  |  Skipped: %d  |  Errors: %d", created_count, skipped_count, error_count)
    if dry_run:
        log.info("(Dry-run mode — no changes were written to Cycode.)")


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Create Cycode Groups, Sub-groups, and Projects from repo labels.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )

    label_grp = p.add_mutually_exclusive_group()
    label_grp.add_argument(
        "--labels",
        metavar="LABEL[,LABEL…]",
        help="Comma-separated list of label names to process (e.g. 'EAI:11945,EAI:11946').",
    )
    label_grp.add_argument(
        "--labels-file",
        metavar="FILE",
        help="Path to a file with one label name per line.",
    )
    label_grp.add_argument(
        "--label-prefix",
        metavar="PREFIX",
        help="Process all labels whose name starts with PREFIX (e.g. 'EAI:').",
    )

    p.add_argument("--group", metavar="NAME", help="Top-level Group to create/reuse.")
    p.add_argument("--subgroup", metavar="NAME", help="Sub-group to create/reuse under --group.")

    p.add_argument(
        "--business-impact",
        choices=["Low", "Medium", "High"],
        default=None,
        help="Business impact for new projects (default: env DEFAULT_BUSINESS_IMPACT or 'Medium').",
    )

    p.add_argument(
        "--project-type",
        choices=["Application", "Business Unit", "Department", "Product Area", "Product Family", "Team", "Other"],
        default=None,
        help="Type categorization for new projects (e.g. 'Application').",
    )

    p.add_argument(
        "--group-type",
        choices=["Application", "Business Unit", "Department", "Product Area", "Product Family", "Team", "Other"],
        default=None,
        help="Type categorization for new groups/sub-groups (e.g. 'Department').",
    )

    p.add_argument("--log-file", metavar="FILE", help="Also write log output to this file.")
    p.add_argument("--dry-run", action="store_true", help="Simulate all writes — nothing is created in Cycode.")
    p.add_argument(
        "--repo-assets",
        action="store_true",
        help="Attach individual repos as static assets instead of the default dynamic label asset.",
    )

    return p.parse_args()


def main() -> None:
    load_dotenv()

    args = parse_args()

    if args.log_file:
        add_file_handler(args.log_file)

    client_id = os.getenv("CYCODE_CLIENT_ID", "")
    client_secret = os.getenv("CYCODE_CLIENT_SECRET", "")
    if not client_id or client_id == "your_client_id_here":
        log.error("CYCODE_CLIENT_ID is not set. Add it to your .env file.")
        sys.exit(1)
    if not client_secret or client_secret == "your_client_secret_here":
        log.error("CYCODE_CLIENT_SECRET is not set. Add it to your .env file.")
        sys.exit(1)

    base_url = os.getenv("CYCODE_BASE_URL", "https://api.cycode.com")
    business_impact = (
        args.business_impact
        or os.getenv("DEFAULT_BUSINESS_IMPACT", "Medium")
    )
    project_type = args.project_type or os.getenv("DEFAULT_PROJECT_TYPE") or None
    group_type = args.group_type or os.getenv("DEFAULT_GROUP_TYPE") or None

    client = CycodeClient(client_id=client_id, client_secret=client_secret, base_url=base_url)

    # Resolve label list from CLI args
    label_names: list[str] = []
    if args.labels:
        label_names = [lb.strip() for lb in args.labels.split(",") if lb.strip()]
    elif args.labels_file:
        with open(args.labels_file) as fh:
            label_names = [line.strip() for line in fh if line.strip()]

    run(
        client,
        label_names=label_names,
        label_prefix=args.label_prefix,
        group_name=args.group,
        subgroup_name=args.subgroup,
        business_impact=business_impact,
        project_type=project_type,
        group_type=group_type,
        dry_run=args.dry_run,
        use_label_asset=not args.repo_assets,
    )


if __name__ == "__main__":
    main()
