"""
Tests for create_groups_by_label.py

Run with:
    python -m pytest test_create_groups_by_label.py -v
    # or
    python -m unittest test_create_groups_by_label.py -v
"""

import os
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from create_groups_by_label import CycodeClient, ensure_group, run


# ── Helpers ───────────────────────────────────────────────────────────────────

def make_client():
    """Return a CycodeClient with authentication bypassed and session mocked."""
    with patch.object(CycodeClient, "_authenticate", return_value=None):
        client = CycodeClient("test_id", "test_secret")
    client.session = MagicMock()
    client._token = "test_token"
    return client


# ── Authentication ─────────────────────────────────────────────────────────────

class TestAuthentication(unittest.TestCase):

    def _mock_auth(self, response_json, ok=True, status_code=200, text=""):
        with patch("requests.Session") as MockSession:
            mock_session = MagicMock()
            MockSession.return_value = mock_session
            mock_resp = MagicMock()
            mock_resp.ok = ok
            mock_resp.status_code = status_code
            mock_resp.text = text
            mock_resp.json.return_value = response_json
            mock_session.post.return_value = mock_resp
            return CycodeClient, mock_session

    def test_auth_accepts_access_token_field(self):
        with patch("requests.Session") as MockSession:
            s = MagicMock()
            MockSession.return_value = s
            r = MagicMock(ok=True)
            r.json.return_value = {"access_token": "tok1"}
            s.post.return_value = r
            client = CycodeClient("id", "secret")
            self.assertEqual(client._token, "tok1")

    def test_auth_accepts_token_field(self):
        with patch("requests.Session") as MockSession:
            s = MagicMock()
            MockSession.return_value = s
            r = MagicMock(ok=True)
            r.json.return_value = {"token": "tok2"}
            s.post.return_value = r
            client = CycodeClient("id", "secret")
            self.assertEqual(client._token, "tok2")

    def test_auth_accepts_jwt_field(self):
        with patch("requests.Session") as MockSession:
            s = MagicMock()
            MockSession.return_value = s
            r = MagicMock(ok=True)
            r.json.return_value = {"jwt": "tok3"}
            s.post.return_value = r
            client = CycodeClient("id", "secret")
            self.assertEqual(client._token, "tok3")

    def test_auth_http_failure_raises(self):
        with patch("requests.Session") as MockSession:
            s = MagicMock()
            MockSession.return_value = s
            r = MagicMock(ok=False, status_code=401, text="Unauthorized")
            s.post.return_value = r
            with self.assertRaises(RuntimeError) as ctx:
                CycodeClient("bad", "creds")
            self.assertIn("Authentication failed", str(ctx.exception))

    def test_auth_missing_token_field_raises(self):
        with patch("requests.Session") as MockSession:
            s = MagicMock()
            MockSession.return_value = s
            r = MagicMock(ok=True)
            r.json.return_value = {"unexpected_field": "value"}
            s.post.return_value = r
            with self.assertRaises(RuntimeError) as ctx:
                CycodeClient("id", "secret")
            self.assertIn("Could not find token", str(ctx.exception))


# ── Retry logic ────────────────────────────────────────────────────────────────

