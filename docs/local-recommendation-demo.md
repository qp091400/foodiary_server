# Local recommendation verification

No deployment service, remote database, existing SSH tunnel on port 8000, existing Expo process on 8081, or app `.env.local` was modified. The checks use a separate HTTP API on 127.0.0.1:8001, a separate Expo web process on localhost:8082, and SQLite under `artifacts/local-recommendation-demo/demo.sqlite3`. Database migrations through 0019 were applied to that local file only. The full catalogue/model are read-only local files.

## Start again
From foodiary_server, using the environment with requirements.txt installed:

```sh
PYTHONPATH=. /private/tmp/foodiary-ml-env/bin/python scripts/run_recommendation_demo.py
```

The temporary environment above was installed during this development session. A project virtual environment with requirements.txt works too. The launcher overrides DATABASE_URL explicitly to the isolated SQLite file, disables external email/push/link-preview/OCR work, and binds the API to loopback. It creates one explicitly labeled local demonstration account and a fridge containing eggs, onions and carrots. It seeds no ratings or selection events. Login: `localdemo` / `FoodiaryDemo2026!` (local disposable fixture only).

From foodiary_app:

```sh
npm run start:local
```

The launcher creates an ignored `.env.development.local` for API port 8001 and clears Metro cache. Existing `.env.local` and deployment configuration stay unchanged. Expo SDK 58 development bundles merge virtual dotenv modules after process variables, so process-only overrides / EXPO_NO_DOTENV alone did not reliably select the local API. The development-specific file fixes that precedence mismatch. The web app is http://localhost:8082. Loopback URLs are for this computer, not a physical phone.

## Reproduce the live API/app-code check
With the local API running, from foodiary_app:

```sh
node scripts/verify-local-recommendations.mjs
```

This executes the actual app ApiClient, personalized recommendation client, user embedding, MLP inference and explicit-feedback policy against the real local HTTP server. A fetch-origin assertion prevents any request to deployment. The CLI supplies in-memory model storage and Node UUID adapters for the native AsyncStorage/expo-crypto modules; these adapters do not replace network, embedding or ranking logic. Native device storage persistence and browser UI interaction are not covered.

The disposable localdemo account's reviews are cleared through its authenticated API at the start. The script then issues actual test view/select/review actions to this local-only account, verifies them, and deletes its test rating at the end. Test selection events remain as a genuine trace of the verification. These records are never used by the public-data training pipeline or sent to a remote service.

## Observed result
The first run returned 300 candidates and model version `16498301425cbef78805dc3645469390faee97e6ef9ce495a42dfa448c179a0a`. The test recipe was `10000recipe:6983262` (감자채전 바삭고소고소~~). One-based rank changed:

- Before feedback: 151
- After one selection: 56
- After five-star review: 1
- After changing the same review to one star: 300
- After deleting the rating: 56 (the real selection remains)

Confirmed: view does not affect preference; stable seed preserves the candidate set; duplicate selection UUID counts once; rating edit does not add a second vote; own rating is excluded from global-quality input; preference vector changes with the rating; cancellation removes only the rating; the model download is cached and reused. All 19 HTTP requests were confined to 127.0.0.1:8001. Detailed machine-readable evidence is in `docs/local-recommendation-check.json`.

Browser automation was declined. At the user's request, verification was limited to API and actual app code. No claim is made that the browser screen or a physical iOS/Android app was clicked through. Separate Expo web and local API servers were left running for manual inspection.


## Connection regression check
From foodiary_app, with the two local processes running:

```sh
node scripts/verify-local-app-connection.mjs
```

This fetches a diagnostic **build artifact** from Metro and executes its compiled ApiClient and virtual environment module in Node, without browser UI automation. The VM starts with an empty process environment, so this test does not merely echo shell overrides. It asserts that the actual development environment resolves API 127.0.0.1:8001, then authenticates localdemo and loads its fridge and the 32D model using the Metro-transformed app networking code. All API requests are guarded to the local origin. The initial issue was that a real browser on port 8082 used the value localhost:8000 from `.env.local`, even though the Expo process was launched with a shell variable pointing at 8001. This regression check catches that exact mismatch. Reload the browser completely after restarting Metro (Cmd+Shift+R).
