# Whiskers mini TI 0.6B · model card

**Model state: TRAINED / LOCAL PoC**, checked 2026-11-17. Independent adapter and GGUF exported; public distribution has not been performed.

| Field | Value |
|---|---|
| Runtime alias | `whiskers-ti-0.6b-v0.1` |
| Base | Qwen/Qwen3-0.6B, Apache-2.0 |
| Original revision | `c1899de289a04d12100db370d81485cdf75e47ca` |
| Base safetensors SHA-256 | `f47f71177f32bcd101b7573ec9171e6a57f4f4d31148d38e382306f42996874b` |
| Fine-tuning | BF16 LoRA, rank 16, alpha 32, dropout 0.05; q/k/v/o/gate/up/down projections |
| Objective | assistant-only cross-entropy on evidence-ID JSON / abstention |
| Completed run | 3 epochs; context 2048; microbatch 2; accumulation 8; AdamW lr 2e-4; seed 20261117 |
| Dataset | `data/manifest.json`; 1,906 train / 255 validation / 236 test rows |
| Reserved demo | 59 rows across six excluded family groups, plus 20 separately written questions |
| Export | merged F16 GGUF -> Q4_K_M |
| GGUF SHA-256 | `d11c0e620cf9bda778aba445dc9b6617112c0481b758919e5353a29031891eff` |
| Raw neural evaluation | 231/236 template-heldout and 16/20 inspected manual questions; paired results under `evidence/comparison-canonical/` |
| Physical Android evaluation | 15/20 strict selections, 20/20 contract-valid; median 9.46 s, p95 17.81 s on moto g54 5G; source policy disabled |

Intended use: local, inspectable TI context selection inside Whiskers. Input is an analyst question plus bounded Malpedia passages. Output is at most four excerpt IDs or abstention. The application validates the output and renders source text, rather than using generated prose as authoritative indicators. It exports the complete supplied evidence for replay.

Inputs currently expose identity, aliases, listed associations, record date and up to six initial description sentences of at most 1,000 characters each. YARA inputs expose literal metadata and a bounded condition, not dynamic scan results. Full records/rules remain available for analyst inspection. Truncation/omission can make an answer unavailable in the supplied model context even when the complete source contains it.

Source data is public Malpedia metadata, governed by its website's CC BY-NC-SA 3.0 terms, and selected YARA-Signator source content under CC BY-SA 4.0. Source authorship, URLs, original rule metadata and hashes are retained. Question labels are mostly template/keyword-derived, not expert-verified behavior annotations. Do not treat the base model's Apache license as permission to relicense the source dataset. Source-data conditions remain separate from the Apache-licensed base and the code.

Limitations: evidence relevance can be wrong despite syntactic validity; abstention may be missed; a quoted source may be stale or inaccurate. Listed actor associations do not identify an operator in a new incident. No sample execution, malware recall, goodware false-positive rate, autonomous blocking or live incident diagnosis is implemented by this selector.

Evaluation plan: original base vs tuned GGUF under identical schema/prompt/evidence, with lexical reference, family/alias isolation, exact source-selection/F1, answerable vs unknown results, invalid-output rate and real CPU latency. The measured lexical reference currently scores 227/236 on template tasks and 13/20 on manual questions. Those are **not** model scores. Independent question review and a larger untouched semantic evaluation are required before a strong quality claim.

Training and paired results are under `evidence/`; retain actual CPU replay evidence, failure cases and artifact/data notices with every distributed build. Use validation for changes; once a held-out result drives development it is no longer an untouched final test.
