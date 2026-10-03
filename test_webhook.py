"""Quick webhook signature test."""

import hmac, hashlib, json, urllib.request, urllib.error

secret = "change-me-please"
body = json.dumps({"ref": "refs/heads/main"}).encode()
sig = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
req = urllib.request.Request(
    "http://127.0.0.1:8000/webhook/github",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": sig,
    },
)
try:
    print(urllib.request.urlopen(req).read().decode())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode())

# Now test with bad signature
req2 = urllib.request.Request(
    "http://127.0.0.1:8000/webhook/github",
    data=body,
    headers={
        "Content-Type": "application/json",
        "X-GitHub-Event": "ping",
        "X-Hub-Signature-256": "sha256=deadbeef",
    },
)
try:
    print(urllib.request.urlopen(req2).read().decode())
except urllib.error.HTTPError as e:
    print("HTTP", e.code, e.read().decode())
