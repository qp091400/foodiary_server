# Foodiary personalized recommendation V1

## Architecture and compatibility
Offline Food.com CSV → pinned multilingual MiniLM → train-only PCA → normalized 32-dimensional recipe vectors. Past explicit ratings form a confidence-damped user vector. Its similarity to a recipe plus Bayesian global quality enter the 2→32→16→1 ReLU MLP (sigmoid at inference). Mobile TypeScript runs the exported MLP, then combines its output with ingredient coverage and expiry priority. No ONNX or native inference dependency is required; the existing Expo Go-compatible modules suffice.

The existing `/fridges/{id}/recipes/recommendations` route is unchanged. New `/personalized` retrieves a stable pool of at most 300 recipes under existing strict all-selected-ingredient rules and dish-family deduplication; the device reranks the complete returned pool. `pool_truncated` and the UI explicitly identify this retrieval limit. This is not a global personalized scan of all 235K recipes. The UI uses FlatList for the pool and retains legacy server pagination when the AI bundle is unavailable. Personal ratings, selections and embeddings are returned only to the authenticated fridge member; only that user's signals are included. Cross-account response state uses the user ID.

The offline bundle contains Foodiary recipe vectors in a read-only SQLite index and a 14KB ranking JSON. API workers never load PyTorch or the text encoder. Public model JSON is cached in the device's existing AsyncStorage, keyed by API environment and model version. Candidate/history vectors and personal profiles stay in memory and are reconstructed from authenticated server state. Full offline catalogue browsing is not implemented.

## Data and license verification
Correct source: https://www.kaggle.com/datasets/shuyangli94/food-com-recipes-and-user-interactions . Metadata was downloaded from the public Kaggle API on 2026-11-01; its license field is **Data files © Original Authors**, not CC0. See `docs/foodcom-source.json` and the retained original `ml/data/foodcom/source-metadata.json`. The original author repository points to Kaggle: https://github.com/majumderb/recipe-personalization . This verification identifies the license label, and does not establish unrestricted commercial training or model redistribution rights. The run and bundle are local research/development artifacts; production distribution requires resolving those permissions.

`RAW_recipes.csv` has 231,637 recipes. `RAW_interactions.csv` includes rating 0, which this experiment treats as unrated and excludes. After zero/orphan filtering, 1,071,520 valid rated events remain. No production or demonstration reviews are imported for training. Names and ingredient lists are the only encoder inputs. Safe literal parsing replaces eval; invalid dates/ratings and ambiguous duplicate user/recipe/day rows fail explicitly.

The first completed run deterministically samples 12,000 recipes by SHA256 of recipe ID (not popularity). It uses 52,194 train, 1,595 validation and 1,621 test events, split by global calendar day. Neutral ratings contribute history but not binary labels. Binary test evaluation has 1,553 samples. The sample experiment is not full-dataset training.

## Leakage and learning
Every training sample sees strictly earlier days, never same-day labels. Repeated user ratings replace their old contribution. The target recipe is excluded from the training user profile, and the user's prior target vote is removed from quality. Validation/test profiles and global ratings are frozen at the training cutoff; their labels never update inputs, PCA, weights or hyperparameters. PCA is fitted on training recipe IDs only. Unobserved recipes are never labeled negative. At serving time, known historical preferences can contribute to the current user's profile; own ratings are excluded from candidate quality.

Explicit rating weight is rating−3. Selection weight is min(0.5, 0.15*log(1+count)); an explicit rating replaces implicit interest, so repeated selections cannot override dislike or neutrality. Sparse profiles use mass/(mass+5) confidence. Cold and cancelling profiles score zero. Review deletion removes the explicit signal at the next refresh; any actual selections remain. Collaborative filtering and V2 fridge-feature training are intentionally absent.

PyTorch uses seed 42, AdamW lr=.001, batch 256, class-weighted BCEWithLogitsLoss, max 30 epochs, validation early stopping patience 5. The completed run stopped after 11 epochs, selecting the best validation checkpoint. Bayesian quality uses a neutral 3-star mean and strength 10; these are initial priors, not empirically optimal values. Fridge combination weights are configurable with `RECOMMENDATION_WEIGHTS=0.6,0.3,0.1`; this initial development setting has not been tuned on real fridge choices. No artificial fridge labels were used.

## Actual evaluation
| Model | ROC-AUC | PR-AUC |
|---|---:|---:|
| Bayesian quality | 0.491775 | 0.929422 |
| Embedding similarity | 0.518985 | 0.931495 |
| Ranking MLP | 0.543741 | 0.943278 |

Positive prevalence is 0.927882; high PR-AUC alone is not strong evidence. MLP NDCG@10=1.0 and Recall@10=0.999603 over observed rated candidates are highly inflated by small candidate sets: 1,407 users share only 1,553 test samples. Only one user has at least ten candidates and both labels. Its MLP NDCG@10 is 1.0 and Recall@10 is 0.833333; this is insufficient for a general ranking claim. See the complete `docs/foodcom-experiment.json`. MLP improved this sample's AUC, but no production uplift is established.

JSON/TypeScript vs selected PyTorch checkpoint: 1,000 feature pairs, maximum absolute difference 9.5007e-8. Mac/Node ran those 1,000 inferences in about 12ms; this is not an iOS/Android hardware measurement. Ranking JSON is 14,016 bytes; checkpoint is 5,269 bytes. Actual Korean catalogue semantic probes are saved separately in the run, including their full cosine matrix. They are qualitative diagnostics, not a fitted performance test. Actual pairs: 계란말이/계란찜≈0.8409 and 된장찌개/김치찌개≈0.8383, but 김치볶음밥/볶음밥≈0.1079 (below 김치볶음밥/과일샐러드≈0.4432). This exposes uneven Korean transfer and prevents claiming broad semantic quality. See `docs/foodcom-semantic-probes.json`.