class TestRetryLogic(unittest.TestCase):

    def _ok_resp(self):
        r = MagicMock(ok=True, status_code=200, content=b'{"items":[]}')
        r.json.return_value = {"items": []}
        return r

    def _err_resp(self, status_code):
        return MagicMock(ok=False, status_code=status_code, content=b"error")

    def test_retries_on_429(self):
        client = make_client()
        client.session.request.side_effect = [self._err_resp(429), self._ok_resp()]
        with patch("time.sleep"):
            client._request("GET", "/v4/groups")
        self.assertEqual(client.session.request.call_count, 2)

    def test_retries_on_500(self):
        client = make_client()
        client.session.request.side_effect = [self._err_resp(500), self._ok_resp()]
        with patch("time.sleep"):
            client._request("GET", "/v4/groups")
        self.assertEqual(client.session.request.call_count, 2)

    def test_retries_on_502(self):
        client = make_client()
        client.session.request.side_effect = [self._err_resp(502), self._ok_resp()]
        with patch("time.sleep"):
            client._request("GET", "/v4/groups")
        self.assertEqual(client.session.request.call_count, 2)

    def test_reauth_on_401(self):
        client = make_client()
        client.session.request.side_effect = [self._err_resp(401), self._ok_resp()]
        with patch.object(client, "_authenticate") as mock_auth:
            client._request("GET", "/v4/groups")
            mock_auth.assert_called_once()

    def test_exhausted_retries_raises(self):
        client = make_client()
        client.session.request.return_value = self._err_resp(429)
        with patch("time.sleep"):
            with self.assertRaises(RuntimeError) as ctx:
                client._request("GET", "/v4/groups")
        self.assertIn("Exhausted retries", str(ctx.exception))

    def test_empty_response_body_returns_empty_dict(self):
        client = make_client()
        r = MagicMock(ok=True, status_code=204, content=b"")
        client.session.request.return_value = r
        result = client._request("DELETE", "/v4/groups/1")
        self.assertEqual(result, {})

    def test_non_retryable_error_raises_immediately(self):
        client = make_client()
        r = MagicMock(ok=False, status_code=400, content=b"bad request", text="bad request")
        client.session.request.return_value = r
        with self.assertRaises(RuntimeError) as ctx:
            client._request("POST", "/v4/projects")
        self.assertIn("HTTP 400", str(ctx.exception))


# ── Pagination ─────────────────────────────────────────────────────────────────

class TestPagination(unittest.TestCase):

    def test_single_page_stops_after_one_request(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"items": [{"id": 1}, {"id": 2}]}):
            result = client._paginate("/v4/groups")
        self.assertEqual(len(result), 2)

    def test_multiple_pages_collects_all(self):
        client = make_client()
        page_0 = {"items": [{"id": i} for i in range(100)]}
        page_1 = {"items": [{"id": i} for i in range(100, 150)]}
        with patch.object(client, "_request", side_effect=[page_0, page_1]):
            result = client._paginate("/v4/groups")
        self.assertEqual(len(result), 150)

    def test_empty_first_page_returns_empty_list(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"items": []}):
            result = client._paginate("/v4/groups")
        self.assertEqual(result, [])

    def test_exactly_full_page_fetches_next(self):
        """A page with exactly page_size items should trigger a follow-up request."""
        client = make_client()
        page_0 = {"items": [{"id": i} for i in range(100)]}
        page_1 = {"items": []}
        with patch.object(client, "_request", side_effect=[page_0, page_1]) as mock_req:
            client._paginate("/v4/groups")
        self.assertEqual(mock_req.call_count, 2)


# ── Labels ─────────────────────────────────────────────────────────────────────

class TestLabels(unittest.TestCase):

    def test_list_labels_no_prefix_returns_all(self):
        client = make_client()
        all_labels = [{"name": "APPID:001"}, {"name": "OTHER:001"}]
        with patch.object(client, "_paginate", return_value=all_labels):
            result = client.list_labels()
        self.assertEqual(len(result), 2)

    def test_list_labels_prefix_filters_correctly(self):
        client = make_client()
        all_labels = [{"name": "APPID:001"}, {"name": "APPID:002"}, {"name": "OTHER:001"}]
        with patch.object(client, "_paginate", return_value=all_labels):
            result = client.list_labels(prefix="APPID:")
        self.assertEqual(len(result), 2)
        self.assertTrue(all(lb["name"].startswith("APPID:") for lb in result))

    def test_list_labels_prefix_no_matches(self):
        client = make_client()
        with patch.object(client, "_paginate", return_value=[{"name": "OTHER:001"}]):
            result = client.list_labels(prefix="APPID:")
        self.assertEqual(result, [])

    def test_get_label_resources_defaults_to_scm_repository(self):
        client = make_client()
        with patch.object(client, "_paginate", return_value=[]) as mock_pag:
            client.get_label_resources("APPID:001")
        params = mock_pag.call_args[0][1]
        self.assertEqual(params["resource_type"], "scm_repository")

    def test_get_label_resources_passes_label_name(self):
        client = make_client()
        with patch.object(client, "_paginate", return_value=[]) as mock_pag:
            client.get_label_resources("APPID:001")
        params = mock_pag.call_args[0][1]
        self.assertEqual(params["label_name"], "APPID:001")


