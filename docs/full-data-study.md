# Full-data / Korean representation study — completed local demo

## Preregistered experiment
The initial protocol was written before validation scores were calculated and is retained as `ml/study_protocol.initial.json`. After the V1 validation baselines exposed a mismatch between AUC and user ranking, a documented validation-only amendment added candidate same-user pairwise supervision and ranking-based checkpoint selection. This happened before candidate training and before any final test evaluation. `ml/study_protocol.json` records the amendment; promotion gates remain unchanged. One pinned multilingual MiniLM encoder encodes the complete Food.com corpus and local Korean catalogue; no fake ratings or synthetic fridge labels are introduced. Corpus caches contain the original name+ingredient representation and a separately encoded cleaned title. Candidate representation combines unit-normalized title/body at fixed weights and learns a 32D PCA from English training recipes plus family-disjoint Korean training text. The recipe/user cosine + two-input lightweight MLP architecture is preserved.

The incumbent used only 12,000 recipes, trained through 2015-04-19, and selected its checkpoint using validation through 2017-02-21. Its untouched later window has only five users with 20 mixed-class observed candidates. Thus it cannot alone establish a robust ranking comparison. We also recreate V1 on 12,000 recipes and all Food.com recipes under the same new chronological split as the new candidates, using the original text/PCA/MLP approach. The exact deployed checkpoint is additionally compared on its untouched late window.

The new global date boundaries are 2010-04-24 and 2011-09-19 (70/10/20 percent of rated events, with no split inside a day). There are 180 validation and 264 test users with at least 20 unique observed candidates and both positive and negative labels. Test profiles/quality are frozen at the training cutoff, and target recipe ratings are excluded from their own profile/quality inputs. No unknown item is a negative label. Pointwise AUC and PR-AUC are accompanied by user-macro NDCG/Recall@10, deterministic label-independent tie breaking and paired user bootstrap confidence intervals.

Four initial representation settings were registered: title weight .65/Korean PCA share .25; title .65/Korean .50; title .30/Korean .25; and body-only/Korean .25. English/Korean PCA fitting uses at most 30K texts per domain, while MLP training uses every valid training interaction from the full dataset. V1 full baseline fits PCA on all its training recipes. The V1 sample replication fits PCA and MLP on the same deterministic 12K recipe sample, under the new time boundaries. All models use the entire available past to form evaluation user profiles, as the service can do.

Korean family fold is SHA256(existing dish_family) mod 10: folds 0..5 for unsupervised PCA, 6..7 for development, 8..9 for final test. Identical encoded titles within a family are deduplicated. At least two distinct titles are required per retrieval family, with up to four variants. This yields 230 development families / 479 candidates and 222 test families / 467 candidates. Equality of existing family labels is only a weak semantic proxy; it does not establish human semantic relevance or Korean recommendation uplift.

Candidate training combines class-weighted BCE with 0.05 times pairwise softplus loss, using five actual positive/negative pairs per training user. Candidates select the highest validation NDCG checkpoint (patience 5); V1 replications retain their original validation-BCE selection. Selection across candidate configurations uses validation ranking only among candidates improving Korean development Recall@10. The selected identity is sealed before opening test metrics. Promotion requires all predeclared gates: at least 30 eligible ranking users, positive paired-bootstrap 95% lower bounds versus both V1 replications, nondecreased AUC versus full V1, improved Korean test retrieval and nondecreased AUC versus the exact incumbent in its valid late window. If evidence fails these gates, the current application bundle stays active; test results must not be used to retune this study.

## Commands
Use the Python environment containing requirements-ml.txt plus API tests. Cache generation can resume after interruption, validating the complete corpus identity, pinned encoder revision and input texts.

```sh
python -m ml.cache_corpus ml/data/foodcom recipe_data/image-seed/recipes.sqlite3 ml/runs/full-study/cache --device mps
python -m ml.full_study train
python -m ml.full_study test
python -m ml.promote_study ml/runs/full-study artifacts/recommendation-full --node /path/to/node26
```

`--device cpu` is available without Apple acceleration. Promotion is fail-closed: it verifies the exact evaluated weights, performs TypeScript/PyTorch parity checks, packages the full Korean vectors, runs a real-bundle API smoke test, and only then changes the local development model path. A running API must restart to read the new environment path. No cloud deployment is performed. Source data license remains `Data files © Original Authors`.

## Outputs
Generated data are under ignored `ml/runs/full-study`. Each configuration retains its checkpoint, JSON weights, projection, full English/Korean vectors, validation report and learning curve. `selection.json` records the sealed validation decision; `comparison.json` records the independent final test and promotion gates. The source protocol and code, but not raw user records, are versionable. Final measured results will be recorded after the run completes.


