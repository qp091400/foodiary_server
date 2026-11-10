# Recommendation model in the existing data repository

- Encoder revision/text configuration: `recommendation-encoder.json`; reusable 32D PCA: `recommendation-projection.npz` (~50 KB), checksum pinned alongside weights. No retraining is needed to encode a new recipe with the identical coordinate space.
- Tracked small weights: `recommendation-model.json` (14,110 bytes).
- Pinned Release: `recommendation-package.json`.
- Data repository: `yuyunsj/foodiary-data` (existing private repository).
- Release: `recommendation-demo-v1`.
- Asset: `Foodiary-recommendation-demo-v1.zip` (~74 MB compressed).
- Contents: ranking weights, 235,203 Korean recipe embeddings, aggregate training report and manifest. No raw interaction records or Food.com CSVs.

The model remains a demonstration model: research promotion gates did not pass. The Release and training report retain that limitation. Uploading this asset does not deploy the running service.

## Prepare on a new checkout

```sh
# Existing gh login with access to the private data repository
python3 -m scripts.prepare_recipes
python3 -m scripts.prepare_recommendation_model
```

An already-downloaded asset works without GitHub credentials:

```sh
python3 -m scripts.prepare_recommendation_model --package /path/Foodiary-recommendation-demo-v1.zip
```

The prepared bundle is `recipe_data/recommendation-seed`, ignored by Git. The installer verifies the archive SHA256, tracked weights, coordinate-space version, recipe catalogue pin, SQLite integrity and embedding count. Only known files are extracted. A corrupt or mismatched package preserves the previous seed; matching caches skip download. A file lock and staged directory switch prevent partial installs.

## Build integration (no deployment executed here)

Cloud Run Docker builds verify and copy the prepared model into `/opt/recommendation-model` and set RECOMMENDATION_MODEL_DIR to that read-only image directory. Heavy training packages are not installed in the API image. Source packaging and Cloud Build upload allowlists include only the small model/pin JSONs.

The existing recipe-data workflow uses a versioned Cloud Build bucket cache. Recommendations now use the same pattern, keyed by package ZIP SHA256. `cloudrun.py build` prepares/downloads the Release when necessary, uploads the model seed once and passes its location to the build. Subsequent builds reuse it. The GitHub-triggered build reads the pin and fetches that cached model before Docker runs.

**One-time prerequisite for a new model version:** in the authenticated Cloud Shell, run the existing `python3 scripts/cloudrun.py build` workflow once to populate the new model's build-bucket cache. Only then can push-triggered builds fetch that version. This task did not upload to Google Cloud, build a cloud image, run remote migrations, or deploy/publish a service. A GitHub Release alone does not create that Cloud Build cache.

## Database merge

Upstream main already owns migrations 0016 (household), 0017 (removal) and 0018 (name corrections). Recommendation events now use **0019 → 0018**; there is one migration head. The isolated local demo launcher backs up and restamps only its known pre-merge local fixture database, preserving its existing events before applying the merged chain. No remote DB is touched.

## Update model

Publish a new immutable data Release, update both JSON pins, prepare and verify locally, then commit the small tracked files. Do not replace an existing asset under the same model version. Model weights and embedding coordinates must always match. Existing recipes-v4 is unaffected.
