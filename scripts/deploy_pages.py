"""Deploy one verified own-catalog artifact; inert without --deploy.

GitHub deploy-pages v5 lists artifacts through the internal Results service.
The upload REST readiness check cannot establish visibility in that service.
Use the documented Pages REST create endpoint with the exact verified/uploaded
artifact ID instead. No deployment POST is retried, including uncertain replies.
Provider errors and credentials never enter logs or the local attempt record.
"""
import argparse
import base64
import json
import os
from pathlib import Path
import re
import signal
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

REPOSITORY = "lance19c/lance19c.github.io"
REPOSITORY_ID = "1454524508"
OWNER_ID = "315144329"
API = "https://api.github.com"
PREFIX = API + "/repos/" + REPOSITORY
MAX_BYTES = 262144
TOTAL_SECONDS = 180
POLL_SECONDS = 120
PENDING = {"queued", "deployment_in_progress", "syncing_files", "finished_file_sync", "updating_pages", "purging_cdn",
           "unknown_status", "not_found", "deployment_attempt_error"}
FAILED = {"deployment_failed", "deployment_content_failed", "deployment_cancelled", "deployment_lost"}


class Held(Exception):
    """Only this fixed message may be printed by the command-line boundary."""


class Unavailable(Held):
    """A network failure; only GET status reconciliation may retry this."""


def need(value):
    if value is not True:
        raise Held("Website publication is unconfirmed; inspect this run before trying again.")


def credential(value):
    need(type(value) is str and 1 <= len(value) <= 32768 and all(33 <= ord(c) <= 126 for c in value))
    return value


def decode(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            need(key not in result)
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: need(False))


def context(env):
    need(env.get("GITHUB_ACTIONS") == "true" and env.get("GITHUB_REPOSITORY") == REPOSITORY
         and env.get("GITHUB_REF") == "refs/heads/main"
         and env.get("GITHUB_EVENT_NAME") in {"push", "workflow_dispatch", "schedule"}
         and env.get("GITHUB_SERVER_URL") == "https://github.com"
         and env.get("GITHUB_API_URL") == API)
    fields = ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT", "PAGES_ARTIFACT_ID", "PAGES_UPLOADED_ARTIFACT_ID")
    need(all(type(env.get(key)) is str and re.fullmatch(r"[1-9][0-9]{0,19}", env[key]) is not None for key in fields))
    need(env["PAGES_ARTIFACT_ID"] == env["PAGES_UPLOADED_ARTIFACT_ID"]
         and type(env.get("GITHUB_SHA")) is str and re.fullmatch(r"[0-9a-f]{40}", env["GITHUB_SHA"]) is not None)
    return {"run_id": env["GITHUB_RUN_ID"], "attempt": env["GITHUB_RUN_ATTEMPT"],
            "head_sha": env["GITHUB_SHA"], "artifact_id": int(env["PAGES_ARTIFACT_ID"]),
            "name": "github-pages-" + env["GITHUB_RUN_ID"] + "-" + env["GITHUB_RUN_ATTEMPT"]}


def artifact_matches(value, ctx):
    need(type(value) is dict and type(value.get("total_count")) is int and value["total_count"] == 1
         and type(value.get("artifacts")) is list and len(value["artifacts"]) == 1)
    item = value["artifacts"][0]
    need(type(item) is dict)
    run = item.get("workflow_run")
    need(type(item.get("id")) is int and item["id"] == ctx["artifact_id"] and item.get("name") == ctx["name"]
         and item.get("expired") is False and type(item.get("size_in_bytes")) is int and item["size_in_bytes"] > 0
         and type(run) is dict and type(run.get("id")) is int and run["id"] == int(ctx["run_id"])
         and run.get("head_sha") == ctx["head_sha"]
         and type(run.get("repository_id")) is int and str(run["repository_id"]) == REPOSITORY_ID
         and type(run.get("head_repository_id")) is int and str(run["head_repository_id"]) == REPOSITORY_ID)


def oidc_url(value):
    need(type(value) is str and 1 <= len(value) <= 8192 and not any(c.isspace() for c in value))
    u = urlsplit(value)
    need(u.scheme == "https" and u.hostname is not None and u.hostname.endswith(".actions.githubusercontent.com")
         and not u.username and not u.password and u.port is None and not u.fragment
         and bool(u.path) and not any(key == "audience" for key, _ in parse_qsl(u.query)))
    return value  # Same default audience as core.getIDToken() with no argument.