## Actions and database
Migration `0016` adds private `recipe_events` with user ID, recipe ID, UTC selected_at, event_type, source, rating (audit only), and unique (user_id,event_key). `/recommendation-ai/events` accepts authenticated view/select events and UUID idempotency keys. Reusing a key with different content is 409. Identity is always taken from the access token; callers cannot supply user IDs or rating events. Model/profile endpoints require authentication and fridge access.

Opening details emits view, which never enters the preference signal. The explicit **이 요리 선택하기** button emits select; retries reuse the same UUID. A successful selection refreshes the current recommendation pool. Existing review writes record only new/changed ratings, and review/post/admin deletion records cancellation. Current RecipeReview rows remain the source of truth, avoiding duplicate rating aggregation. There is no cooking-completed event because no verified recipe-specific completion flow exists.

Expiry features reuse the existing ingredient normalizer and directional parent aliases. Expired items contribute neither availability nor urgency under the existing exclusion policy; this is not a food-safety diagnosis. Unknown dates contribute availability only. Quantity is excluded. All current active fridge foods contribute coverage/urgency; selected foods still determine candidate eligibility.

## Reproduce and install
From foodiary_server (commands can use an existing virtual environment):

```sh
python3 -m venv .venv-ml
.venv-ml/bin/pip install -r requirements-ml.txt
.venv-ml/bin/python scripts/download_foodcom.py ml/data/foodcom
.venv-ml/bin/python -m ml.pipeline ml/data/foodcom ml/runs/foodcom-v1 --max-recipes 12000 --encoder-revision e8f8c211226b894fcb81acc59f3b34ba3efd5f42
.venv-ml/bin/python -m ml.verify_runtime ml/runs/foodcom-v1 ../foodiary_app/src/lib/recommendation/inference.ts
.venv-ml/bin/python -m ml.semantic_probe ml/runs/foodcom-v1 recipe_data/image-seed/recipes.sqlite3
.venv-ml/bin/python -m ml.package_bundle ml/runs/foodcom-v1 recipe_data/image-seed/recipes.sqlite3 artifacts/recommendation-v1 --device cpu
.venv-ml/bin/alembic upgrade head
```

Node must support TypeScript stripping (tested with Node 26); `--node /path/to/node` selects it. `--max-recipes 0` runs the full dataset; use a new run directory. Archive `ml/environment.lock.txt` (actual development environment, includes API and ML tools) and dataset SHA256s. CSVs, learned artifacts and caches are ignored by Git. Cache validation includes IDs, revision and input text hash.

For an existing production/development process, first migrate its database, then set `RECOMMENDATION_MODEL_DIR` to the completed immutable bundle directory and restart the API. An unset path or unavailable bundle preserves legacy recommendations. In Docker, mount that bundle read-only and configure its container path; do not include ml/data, ml/runs or training dependencies in the API image. Model manifest hashes protect weights and embedding index, and coordinate-space versions must match. Package output directories must be new; never overwrite a serving bundle. Rollback means switching the path to a previous complete version or leaving it empty. Cloud deployment has not been performed.

## Verification and limits
The completed checks include 938 backend tests passed / 9 skipped, 160 app tests passed, Expo lint and TypeScript, temporary SQLite migration upgrade/downgrade/re-upgrade, authentication/idempotency/own-rating exclusion/fridge-change and expiry tests, and numerical inference parity. PostgreSQL migration and physical mobile device performance still need staging verification. Full-dataset model training, confidence intervals, richer candidate ranking evaluation, systematic bilingual semantic validation, real fridge-weight calibration, and subsequent V2/CF training remain future work. Retraining runs separately from requests. Model updates require fresh offline bundles; the app fetches the version indicated by each candidate response.


## Completed local installation
The full serving bundle at `artifacts/recommendation-v1` contains 235,203 Foodiary embeddings. The development `.env` now sets its absolute `RECOMMENDATION_MODEL_DIR`; restart the local API to load it. The checked local settings use APP_ENV=development and AUTO_CREATE_TABLES=true, so the new table is created by existing startup handling; production must use Alembic. No running API was restarted and no remote database was modified.

An opt-in smoke test used the real trained weights, full catalogue and generated index in an isolated SQLite test database. It returned 300 eligible candidates, all with compatible vectors, and verified that a genuine select request changes only the caller's signals. The complete initial pool + select + refreshed pool + model sequence took about 1.56s on the development machine. Run it with `RECOMMENDATION_TEST_BUNDLE=/absolute/bundle RECOMMENDATION_TEST_CATALOGUE=/absolute/catalogue-dir python -m pytest tests/test_recommendation_real_bundle.py -q -s`. This test is skipped in ordinary runs without generated artifacts. The final complete suite result is 938 passed / 9 skipped; the real-bundle opt-in test passed separately.


## Subsequent full-data local demo
See `docs/full-data-study.md` for the completed full-data study and measured limitations. Local development now uses `artifacts/recommendation-full-demo`; the initial V1 bundle remains available for rollback. All work and prepared source/model archives remain local. No remote service or database was updated. The mobile app uses server-delivered vectors and weights; the temporary bundled-vector/old-server bypass was removed.
