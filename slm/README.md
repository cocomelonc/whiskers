# whiskers mini TI SLM

Independent Whiskers mini TI model work, with a web panel and an Android chat PoC.

**Verified 2026-11-17:** independent LoRA training on L20, GGUF export, deterministic data preparation, offline web integration, and Android UI on Motorola moto g54 5G. The own model took **894.93 seconds** for training/validation/merge, with **4,303 MiB peak PyTorch allocated memory**. The full-model APK is installed on Motorola moto g54 5G, and real native inference is verified. See [training summary](evidence/training-summary.json).

## Scope and independence

The first SLM is a small **evidence selector** for TI, integrated into the family card. It chooses relevant source passages for a question or abstains; Python renders the original passages. This makes the application inspectable and useful without asking a 0.6B model to memorize live threat intelligence.

| Component | Whiskers ownership |
|---|---|
| Dataset, split, questions | `slm/data/`, `slm/training/`; independent preparation and seed |
| Adapter / merged weights / GGUF | fresh `/data/whiskers-slm/<run>/` on the GPU server; `slm/artifacts/` locally |
| Runtime | own model alias `whiskers-ti-0.6b-v0.1`, port 8082, `WHISKERS_SLM_*` |
| Integration | `/api/slm/*` and the **whiskers mini TI** panel |
| Evaluation | frozen family/alias groups, separate manual questions, separate scores |
| Existing Whiskers AI | Ollama brief/explain/ask and the worker keep their current paths and model settings |

Whiskers uses assistant-only LoRA training, frozen public source data, pinned original Qwen weights and a pinned converter. Its dataset, adapter, GGUF, runtime identity and evaluation records are stored in the paths above.

## Capabilities

- English questions about the selected family's description, aliases, listed actor associations and record date.
- A short brief from the first description passages, with source links.
- Literal YARA metadata/condition evidence and the explicit limitation that text alone does not establish sample coverage or false-positive rate.
- Unknown answers for missing indicators, live C2 requests, or claims about a local machine without observations.
- Export of the question, source-selected answer, evidence IDs/links, model alias, evidence digest and timing as JSON.

The SLM generates a selection such as `{"abstain":false,"evidence_ids":["description:2"]}`. It does **not** generate the displayed source text. JSON Schema constrains IDs, and a second Python validator rejects unknown IDs, extra prose, duplicate IDs, inconsistent abstentions and incomplete responses. A valid ID can still be irrelevant: semantic correctness and abstention must be measured separately. This is not a prompt-injection guarantee or a current malware detector.

YARA export in Whiskers continues to export original source rules. Generating new YARA/Sigma/Suricata rules is a later, separately evaluated capability; the application does not advertise it as implemented.

## Real source dataset

Acquired anonymously on **2026-11-17**: **3,843 public Malpedia family records**, 4,284,649 bytes. Snapshot SHA-256: `eb735ce0c97faeddf29921528d6bdea5266957a82114ac9289af89159bd8fd94`. The version endpoint separately returned version 26649, dated 2026-11-09; the two requests are not an atomic transaction. Never interpret a record edit or acquisition time as a recent malware observation.

The independent dataset uses 240 eligible described families plus selected public rules from other eligible family groups. Questions and gold selections are programmatically derived from actual source fields; behavior questions use keyword-based weak labels. These are **not 2,000 expert-authored TI answers**. Six demo families and their known alias-connected records are excluded from train/validation/test.

| Split | Rows | Use |
|---|---:|---|
| Train | 1,906 | LoRA SFT |
| Validation | 255 | Training/development decisions |
| Test | 236 | Frozen selection/abstention measurement |
| Demo | 59 | Reserved Emotet, Cobalt Strike, Mirai, FluBot, XWorm, PlugX |
| Manual questions | 20 | Separately written semantic questions on reserved demo records; researcher review pending |

