"""Offline deployment boundary tests. No credentials or provider calls."""
import base64
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from urllib.error import HTTPError, URLError

SPEC = importlib.util.spec_from_file_location("pages", Path(__file__).with_name("deploy_pages.py"))
pages = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pages)


def token(claims):
    segment = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return "header." + segment + ".signature"


class Fake:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def request(self, method, url, credential, **kwargs):
        self.calls.append((method, url, credential, kwargs))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class DeploymentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"GITHUB_ACTIONS": "true", "GITHUB_REPOSITORY": pages.REPOSITORY, "GITHUB_REF": "refs/heads/main",
                    "GITHUB_EVENT_NAME": "push", "GITHUB_SERVER_URL": "https://github.com", "GITHUB_API_URL": pages.API,
                    "GITHUB_RUN_ID": "123", "GITHUB_RUN_ATTEMPT": "2", "GITHUB_SHA": "a" * 40,
                    "PAGES_ARTIFACT_ID": "456", "PAGES_UPLOADED_ARTIFACT_ID": "456", "RUNNER_TEMP": self.tmp.name,
                    "GITHUB_TOKEN": "PRIVATE_GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "PRIVATE_REQUEST_TOKEN",
                    "ACTIONS_ID_TOKEN_REQUEST_URL": "https://pipelines.actions.githubusercontent.com/fixture/idtoken?api-version=2.0"}
        self.ctx = pages.context(self.env)
        self.metadata = {"total_count": 1, "artifacts": [{"id": 456, "name": "github-pages-123-2", "size_in_bytes": 12,
                         "expired": False, "workflow_run": {"id": 123, "head_sha": "a" * 40,
                          "repository_id": int(pages.REPOSITORY_ID), "head_repository_id": int(pages.REPOSITORY_ID)}}]}
        self.claims = {"iss": "https://token.actions.githubusercontent.com", "aud": "https://github.com/lance19c",
                       "sub": "repo:" + pages.REPOSITORY + ":environment:github-pages", "environment": "github-pages",
                       "repository": pages.REPOSITORY, "ref": "refs/heads/main", "sha": "a" * 40, "run_id": "123",
                       "repository_id": pages.REPOSITORY_ID, "repository_owner_id": pages.OWNER_ID,
                       "run_attempt": "2", "workflow_ref": pages.REPOSITORY + "/.github/workflows/refresh.yml@refs/heads/main",
                       "iat": 1000, "nbf": 1000, "exp": 1300}
        self.clock = 0

    def sleep(self, seconds):
        self.assertGreater(seconds, 0)
        self.assertLessEqual(seconds, 5)
        self.clock += seconds

    def run_deploy(self, wire):
        return pages.deploy(self.env, wire, clock=lambda: self.clock, wall=lambda: 1000, sleep=self.sleep)

    def responses(self, *statuses):
        return [(200, self.metadata), (200, {"value": token(self.claims)}), (200, {"id": "a" * 40})] + list(statuses)

    def marker(self):
        return Path(self.tmp.name) / "cowling-pages-123-2-456.json"

    def test_default_command_is_inert(self):
        result = subprocess.run([sys.executable, str(Path(pages.__file__))], capture_output=True, text=True, timeout=5)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(json.loads(result.stdout), {"status": "LOCAL_ONLY", "network_called": False})

    def test_exact_rest_upload_oidc_and_single_deployment(self):
        wire = Fake(self.responses((200, {"status": "queued"}), (200, {"status": "succeed"})))
        self.assertEqual(self.run_deploy(wire)["status"], "succeed")
        posts = [c for c in wire.calls if c[0] == "POST"]
        self.assertEqual(len(posts), 1)
        self.assertEqual(posts[0][1], pages.PREFIX + "/pages/deployments")
        self.assertEqual(posts[0][3]["data"], {"artifact_id": 456, "environment": "github-pages",
                        "pages_build_version": "a" * 40, "oidc_token": token(self.claims)})
        self.assertEqual(wire.calls[1][1], self.env["ACTIONS_ID_TOKEN_REQUEST_URL"])
        self.assertNotIn("audience=", wire.calls[1][1])
        self.assertEqual(stat.S_IMODE(self.marker().stat().st_mode), 0o600)
        self.assertEqual(json.loads(self.marker().read_text())["state"], "SUCCEEDED")
        for secret in (self.env["GITHUB_TOKEN"], self.env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"], token(self.claims)):
            self.assertNotIn(secret, self.marker().read_text())

    def test_unknown_creation_records_before_post_and_never_retries(self):
        wire = Fake(self.responses()[:2] + [URLError("PRIVATE_PROVIDER_ERROR")])
        with self.assertRaises(Exception):
            self.run_deploy(wire)
        self.assertEqual(json.loads(self.marker().read_text())["state"], "CREATE_UNCONFIRMED")
        self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)
        with self.assertRaises(pages.Held):
            self.run_deploy(wire)
        self.assertEqual(len(wire.calls), 3)

    def test_rejected_create_status_has_one_post_and_no_poll(self):
        for status in (302, 400, 401, 403, 404, 409, 422, 429, 500, 503):
            with self.subTest(status=status):
                self.marker().unlink(missing_ok=True)
                wire = Fake(self.responses()[:2] + [(status, {"secret": "PRIVATE"})])
                with self.assertRaises(pages.Held):
                    self.run_deploy(wire)
                self.assertEqual(len(wire.calls), 3)
                self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_invalid_create_reply_keeps_unknown_without_retry(self):
        for reply in (None, [], {}, {"id": True}, {"id": 0}, {"id": "../elsewhere"}, {"id": "PRIVATE_GITHUB_TOKEN"}):
            with self.subTest(reply=reply):
                self.marker().unlink(missing_ok=True)
                wire = Fake(self.responses()[:2] + [(200, reply)])
                with self.assertRaises(pages.Held):
                    self.run_deploy(wire)
                self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)
                self.assertEqual(json.loads(self.marker().read_text())["state"], "CREATE_UNCONFIRMED")

    def test_transient_get_failures_only_repeat_get(self):
        statuses = [(code, None) for code in (404, 429, 500, 502, 503, 504)] + [(200, {"status": "succeed"})]
        wire = Fake(self.responses(*statuses))
        self.assertEqual(self.run_deploy(wire)["status"], "succeed")
        self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)
        self.assertLessEqual(self.clock, pages.POLL_SECONDS)

    def test_network_status_failure_retries_get_without_recreating(self):
        wire = Fake(self.responses(pages.Unavailable("PRIVATE_ERROR"), (200, {"status": "succeed"})))
        self.assertEqual(self.run_deploy(wire)["status"], "succeed")
        self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_public_metadata_prefers_existing_token_quota(self):
        wire = Fake(self.responses((200, {"status": "succeed"})))
        self.run_deploy(wire)
        self.assertEqual(wire.calls[0][2], self.env["GITHUB_TOKEN"])
        self.assertEqual(wire.calls[1][2], self.env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"])

    def test_public_metadata_falls_back_once_if_existing_token_lacks_actions_read(self):
        wire = Fake([(403, None)] + self.responses((200, {"status": "succeed"})))
        self.assertEqual(self.run_deploy(wire)["status"], "succeed")
        self.assertEqual(wire.calls[0][2], self.env["GITHUB_TOKEN"])
        self.assertIsNone(wire.calls[1][2])
        self.assertEqual(wire.calls[0][1], wire.calls[1][1])
        self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_public_fallback_does_not_repeat_or_expand_permissions(self):
        wire = Fake([(403, None), (403, None)])
        with self.assertRaises(pages.Held): self.run_deploy(wire)
        self.assertEqual(len(wire.calls), 2)
        self.assertTrue(all(c[0] == "GET" and "/artifacts?" in c[1] for c in wire.calls))

    def test_missing_or_invalid_credential_blocks_all_requests(self):
        for key in ("GITHUB_TOKEN", "ACTIONS_ID_TOKEN_REQUEST_TOKEN"):
            old = self.env[key]
            for value in (None, "", "bad\nvalue", "a\x00b"):
                with self.subTest(key=key, value=value):
                    self.env[key] = value; wire = Fake([])
                    with self.assertRaises(pages.Held): self.run_deploy(wire)
                    self.assertEqual(wire.calls, [])
            self.env[key] = old

    def test_new_repository_immutable_subject_is_supported_and_id_bound(self):
        claims = dict(self.claims, sub="repo:lance19c@" + pages.OWNER_ID + "/lance19c.github.io@" + pages.REPOSITORY_ID + ":environment:github-pages")
        self.assertEqual(pages.oidc_token({"value": token(claims)}, self.ctx, 1000), token(claims))
        for key in ("repository_id", "repository_owner_id", "sub"):
            bad = dict(claims); bad[key] += "1"
            with self.subTest(key=key), self.assertRaises(pages.Held):
                pages.oidc_token({"value": token(bad)}, self.ctx, 1000)

    def test_all_documented_pending_states_wait_for_actual_success(self):
        statuses = [(200, {"status": state}) for state in sorted(pages.PENDING)] + [(200, {"status": "succeed"})]
        wire = Fake(self.responses(*statuses))
        self.assertEqual(self.run_deploy(wire)["status"], "succeed")
        self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_modified_artifact_or_run_proof_blocks_before_identity_or_post(self):
        for key, value in (("id", True), ("id", 999), ("name", "another"), ("size_in_bytes", 0), ("expired", True)):
            bad = deepcopy(self.metadata); bad["artifacts"][0][key] = value
            wire = Fake([(200, bad)])
            with self.subTest(key=key, value=value), self.assertRaises(pages.Held): self.run_deploy(wire)
            self.assertEqual(len(wire.calls), 1)
        for key, value in (("id", True), ("id", 124), ("head_sha", "b" * 40), ("repository_id", 1), ("head_repository_id", 2)):
            bad = deepcopy(self.metadata); bad["artifacts"][0]["workflow_run"][key] = value
            wire = Fake([(200, bad)])
            with self.subTest(key=key), self.assertRaises(pages.Held): self.run_deploy(wire)
            self.assertEqual(len(wire.calls), 1)

    def test_terminal_failure_does_not_report_success(self):
        for state in pages.FAILED:
            with self.subTest(state=state):
                self.marker().unlink(missing_ok=True)
                wire = Fake(self.responses((200, {"status": state})))
                with self.assertRaises(pages.Held):
                    self.run_deploy(wire)
                self.assertEqual(json.loads(self.marker().read_text())["state"], "FAILED")

    def test_unknown_status_or_authorization_failure_stops(self):
        for status, reply in ((200, {}), (200, {"status": "unrecognized"}), (200, {"status": True}), (403, None), (302, None)):
            with self.subTest(status=status, reply=reply):
                self.marker().unlink(missing_ok=True)
                wire = Fake(self.responses((status, reply)))
                with self.assertRaises(pages.Held):
                    self.run_deploy(wire)
                self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_never_ready_stops_within_poll_deadline(self):
        wire = Fake(self.responses(*[(200, {"status": "deployment_in_progress"})] * 30))
        with self.assertRaises(pages.Held):
            self.run_deploy(wire)
        self.assertLessEqual(self.clock, 120)
        self.assertLessEqual(len(wire.calls), 27)
        self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_changed_artifact_identity_blocks_before_oidc_and_post(self):
        changes = ({"id": 789}, {"id": True}, {"name": "github-pages-123-1"}, {"expired": True},
                   {"size_in_bytes": 0}, {"size_in_bytes": True}, {"workflow_run": {"id": 124, "head_sha": "a" * 40}},
                   {"workflow_run": {"id": 123, "head_sha": "b" * 40}})
        for change in changes:
            with self.subTest(change=change):
                value = deepcopy(self.metadata); value["artifacts"][0].update(change)
                wire = Fake([(200, value)])
                with self.assertRaises(pages.Held):
                    self.run_deploy(wire)
                self.assertEqual(len(wire.calls), 1)
                self.assertFalse(self.marker().exists())

    def test_malformed_or_ambiguous_metadata_is_held(self):
        for value in (None, [], {}, {"total_count": True, "artifacts": []}, {"total_count": 0, "artifacts": []},
                      {"total_count": 2, "artifacts": self.metadata["artifacts"] * 2}):
            with self.subTest(value=value), self.assertRaises(pages.Held):
                pages.artifact_matches(value, self.ctx)

    def test_wrong_workflow_context_is_held_before_network(self):
        changes = {"GITHUB_ACTIONS": "false", "GITHUB_REPOSITORY": "other/repo", "GITHUB_REF": "refs/pull/1/merge",
                   "GITHUB_EVENT_NAME": "pull_request", "GITHUB_SERVER_URL": "https://elsewhere.test",
                   "GITHUB_API_URL": "https://elsewhere.test", "GITHUB_RUN_ID": "0", "GITHUB_RUN_ATTEMPT": "1\n",
                   "PAGES_ARTIFACT_ID": "789", "PAGES_UPLOADED_ARTIFACT_ID": "0456", "GITHUB_SHA": "b" * 39}
        for key, value in changes.items():
            with self.subTest(key=key):
                env = dict(self.env); env[key] = value; wire = Fake([])
                with self.assertRaises(pages.Held):
                    pages.deploy(env, wire)
                self.assertEqual(wire.calls, [])

    def test_oidc_default_audience_and_exact_claims_required(self):
        changes = {"iss": "https://elsewhere.test", "aud": "other", "sub": "repo:other/repo:environment:github-pages",
                   "environment": "other", "repository": "other/repo", "ref": "refs/heads/other", "sha": "b" * 40,
                   "run_id": "124", "run_attempt": "1", "workflow_ref": "other", "iat": 500, "nbf": 2000,
                   "exp": 999}
        for key, value in changes.items():
            with self.subTest(key=key):
                claims = dict(self.claims); claims[key] = value
                with self.assertRaises(pages.Held):
                    pages.oidc_token({"value": token(claims)}, self.ctx, 1000)

    def test_oidc_url_cannot_send_credential_to_another_origin_or_redirect(self):
        for url in ("http://pipelines.actions.githubusercontent.com/idtoken", "https://evil.test/idtoken",
                    "https://evilactions.githubusercontent.com/idtoken", "https://a.actions.githubusercontent.com.evil.test/idtoken",
                    "https://u:p@a.actions.githubusercontent.com/idtoken", "https://a.actions.githubusercontent.com:443/idtoken",
                    "https://a.actions.githubusercontent.com/idtoken#fragment", "https://a.actions.githubusercontent.com/idtoken?audience=other"):
            with self.subTest(url=url), self.assertRaises(pages.Held):
                pages.oidc_url(url)

    def test_duplicate_json_keys_and_nonfinite_values_rejected(self):
        for raw in ('{"status":"succeed","status":"failed"}', '{"value":NaN}', '{"value":Infinity}'):
            with self.subTest(raw=raw), self.assertRaises(pages.Held):
                pages.decode(raw)

    def test_wire_uses_no_proxy_no_redirect_and_bounded_request(self):
        wire = pages.Wire(100, clock=lambda: 0)
        calls = []
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_): pass
            def geturl(self): return pages.API + "/fixed"
            def read(self, maximum):
                self.maximum = maximum
                return b'{"status":"succeed"}'
        class Opener:
            def open(self, request, **kwargs):
                calls.append((request, kwargs)); return Response()
        wire.opener = Opener()
        self.assertEqual(wire.request("GET", pages.API + "/fixed", "PRIVATE_TOKEN")[0], 200)
        self.assertEqual(calls[0][1], {"timeout": 10})
        self.assertEqual(pages.NoRedirects().redirect_request(None), None)
        self.assertEqual(calls[0][0].get_header("Authorization"), "Bearer PRIVATE_TOKEN")

    def test_wire_rejects_redirected_response_and_oversize(self):
        for url, body in (("https://evil.test/", b'{}'), (pages.API, b"x" * (pages.MAX_BYTES + 1))):
            with self.subTest(url=url):
                class Response:
                    status = 200
                    def __enter__(self): return self
                    def __exit__(self, *_): pass
                    def geturl(self): return url
                    def read(self, maximum): return body
                wire = pages.Wire(100, clock=lambda: 0)
                wire.opener = type("Opener", (), {"open": lambda *a, **k: Response()})()
                with self.assertRaises(pages.Held):
                    wire.request("GET", pages.API, "PRIVATE")

    def test_local_marker_existing_or_symlink_blocks_all_calls(self):
        self.marker().symlink_to(Path(self.tmp.name) / "missing")
        wire = Fake([])
        with self.assertRaises(pages.Held):
            self.run_deploy(wire)
        self.assertEqual(wire.calls, [])

    def test_unknown_creation_reply_cannot_be_resubmitted(self):
        wire = Fake([(200, self.metadata), (200, {"value": token(self.claims)}), pages.Unavailable("PRIVATE_WIRE_ERROR")])
        with self.assertRaises(pages.Held): self.run_deploy(wire)
        marker = next(Path(self.tmp.name).glob("cowling-pages-*.json"))
        self.assertEqual(json.loads(marker.read_text())["state"], "CREATE_UNCONFIRMED")
        again = Fake([])
        with self.assertRaises(pages.Held): self.run_deploy(again)
        self.assertEqual(again.calls, [])
        self.assertNotIn("PRIVATE", marker.read_text())
        self.assertEqual(stat.S_IMODE(marker.stat().st_mode), 0o600)

    def test_provider_credential_echo_cannot_enter_journal_or_status_url(self):
        for credential in (self.env["GITHUB_TOKEN"], self.env["ACTIONS_ID_TOKEN_REQUEST_TOKEN"]):
            with self.subTest(credential=credential):
                wire = Fake([(200, self.metadata), (200, {"value": token(self.claims)}), (200, {"id": "prefix" + credential})])
                with self.assertRaises(pages.Held): self.run_deploy(wire)
                marker = next(Path(self.tmp.name).glob("cowling-pages-*.json"))
                self.assertNotIn(credential, marker.read_text()); marker.unlink()
                self.assertEqual(sum(c[0] == "POST" for c in wire.calls), 1)

    def test_real_os_deadline_interrupts_stalled_creation_without_secret_output(self):
        program = (
            "import importlib.util,json,os,sys,time\n"
            "s=importlib.util.spec_from_file_location('pages'," + repr(str(Path(pages.__file__).resolve())) + ");m=importlib.util.module_from_spec(s);s.loader.exec_module(m)\n"
            "os.environ.clear();os.environ.update(" + repr(self.env) + ")\n"
            "class Fake:\n"
            " def request(self,method,url,credential,**kwargs):\n"
            "  if method=='POST':time.sleep(10)\n"
            "  return (200," + repr(self.metadata) + ") if '/artifacts?' in url else (200,{'value':" + repr(token(self.claims)) + "})\n"
            "m.Wire=lambda *a:Fake();m.TOTAL_SECONDS=1\n"
            "deploy=m.deploy;m.deploy=lambda env,wire:deploy(env,wire,wall=lambda:1000)\n"
            "sys.argv=['pages','--deploy'];m.main()\n"
        )
        result = subprocess.run([sys.executable, "-c", program], text=True, capture_output=True, timeout=4)
        self.assertEqual(result.returncode, 1)
        self.assertIn("publication is unconfirmed", result.stdout)
        self.assertNotIn("PRIVATE", result.stdout + result.stderr)
        marker = next(Path(self.tmp.name).glob("cowling-pages-*.json"))
        self.assertEqual(json.loads(marker.read_text())["state"], "CREATE_UNCONFIRMED")

    def test_workflow_binds_both_ids_and_preserves_existing_permissions_and_limits(self):
        workflow = Path(pages.__file__).parents[1] / ".github/workflows/refresh.yml"
        text = workflow.read_text()
        for required in ("PAGES_ARTIFACT_ID: ${{ steps.verified_site.outputs.artifact_id }}",
                         "PAGES_UPLOADED_ARTIFACT_ID: ${{ steps.upload_site.outputs.artifact_id }}",
                         "GITHUB_TOKEN: ${{ github.token }}", "run: python3 scripts/deploy_pages.py --deploy",
                         "output.write(f\"artifact_id={current}\\n\")", "environment: github-pages", "timeout-minutes: 5",
                         "cron: '17 */6 * * *'", "contents: write\n  pages: write\n  id-token: write"):
            with self.subTest(required=required): self.assertIn(required, text)
        self.assertNotIn("actions: write", text)
        self.assertNotIn("actions/deploy-pages@", text)


if __name__ == "__main__":
    unittest.main()