# ── Groups ─────────────────────────────────────────────────────────────────────

class TestGroups(unittest.TestCase):

    def test_find_group_by_name_found(self):
        client = make_client()
        groups = [{"id": 101, "name": "My Group", "parent_group_id": None}]
        with patch.object(client, "list_groups", return_value=groups):
            result = client.find_group_by_name("My Group")
        self.assertEqual(result["id"], 101)

    def test_find_group_by_name_not_found(self):
        client = make_client()
        with patch.object(client, "list_groups", return_value=[]):
            result = client.find_group_by_name("Missing Group")
        self.assertIsNone(result)

    def test_find_group_exact_name_match_only(self):
        """A group named 'My Group Extended' should not match 'My Group'."""
        client = make_client()
        groups = [{"id": 101, "name": "My Group Extended", "parent_group_id": None}]
        with patch.object(client, "list_groups", return_value=groups):
            result = client.find_group_by_name("My Group")
        self.assertIsNone(result)

    def test_find_group_with_correct_parent(self):
        client = make_client()
        groups = [{"id": 202, "name": "Sub Group", "parent_group_id": 101}]
        with patch.object(client, "list_groups", return_value=groups):
            result = client.find_group_by_name("Sub Group", parent_id=101)
        self.assertEqual(result["id"], 202)

    def test_find_group_with_wrong_parent_returns_none(self):
        client = make_client()
        groups = [{"id": 202, "name": "Sub Group", "parent_group_id": 999}]
        with patch.object(client, "list_groups", return_value=groups):
            result = client.find_group_by_name("Sub Group", parent_id=101)
        self.assertIsNone(result)

    def test_create_group_dry_run_no_api_call(self):
        client = make_client()
        result = client.create_group("Test Group", dry_run=True)
        self.assertTrue(result.get("_dry_run"))
        self.assertIsNone(result.get("id"))
        client.session.request.assert_not_called()

    def test_create_group_live_calls_api(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 101}) as mock_req:
            result = client.create_group("Test Group")
        self.assertEqual(result["id"], 101)
        mock_req.assert_called_once()

    def test_create_group_sends_parent_group_id(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 202}) as mock_req:
            client.create_group("Sub Group", parent_group_id=101)
        body = mock_req.call_args[1]["json"]
        self.assertEqual(body["parent_group_id"], 101)

    def test_create_group_sends_type_when_set(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 101}) as mock_req:
            client.create_group("My Group", group_type="Department")
        body = mock_req.call_args[1]["json"]
        self.assertEqual(body["type"], "Department")

    def test_create_group_omits_type_when_not_set(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 101}) as mock_req:
            client.create_group("My Group")
        body = mock_req.call_args[1]["json"]
        self.assertNotIn("type", body)


# ── Projects ───────────────────────────────────────────────────────────────────

class TestProjects(unittest.TestCase):

    def test_find_project_by_name_found(self):
        client = make_client()
        with patch.object(client, "list_projects", return_value=[{"id": 1, "name": "APPID:001"}]):
            result = client.find_project_by_name("APPID:001")
        self.assertEqual(result["id"], 1)

    def test_find_project_by_name_not_found(self):
        client = make_client()
        with patch.object(client, "list_projects", return_value=[]):
            result = client.find_project_by_name("APPID:999")
        self.assertIsNone(result)

    def test_find_project_exact_name_match_only(self):
        client = make_client()
        with patch.object(client, "list_projects", return_value=[{"id": 1, "name": "APPID:0012"}]):
            result = client.find_project_by_name("APPID:001")
        self.assertIsNone(result)

    def test_create_project_dry_run_no_api_call(self):
        client = make_client()
        result = client.create_project("APPID:001", assets=[], dry_run=True)
        self.assertTrue(result.get("_dry_run"))
        self.assertIsNone(result.get("id"))
        client.session.request.assert_not_called()

    def test_create_project_live_calls_api(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 55}) as mock_req:
            result = client.create_project("APPID:001", assets=[])
        self.assertEqual(result["id"], 55)
        mock_req.assert_called_once()

    def test_create_project_sends_project_type(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 55}) as mock_req:
            client.create_project("APPID:001", assets=[], project_type="Application")
        body = mock_req.call_args[1]["json"]
        self.assertEqual(body["project_type"], "Application")

    def test_create_project_omits_project_type_when_not_set(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 55}) as mock_req:
            client.create_project("APPID:001", assets=[])
        body = mock_req.call_args[1]["json"]
        self.assertNotIn("project_type", body)

    def test_create_project_sends_parent_group_id(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 55}) as mock_req:
            client.create_project("APPID:001", assets=[], parent_group_id=202)
        body = mock_req.call_args[1]["json"]
        self.assertEqual(body["parent_group_id"], 202)

    def test_create_project_omits_parent_group_id_when_not_set(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 55}) as mock_req:
            client.create_project("APPID:001", assets=[])
        body = mock_req.call_args[1]["json"]
        self.assertNotIn("parent_group_id", body)

    def test_create_project_sends_business_impact(self):
        client = make_client()
        with patch.object(client, "_request", return_value={"id": 55}) as mock_req:
            client.create_project("APPID:001", assets=[], business_impact="High")
        body = mock_req.call_args[1]["json"]
        self.assertEqual(body["business_impact"], "High")