Source rules: **90** selected files from [Malpedia signator-rules](https://github.com/malpedia/signator-rules/tree/eddaf26a30c6daff1b2350f52d0b5c11767f7552), commit `eddaf26a30c6daff1b2350f52d0b5c11767f7552`. The fixture also carries original rules where that snapshot has one for a demo family. No member-only rules, private samples, fabricated IoCs, or teacher-generated intelligence are used.

See [manifest](data/manifest.json), [source notices](data/SOURCE_NOTICES.md), [metadata acquisition](data/metadata-source.json), and [preparation reproducibility](evidence/data-reproducibility.json). Normalized complete supplied descriptions have **zero exact cross-split duplicates** in the current audit. Unknown aliases, semantic near-duplicates, and base-model pretraining contamination remain possible.

Frozen `.jsonl.gz` files are enough to replay training/evaluation. Regenerate them byte-for-byte from the locally retained `data/raw/`:

```bash
python3 -m slm.training.prepare
```

For a new acquisition, choose a **new** directory. The live API changes; this is a new experiment, not reproduction of the frozen snapshot:

```bash
python3 -m slm.training.fetch_sources --out slm/runs/next-snapshot/raw
```

The metadata portion is subject to Malpedia's **CC BY-NC-SA 3.0** website terms; the signator repository states **CC BY-SA 4.0**. Keep those separate from the code and Qwen licenses. This is a non-commercial research/demo data path. See the primary [Malpedia terms](https://malpedia.caad.fkie.fraunhofer.de/usage/tos); commercial model/data distribution needs a separately cleared source path.

## Run the offline panel now

From the Whiskers repository root:

```bash
python3 -m venv .venv
.venv/bin/pip install -r slm/requirements-runtime.lock
.venv/bin/python -m slm.demo.seed --out slm/demo/whiskers-demo.db
WHISKERS_OFFLINE=1 WHISKERS_DB=slm/demo/whiskers-demo.db WHISKERS_SLM_ENABLED=0 .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

Seed once; it refuses to overwrite an existing database. Search `emotet`, then open its family card. Metadata and original YARA work without Malpedia access or an external CSS CDN. The mini TI panel correctly says **local SLM not loaded** until its own checkpoint server is started. Offline mode disables corpus refresh and the activity scheduler. It uses a separate database; the ordinary `whiskers.db` is not migrated or retagged.

## Reproduce training on L20

Base: [Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B), original revision `c1899de289a04d12100db370d81485cdf75e47ca`, Apache-2.0. The original `model.safetensors` SHA-256 is pinned in [base-model.json](data/base-model.json). The run script verifies this weight digest before training.

The training requirements pin the libraries used by Whiskers. Create the training environment with:

```bash
python3 -m venv /opt/whiskers-train
/opt/whiskers-train/bin/pip install --extra-index-url https://download.pytorch.org/whl/cu128 -r slm/training/requirements-train.lock
/opt/whiskers-train/bin/python -m slm.training.fetch_base
```

The last command prints the pinned snapshot path and reuses the Hugging Face cache. Existing original base files can also be reused: the script checks the weight digest. Copy the Whiskers `slm/` directory to its own source directory on the server, for example:

```bash
rsync -az --exclude __pycache__ --exclude artifacts --exclude runs --exclude '*.db*' slm/ root@10.10.10.95:/data/whiskers-slm-src/slm/
```

On the server, from `/data/whiskers-slm-src`, with the existing converter checkout at the recorded revision:

```bash
WHISKERS_TRAIN_PYTHON=/opt/whiskers-train/bin/python bash slm/training/run_l20.sh /data/slm/base/Qwen3-0.6B /data/whiskers-slm/v0.1-night
```

Every work directory must be fresh. This verifies dataset and original-weight hashes, records GPU/driver/environment, trains a new LoRA adapter, merges it, preserves base tokenizer serialization, exports F16 GGUF, quantizes Q4_K_M and records the output hash. Default converter environment is `/opt/slm/venv/bin/python`; `WHISKERS_CONVERT_PYTHON` and `WHISKERS_LLAMA_CPP` can override independent installations. The script does not stop Ollama or any other service.

Pin for converter/runtime: [llama.cpp `6f1e654...`](https://github.com/ggml-org/llama.cpp/tree/6f1e6543704bc5b080c7a98e7b6f707b31f6ed51). Its documented schema API uses `response_format.schema`; model identity uses `--alias`. That checkout/toolchain must already exist for `run_l20.sh`; building a fresh CUDA/runtime toolchain is a separate prerequisite. Do not silently substitute `main`.

## Model serving and comparison

After a successful export, copy the Whiskers GGUF and its hash to `slm/artifacts/`, verify the hash, then start its own server:

```bash
/opt/slm/llama.cpp/build/bin/llama-server -m slm/artifacts/whiskers-ti-0.6b-Q4_K_M.gguf --alias whiskers-ti-0.6b-v0.1 --host 127.0.0.1 --port 8082 --ctx-size 4096 --jinja --chat-template-kwargs '{"enable_thinking":false}' -ngl 0
```

Enable mini TI when starting Whiskers:

```bash
WHISKERS_OFFLINE=1 WHISKERS_DB=slm/demo/whiskers-demo.db WHISKERS_SLM_ENABLED=1 WHISKERS_SLM_URL=http://127.0.0.1:8082 WHISKERS_SLM_MODEL=whiskers-ti-0.6b-v0.1 .venv/bin/uvicorn main:app --host 127.0.0.1 --port 8000
```

The alias check catches configuration mistakes; it is not cryptographic proof of server weights. Keep the actual GGUF hash/run manifest with the recorded demo. There is no automatic fallback to another model.

```bash
.venv/bin/python -m slm.training.evaluate --backend slm --model whiskers-ti-0.6b-v0.1 --split manual --out slm/evidence/tuned-manual.json
.venv/bin/python -m slm.training.evaluate --backend slm --model whiskers-ti-0.6b-v0.1 --split test --out slm/evidence/tuned-test.json
```

Serve an **original base GGUF** as `whiskers-base-0.6b` on a second port and run the same evaluator with `--url http://127.0.0.1:8083 --model whiskers-base-0.6b`. Both neural models get the same prompts, evidence, temperature, schema constraints and question files. Compare source-selection exact match/F1, known-answer accuracy, unknown-answer accuracy, invalid output rate and p50/p95 latency. No test-guided prompt tuning; use validation for changes. Once a test result influences development, report it as development evidence and create a new untouched test set.

Current non-neural baselines: **227/236** exact selections on template-derived test rows; **13/20** on separately written manual questions (7/14 answerable, 6/6 unknown). These are already measured in [lexical-test.json](evidence/lexical-test.json) and [lexical-manual.json](evidence/lexical-manual.json). The manual question set and this baseline were created in the same work session and need independent review. High template scores alone are insufficient evidence for a useful SLM. The tuned Q4 model measured **231/236** strict selections on the frozen template test (118/123 answerable, 113/113 unknown, 236/236 valid outputs), and **16/20** on the manual set (11/14 answerable, 5/6 unknown, 20/20 valid). The canonical original base measured 11/236 and 0/20 respectively under the same zero-shot contract; the lexical reference remains an important comparison. These figures describe this evidence-ID task, not general Qwen quality. The base and adapted weights use the same Transformers load/save path before GGUF conversion, keeping tied-weight serialization consistent. The set has now been inspected during integration development, so it is diagnostic evidence, not a blind final evaluation. Read the paired JSON for the larger template test; these scores are not malware-detection accuracy.

## Resources and schedule

Planning estimates, not Whiskers measurements:

| Work | Budget |
|---|---|
| Qwen 0.6B BF16 LoRA, rank 16 / alpha 32, 3 epochs, context 2048, microbatch 2 / accumulation 8 | one L20; reserve 12–16 GiB free VRAM |
| Initial ~1.9k-row training | roughly 15–60 minutes after the environment/base are ready; measure actual tokens/second |
| Training + export + paired GGUF evaluations | reserve 2–4 hours, including diagnosis and reruns |
| Host RAM | 16 GiB minimum planning target; 32 GiB comfortable for merge/export |
| Disk | 20–30 GiB free with a fresh CUDA Python environment, original base, merged/F16/Q4 files and build cache; about 5–10 GiB incremental if tools/base already exist |
| CPU demo inference | target 2–4 GiB spare RAM; measure latency on the actual presentation laptop |
| Finish model, review 20→100 manual questions, record video, package | 3–5 focused working days after GPU access |

`training-summary.json` records used/filtered rows, loss and peak PyTorch allocated memory. Allocated memory is not total process VRAM; retain `nvidia-smi` measurements too. CPU inference, 4-bit export and identical CUDA training seeds do not imply bit-identical weights across hardware/drivers.

## Mathematical basis and limits

For frozen base weights, LoRA learns `W' = W + (alpha/r) B A`, with `r=16`. Assistant-only supervised loss is `L = -sum(m_t log p_theta(y_t | x,y_<t)) / sum(m_t)`, where the mask is 1 only for assistant selection tokens. We optimize a narrow conditional evidence-selection task, not new malware knowledge pretraining. [LoRA, Hu et al.](https://arxiv.org/abs/2106.09685) supports the adaptation method; [Qwen3 Technical Report](https://arxiv.org/abs/2505.09388) describes the model family. Neither paper establishes this demo's TI quality.

For predicted ID set `P` and gold set `G`, evidence F1 is `2|P∩G|/(|P|+|G|)`; exact abstention is scored separately for unknown questions. Also measure accuracy on answerable cases so an always-abstain model cannot look successful. [ALCE, Gao et al.](https://arxiv.org/abs/2305.14627) motivates evaluating correctness and citation quality separately. Our extractive renderer ensures source fidelity after validation; it does not ensure that a passage actually answers the question or that the original source is true/current.

## Tests and further work

```bash
.venv/bin/pip install -r slm/requirements-test.txt
.venv/bin/python -m unittest discover -s tests -v
```

Real Android captures are included in the root [README](../README.md). Unit tests use contract fixtures; neural quality is measured separately with the actual GGUF.

1. **Evidence selection:** source selector, abstention, original YARA, offline replay and paired evaluation. Promote only if manual questions improve meaningfully over the base and lexical reference without a worse unknown-answer rate.
2. **Report context:** researcher-reviewed sentence/procedure questions; ATT&CK and report-level retrieval with provenance, dates and conflicting-source handling. Add explicit IOC fields from permitted sources; retain their literal values outside generated prose.
3. **Detection content:** candidate YARA from actual extracted sample features, compiler validation, positive/negative corpora and human review. Sigma needs the correct logsource/field mapping; Suricata needs network evidence and replay PCAPs. Evaluate each output independently.
4. **Broader release:** more held-out reports/families, smaller-vs-larger model comparison, CPU measurements, calibrated unknowns and a reproducible release bundle.

The application has an additional **source-only policy** for live infrastructure and local-incident questions. A family reference record cannot establish either. Policy refusals are explicitly labelled `source_policy`, do not call the model and are excluded from the raw neural scores above. One observed raw-model incident-attribution error motivated this integration guard; future overall application accuracy requires a new untouched evaluation set.

## Android measurements

On Motorola moto g54 5G, raw native inference produced 20/20 contract-valid outputs and 15/20 strict excerpt selections on the inspected manual set, with source policy disabled for that measurement. See [android-inference.json](evidence/android-inference.json). Median latency was 9.46 s; p95 was 17.81 s for this sequence of cold and cached prompts. These are physical-device results, distinct from GPU measurements.

The visible Emotet brief took 11.2 s; the FluBot DNS answer took 7.9 s. Both real chat captures appear in the root [README](../README.md#android-chat). PSS after the Emotet turn was 1,433,624 KiB (about 1.37 GiB). The model in the APK and the private on-device model both matched SHA-256 `d11c0e620cf9bda778aba445dc9b6617112c0481b758919e5353a29031891eff`. A JSON answer exported through Android storage was replayed successfully: the evidence digest, selected passages, displayed answer and model hash matched.