## Validation-only extension (before final test)
The first four candidates with pairwise coefficient .05 improved Korean retrieval and validation user ranking, but reduced global validation AUC substantially. Their weights were not activated. Before any final test evaluation, four additional validation configurations were registered: title weights .65/.30 × pairwise coefficients 0/.005, all with Korean PCA share .25. These candidates choose validation ranking checkpoints only when AUC meets the full V1 validation baseline. Final selection applies the same validation AUC constraint. Original selection/protocol snapshots are retained as `selection.round1.json` and `protocol.round1.json`. The final-test and promotion gates are unchanged. This is adaptive validation model development, not a claim that every configuration was fixed before observing validation results.


## Final measured result and demonstration scope
The completed experiment uses all 231,637 Food.com recipes and 1,071,520 valid rated interactions, with 749,730 training events (720,761 binary labels), 107,434 validation events and 214,356 test events. The selected `title65_ko25` configuration has 32D vectors and the original 2→32→16→1 MLP. Complete final metrics are in `docs/full-data-comparison.json`.

| Same-split model | ROC-AUC | User NDCG@10 | Recall@10 | Korean weak-proxy Recall@10 |
|---|---:|---:|---:|---:|
| V1 12K replication | .643503 | .954099 | .239580 | .466809 |
| V1 full-data replication | .678304 | .946212 | .238703 | .463954 |
| Full-data title65/Ko25 | .586322 | .960024 | .240412 | .630621 |

Ranking includes 264 users, each with >=20 actual rated candidates and both classes; the pointwise test includes 206,171 binary labels. Paired user-bootstrap NDCG difference versus 12K V1 is +.005925, 95% CI [-.002213,.013843], so an advantage over that baseline is **not statistically established**. Versus full V1 the difference is +.013811, CI [.004517,.022846]. The exact original deployed checkpoint's untouched late-window AUC is .613820 versus candidate .597598. AUC regressed, and the conservative research promotion gate did not pass. Do not describe this as an across-the-board improvement or a production-approved model.

The user subsequently prioritized a functioning demonstration. Before final test evaluation, `demo-policy.json` recorded a separate local-demo choice based on validation ranking and Korean development retrieval. The strict research results remain unchanged (`promote=false`). Local demo activation requires a higher measured mean recommendation NDCG than sample V1, improved Korean retrieval, runtime parity and a real-bundle API smoke check. This is a demo tradeoff, not relaxed reporting of research success. The alternative E5 encoding experiment was interrupted when the user prioritized delivery; it is incomplete and was not selected or served.

## Final local architecture and later deployment
Following the user's workflow correction, the temporary old-server workaround, 16MB app-bundled embeddings and local-only selection ledger were removed. The app uses the new local/backend candidate, model and event APIs. Server-side embeddings stay in the read-only model index; only needed vectors and the small MLP are downloaded. Selection records are committed through authenticated DB/API calls, with the same idempotency key on retries. No event is reported successful if server recording fails. No Cloud Run service, remote database or deployed app was changed, and the existing app `.env.local` API address was not edited.

The app keeps candidate retrieval seeds stable across preference updates. A deterministic feedback policy respects a user's already-known negative/positive rating on that recipe, with a bounded boost for genuine selections; neutral ratings override implicit interest. This is serving logic, not a fabricated training label. Raw MLP test metrics above do not evaluate fridge or feedback postprocessing, and no held-out target rating was used as an evaluation input. Population MLP scores are not guaranteed monotone in cosine preference, so known explicit dissatisfaction must not be treated as a recommendation boost.

The completed local demo bundle is `artifacts/recommendation-full-demo`, version `16498301425cbef78805dc3645469390faee97e6ef9ce495a42dfa448c179a0a`. Local development `.env` points there; a local API restart reads that setting. `artifacts/foodiary-recommendation-source.tar.gz` contains deployable API code/migrations without credentials or training data. `artifacts/foodiary-recommendation-model.tar.gz` contains only weights, the 235,203-recipe vector index, manifest and training report. These files have been prepared locally, not uploaded. After local verification, the deployment step must transfer the source/model, apply migration 0016, configure the model directory and restart the target API together. Container deployments should provide the immutable model directory read-only. Existing cloud configuration was left untouched.

Local checks include API smoke with the actual full bundle, PyTorch/TypeScript numerical parity, the complete backend suite, app tests, Expo lint and TypeScript checks. Actual mobile hardware latency and live production deployment are not claimed.