# ── ensure_group ───────────────────────────────────────────────────────────────

class TestEnsureGroup(unittest.TestCase):

    def test_existing_group_skips_creation(self):
        client = make_client()
        with patch.object(client, "find_group_by_name", return_value={"id": 101}):
            with patch.object(client, "create_group") as mock_create:
                result = ensure_group(client, "My Group", None, dry_run=False)
        mock_create.assert_not_called()
        self.assertEqual(result, 101)

    def test_new_group_creates(self):
        client = make_client()
        with patch.object(client, "find_group_by_name", return_value=None):
            with patch.object(client, "create_group", return_value={"id": 202}) as mock_create:
                result = ensure_group(client, "New Group", None, dry_run=False)
        mock_create.assert_called_once()
        self.assertEqual(result, 202)

    def test_dry_run_returns_none_id(self):
        client = make_client()
        with patch.object(client, "find_group_by_name", return_value=None):
            with patch.object(client, "create_group", return_value={"id": None, "_dry_run": True}):
                result = ensure_group(client, "New Group", None, dry_run=True)
        self.assertIsNone(result)

    def test_passes_parent_id_to_create(self):
        client = make_client()
        with patch.object(client, "find_group_by_name", return_value=None):
            with patch.object(client, "create_group", return_value={"id": 202}) as mock_create:
                ensure_group(client, "Sub Group", parent_id=101, dry_run=False)
        call_kwargs = mock_create.call_args[1]
        self.assertEqual(call_kwargs["parent_group_id"], 101)


# ── run() orchestration ────────────────────────────────────────────────────────

