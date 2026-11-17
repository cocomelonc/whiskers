# Whiskers dataset sources

The data in this directory belongs to the independent **whiskers mini TI** experiment.

- **Malpedia public family metadata**, Fraunhofer FKIE and Malpedia contributors: [website](https://malpedia.caad.fkie.fraunhofer.de/), [API](https://malpedia.caad.fkie.fraunhofer.de/usage/api), [terms](https://malpedia.caad.fkie.fraunhofer.de/usage/tos), **CC BY-NC-SA 3.0** as stated in the website terms checked 2026-11-17. This is a research/demo snapshot; do not describe it or a derived dataset as Apache/MIT-licensed. Description whitespace is normalized, evidence is split into passages, and question/selection labels are added. Citation URLs in each row point to the original family. The acquisition date is not the date of a malware observation.
- **Malpedia YARA-Signator rules**, Felix Bilstein and contributors: [repository](https://github.com/malpedia/signator-rules), **CC BY-SA 4.0**. The exact source commit and per-file hashes are in `rule-source.json` and `manifest.json`. Original rule metadata stays in the public demo. These code-based rules were automatically generated; their existence is not proof of complete family coverage.
- Questions and selection labels are derived by the Whiskers preparation script. Metadata and signatures are real source content; question wording and labels are template supervision, not a human-labelled TI corpus. `manual_cases.json` contains 20 separately written questions about the six reserved demo records. Independent researcher review is still needed.

The `.jsonl.gz` files are frozen replay inputs. Decompression produces the SHA-256 values in `manifest.json`. The raw full API response is retained locally under `raw/`, excluded from Git. Re-fetching the live API can produce a different snapshot; retain the frozen rows to repeat this experiment.

Base model: [Qwen/Qwen3-0.6B](https://huggingface.co/Qwen/Qwen3-0.6B), Apache-2.0. That is the base model's license, not a relicensing of Malpedia content. Record the source-data conditions with any adapter/model release; a commercial product needs a separately cleared data path.