def oidc_token(value, ctx, now):
    need(type(value) is dict and type(value.get("value")) is str)
    token = value["value"]
    need(len(token) <= 32768 and re.fullmatch(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", token) is not None)
    middle = token.split(".")[1]
    claims = decode(base64.urlsafe_b64decode(middle + "=" * (-len(middle) % 4)))
    # These are correlation checks, not local signature verification. GitHub
    # verifies its token signature when creating the Pages deployment.
    need(type(claims) is dict and claims.get("iss") == "https://token.actions.githubusercontent.com"
         and claims.get("aud") == "https://github.com/lance19c"
         and claims.get("sub") in {"repo:" + REPOSITORY + ":environment:github-pages",
             "repo:lance19c@" + OWNER_ID + "/lance19c.github.io@" + REPOSITORY_ID + ":environment:github-pages"}
         and claims.get("environment") == "github-pages" and claims.get("repository") == REPOSITORY
         and claims.get("repository_id") == REPOSITORY_ID and claims.get("repository_owner_id") == OWNER_ID
         and claims.get("ref") == "refs/heads/main" and claims.get("sha") == ctx["head_sha"]
         and claims.get("run_id") == ctx["run_id"] and claims.get("run_attempt") == ctx["attempt"]
         and claims.get("workflow_ref") == REPOSITORY + "/.github/workflows/refresh.yml@refs/heads/main"
         and all(type(claims.get(k)) is int for k in ("iat", "nbf", "exp"))
         and -30 <= now - claims["iat"] <= 300 and claims["nbf"] <= now + 30 < claims["exp"])
    return token


class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, *_args, **_kwargs):
        return None


class Wire:
    def __init__(self, deadline, clock=time.monotonic):
        self.deadline, self.clock = deadline, clock
        self.opener = build_opener(ProxyHandler({}), NoRedirects())

    def request(self, method, url, token, *, data=None):
        remaining = self.deadline - self.clock()
        need(remaining > 0 and (token is None or type(token) is str and 1 <= len(token) <= 32768
             and not any(c.isspace() for c in token)))
        need(token is not None or method == "GET" and url.startswith(PREFIX + "/actions/runs/"))
        body = None if data is None else json.dumps(data, separators=(",", ":"), allow_nan=False).encode()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2026-03-10",
                   "User-Agent": "CowlingCatalog-Pages-exact-artifact"}
        if token is not None:
            headers["Authorization"] = "Bearer " + token
        if body is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, method=method, headers=headers, data=body)
        try:
            with self.opener.open(request, timeout=min(10, remaining)) as response:
                need(response.geturl() == url)
                raw = response.read(MAX_BYTES + 1)
                need(len(raw) <= MAX_BYTES)
                return response.status, decode(raw)
        except HTTPError as error:
            code = error.code
            error.close()
            return code, None
        except (URLError, OSError):
            raise Unavailable("Website publication is unconfirmed; inspect this run before trying again.") from None


def journal(path, value, *, exclusive=False):
    target = path if exclusive else path.with_suffix(".tmp")
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as out:
        json.dump(value, out, sort_keys=True, separators=(",", ":")); out.write("\n"); out.flush(); os.fsync(out.fileno())
    if not exclusive:
        os.replace(target, path)
    fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def deploy(env, wire, *, clock=time.monotonic, wall=time.time, sleep=time.sleep):
    ctx = context(env)
    marker = Path(env["RUNNER_TEMP"]) / ("cowling-pages-" + ctx["run_id"] + "-" + ctx["attempt"] + "-" + str(ctx["artifact_id"]) + ".json")
    need(not os.path.lexists(marker))
    token = credential(env.get("GITHUB_TOKEN"))
    request_token = credential(env.get("ACTIONS_ID_TOKEN_REQUEST_TOKEN"))
    metadata = PREFIX + "/actions/runs/" + ctx["run_id"] + "/artifacts?name=" + quote(ctx["name"], safe="") + "&per_page=100"
    # Prefer the existing token's quota. Public metadata remains readable if the
    # token's deliberately unchanged permission set lacks Actions-read access.
    status, value = wire.request("GET", metadata, token)
    if status == 403:
        status, value = wire.request("GET", metadata, None)
    need(status == 200); artifact_matches(value, ctx)
    status, value = wire.request("GET", oidc_url(env.get("ACTIONS_ID_TOKEN_REQUEST_URL")), request_token)
    need(status == 200)
    identity = oidc_token(value, ctx, wall())
    record = {"version": 1, "state": "CREATE_UNCONFIRMED", **ctx}
    journal(marker, record, exclusive=True)  # Durable marker BEFORE the only POST.
    status, value = wire.request("POST", PREFIX + "/pages/deployments", token,
                                 data={"artifact_id": ctx["artifact_id"], "pages_build_version": ctx["head_sha"],
                                       "environment": "github-pages", "oidc_token": identity})
    need(status == 200 and type(value) is dict)
    deployment_id = value.get("id")
    need((type(deployment_id) is int and deployment_id > 0)
         or (type(deployment_id) is str and re.fullmatch(r"[A-Za-z0-9_-]{1,80}", deployment_id) is not None))
    need(not any(secret in str(deployment_id) for secret in
                 (token, request_token, identity)))
    record.update(state="CREATED_UNCONFIRMED", deployment_id=deployment_id)
    journal(marker, record)
    deadline = clock() + POLL_SECONDS
    for _ in range(25):
        need(clock() < deadline)
        sleep(min(5, max(0, deadline - clock())))
        need(clock() < deadline)
        try:
            status, value = wire.request("GET", PREFIX + "/pages/deployments/" + str(deployment_id), token)
        except Unavailable:
            continue
        if status in {404, 429, 500, 502, 503, 504}:
            continue  # Bounded GET reconciliation only; never repeat creation.
        need(status == 200 and type(value) is dict)
        state = value.get("status")
        if state == "succeed":
            record["state"] = "SUCCEEDED"; journal(marker, record)
            return {"status": "succeed", "artifact_id": ctx["artifact_id"], "deployment_id": deployment_id}
        if state in FAILED:
            record["state"] = "FAILED"; journal(marker, record)
            need(False)
        need(state in PENDING)
    need(False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy", action="store_true")
    args = parser.parse_args()
    if not args.deploy:
        print('{"status":"LOCAL_ONLY","network_called":false}'); return
    def timeout(*_):
        need(False)
    signal.signal(signal.SIGALRM, timeout)
    signal.setitimer(signal.ITIMER_REAL, TOTAL_SECONDS)
    try:
        print(json.dumps(deploy(os.environ, Wire(time.monotonic() + TOTAL_SECONDS))))
    except Exception:
        print("Website publication is unconfirmed; inspect this run before trying again.")
        raise SystemExit(1) from None
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)


if __name__ == "__main__":
    main()