class TestRun(unittest.TestCase):

    def _make_client(self, resources=None, existing_project=None):
        client = make_client()
        client.list_labels = MagicMock(return_value=[])
        client.get_label_resources = MagicMock(return_value=resources or [])
        client.find_project_by_name = MagicMock(return_value=existing_project)
        client.create_project = MagicMock(return_value={"id": 1})
        return client

    def _run(self, client, labels=None, prefix=None, group=None, subgroup=None,
             project_type=None, group_type=None, dry_run=False):
        run(client, label_names=labels or [], label_prefix=prefix,
            group_name=group, subgroup_name=subgroup, business_impact="Medium",
            project_type=project_type, group_type=group_type, dry_run=dry_run)

    def test_skips_label_with_no_repos(self):
        client = self._make_client(resources=[])
        self._run(client, labels=["APPID:001"])
        client.create_project.assert_not_called()

    def test_skips_existing_project(self):
        client = self._make_client(
            resources=[{"resource_id": "uuid-1"}],
            existing_project={"id": 99, "name": "APPID:001"}
        )
        self._run(client, labels=["APPID:001"])
        client.create_project.assert_not_called()

    def test_creates_project_for_new_label(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        self._run(client, labels=["APPID:001"])
        client.create_project.assert_called_once()

    def test_continues_after_project_creation_error(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        client.create_project.side_effect = [RuntimeError("API error"), {"id": 2}]
        self._run(client, labels=["APPID:001", "APPID:002"])
        self.assertEqual(client.create_project.call_count, 2)

    def test_dry_run_passes_flag_to_create_project(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        client.create_project.return_value = {"id": None, "_dry_run": True}
        self._run(client, labels=["APPID:001"], dry_run=True)
        call_kwargs = client.create_project.call_args[1]
        self.assertTrue(call_kwargs["dry_run"])

    def test_builds_asset_list_from_resources(self):
        resources = [
            {"resource_id": "uuid-1"},
            {"resource_id": "uuid-2"},
            {"resource_id": "uuid-3"},
        ]
        client = self._make_client(resources=resources)
        self._run(client, labels=["APPID:001"])
        assets = client.create_project.call_args[1]["assets"]
        self.assertEqual(len(assets), 3)
        self.assertEqual(assets[0]["asset_id"], "uuid-1")
        self.assertEqual(assets[1]["asset_id"], "uuid-2")
        self.assertEqual(assets[2]["asset_id"], "uuid-3")

    def test_asset_fields_are_correct(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        self._run(client, labels=["APPID:001"])
        asset = client.create_project.call_args[1]["assets"][0]
        self.assertEqual(asset["id"], 0)
        self.assertEqual(asset["asset_type"], "Repository")
        self.assertEqual(asset["collisions_count"], 0)

    def test_uses_label_prefix_to_discover_labels(self):
        client = self._make_client()
        client.list_labels.return_value = [{"name": "APPID:001"}, {"name": "APPID:002"}]
        client.get_label_resources.return_value = []
        self._run(client, prefix="APPID:")
        client.list_labels.assert_called_once_with(prefix="APPID:")

    def test_exits_when_no_labels_and_no_prefix(self):
        client = self._make_client()
        with self.assertRaises(SystemExit):
            self._run(client, labels=[])

    def test_passes_project_type_to_create(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        self._run(client, labels=["APPID:001"], project_type="Application")
        call_kwargs = client.create_project.call_args[1]
        self.assertEqual(call_kwargs["project_type"], "Application")

    def test_creates_group_and_subgroup_in_order(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        with patch("create_groups_by_label.ensure_group", side_effect=[101, 202]) as mock_ensure:
            self._run(client, labels=["APPID:001"], group="My Group", subgroup="My Sub")
        self.assertEqual(mock_ensure.call_count, 2)

    def test_project_uses_subgroup_as_parent(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        with patch("create_groups_by_label.ensure_group", side_effect=[101, 202]):
            self._run(client, labels=["APPID:001"], group="My Group", subgroup="My Sub")
        call_kwargs = client.create_project.call_args[1]
        self.assertEqual(call_kwargs["parent_group_id"], 202)

    def test_processes_multiple_labels(self):
        client = self._make_client(resources=[{"resource_id": "uuid-1"}])
        self._run(client, labels=["APPID:001", "APPID:002", "APPID:003"])
        self.assertEqual(client.create_project.call_count, 3)


# ── main() credential validation ───────────────────────────────────────────────

class TestMainCredentialValidation(unittest.TestCase):

    def _run_main_with_env(self, env):
        with patch.dict(os.environ, env, clear=False):
            with patch("create_groups_by_label.load_dotenv"):
                with patch("sys.argv", ["script", "--label-prefix", "APPID:"]):
                    from create_groups_by_label import main
                    with self.assertRaises(SystemExit):
                        main()

    def test_missing_client_id_exits(self):
        self._run_main_with_env({"CYCODE_CLIENT_ID": "", "CYCODE_CLIENT_SECRET": "secret"})

    def test_missing_client_secret_exits(self):
        self._run_main_with_env({"CYCODE_CLIENT_ID": "id", "CYCODE_CLIENT_SECRET": ""})

    def test_placeholder_client_id_exits(self):
        self._run_main_with_env({"CYCODE_CLIENT_ID": "your_client_id_here", "CYCODE_CLIENT_SECRET": "secret"})

    def test_placeholder_client_secret_exits(self):
        self._run_main_with_env({"CYCODE_CLIENT_ID": "id", "CYCODE_CLIENT_SECRET": "your_client_secret_here"})


if __name__ == "__main__":
    unittest.main(verbosity=2)
