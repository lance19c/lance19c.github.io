The catalog workflow publishes the exact uploaded artifact through GitHub's Pages
REST API. The existing public metadata check must see the same artifact twice;
its ID must also equal upload-pages-artifact's documented `artifact_id` output.
The publisher independently checks the name, ID, run, workflow commit and numeric
repository identity before requesting a fresh OIDC token and creating deployment.

Run 45704809432 failed on November 25 after public REST metadata verification
succeeded: deploy-pages v5 reported zero artifacts. Its pinned implementation
uses `DefaultArtifactClient.listArtifacts()` without `findBy`, which queries the
internal Results/Twirp service with the runtime token and backend workflow/job
IDs. Public REST visibility cannot establish visibility in that separate service.
The underlying reason that the internal service returned an empty list remains
unknown; the upload itself is present as artifact 15064916798 for the exact run.

This publisher uses the same documented deployment endpoint and payload fields
as the official action after artifact discovery. It keeps the existing workflow
token, `github-pages` environment and permissions. Artifact revalidation prefers
that token's quota; if it receives 403, it makes one unauthenticated read of the
same public metadata endpoint, requiring no additional Actions permission.
The preceding readiness guard retains its existing unauthenticated reads. OIDC is requested
with the unmodified runner URL and default owner audience, as `core.getIDToken()`
does without an argument. Both documented subject formats are accepted, with
the exact owner/repository numeric IDs also required.

A private attempt record is written in RUNNER_TEMP before the sole deployment
POST. A timeout, failed response, malformed body or lost connection never causes
a second creation request. An existing attempt record blocks another invocation
for that same run/attempt/artifact. Only status GETs may retry, for at most 120
seconds and at most 25 checks; a 180-second OS deadline bounds the whole process.
Only `succeed` establishes publication. Unknown/failed deployment is reported
honestly and requires inspection; the script does not issue a cancellation POST.
No response body, token, OIDC value or authorization header is logged or saved.

The script is inert unless explicitly invoked with `--deploy`. Offline tests
inject all provider replies, including a real OS-deadline test with a fake
stalled creation call. `python -m unittest discover -s scripts -p 'test_*.py'`
runs the publisher checks along with the existing catalog tests.

Primary sources:

- [Pinned deploy-pages discovery and deployment implementation](https://github.com/actions/deploy-pages/blob/368f82528645a54fb793d4d04e342629a3f51346/src/internal/api-client.js)
- [Pinned deployment success, pending and failure handling](https://github.com/actions/deploy-pages/blob/368f82528645a54fb793d4d04e342629a3f51346/src/internal/deployment.js)
- [Pinned default OIDC call](https://github.com/actions/deploy-pages/blob/368f82528645a54fb793d4d04e342629a3f51346/src/index.js)
- [Official artifact client internal/public selection](https://github.com/actions/toolkit/blob/main/packages/artifact/src/internal/client.ts)
- [Official upload-pages-artifact output](https://github.com/actions/upload-pages-artifact/blob/v3/action.yml)
- [Pages create and status API](https://docs.github.com/en/rest/pages/pages?apiVersion=2026-03-10#create-a-github-pages-deployment)
- [Official API schema including deployment states](https://github.com/github/rest-api-description/blob/main/descriptions/api.github.com/api.github.com.json)
- [OIDC default audience, claims and immutable subjects](https://docs.github.com/en/actions/reference/security/oidc)
