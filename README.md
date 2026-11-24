# guard-tokenizer-drift

**Same guard weights, different transformers version, different decisions.**

This repository measures how much the decisions of an open prompt-injection guard change when only the
Hugging Face `transformers` library is upgraded. The model files are byte-identical; only the code that
turns text into token ids changes.

## Results (24 November 2026)

Threshold 0.5. Reference: transformers 4.57.6. Compared: 5.15.0 and 5.24.0 (latest release); both give
identical results.

### protectai/deberta-v3-base-prompt-injection-v2

| | 4.57.6 | 5.x |
|---|---|---|
| Legitimate texts tokenized differently | | 29.4% |
| False-positive rate on 680 legitimate texts | 9.9% | 13.1% (22 texts flip to alert) |
| Injection canaries in fullwidth characters detected | 100% | 86% |
| Injection canaries with zero-width spaces detected | 86% | 100% |
| Canary detections lost / gained (all 240 canary rows) | | 3 / 18 |

The change is not a uniform degradation: the guard raises more alerts overall, on legitimate text and
on canaries alike, and misses fullwidth text it used to catch.

### Llama Prompt Guard 2 (86M), public mirror `project-free-llama/Llama-Prompt-Guard-2-86M`

| Injection canaries detected | 4.57.6 | 5.x |
|---|---|---|
| plain text | 79% | 71% |
| zero-width spaces inside words | 79% | **0%** |
| fullwidth characters | 79% | **0%** |
| letters separated by spaces | 79% | **0%** |
| one-letter prefix (TokenBreak) | 79% | 79% |
| Cyrillic homoglyphs, Unicode tags | 0% | 0% |

96.6% of the legitimate texts are tokenized differently; the false-positive rate stays at 0%.
Overall, 38 canary detections are lost and 6 are gained. Full per-canary results are in `results/`.

Why the three obfuscations fail together: the normalizer stored in this model's `tokenizer.json`
applies Unicode folding (NFKC), removes invisible characters and deletes every space. Under 4.57.6
the zero-width, fullwidth and spaced variants therefore get exactly the same token ids as the plain
text, so the guard was immune to them by construction. Under 5.x that chain is skipped: fullwidth
letters become `[UNK]`, zero-width spaces split words around `[UNK]`, and spaced letters become one
token per letter.

The official `meta-llama/Llama-Prompt-Guard-2-86M` checkpoint is gated and was not tested; the mirror
is assumed to carry the same files.

## Cause and upstream status

Transformers 5 rebuilds these tokenizers itself and drops the normalization rules stored in the model
files:

- [huggingface/transformers#49622](https://github.com/huggingface/transformers/issues/49622):
  `DebertaV2Tokenizer` drops the SentencePiece NFKC normalization; fullwidth letters and ligatures
  become `[UNK]`. Names the ProtectAI guard.
- [huggingface/transformers#50674](https://github.com/huggingface/transformers/issues/50674): the
  normalizer stored in `tokenizer.json` is dropped for several models, including the Prompt Guard 2
  mirror ("ASCII space deletion is lost").
- Proposed fixes, **not merged as of 24 November 2026**:
  [#50908](https://github.com/huggingface/transformers/pull/50908) and
  [#49579](https://github.com/huggingface/transformers/pull/49579).
- **No released version contains the fix yet; 5.24.0 is still affected.**

## Workarounds

1. Pin `transformers` (and `tokenizers`) together with the guard, as part of its version.
2. Load the model's `tokenizer.json` directly with the `tokenizers` library, which applies the stored
   normalizer: under transformers 5.24.0 with tokenizers 0.23.3, the perturbed texts get the same ids
   as under 4.57.6 again (checked on both guards).
3. Keep a regression test of reference token ids and guard decisions on a fixed corpus, run at every
   rebuild. A hash of the model files cannot detect this: the files do not change.

## Reproduce

```bash
python3 -m venv .venv-v4 && .venv-v4/bin/pip install -r requirements-v4.txt
python3 -m venv .venv-v5 && .venv-v5/bin/pip install -r requirements-v5.txt
.venv-v5/bin/python drift.py --model protectai/deberta-v3-base-prompt-injection-v2 \
    --revision 90c9989b1a342275dd0d1a95aad283c04e075671 \
    --ref .venv-v4/bin/python --new .venv-v5/bin/python --out results/protectai.json
.venv-v5/bin/python drift.py --model project-free-llama/Llama-Prompt-Guard-2-86M \
    --revision 43882965632dcb7b20299530f6436ac759d07fd9 \
    --ref .venv-v4/bin/python --new .venv-v5/bin/python --out results/promptguard2.json
```

Each version tokenizes in its own interpreter (`encode_worker.py`); one copy of the model then scores
every set of ids, so the input is the only thing that differs. Runs on CPU in a few minutes.

## Method and limits

- **Canaries** (`canaries.py`): 30 benign instructions (15 French, 15 English) whose only payload is the
  word PINEAPPLE, 14 of them (7 per language) written as prompt injections. Each is scored as written
  and under 6 perturbations from published attack classes plus 1 random-typo control (`perturb.py`),
  240 rows in all. Detection rates by perturbation are over the 14 injection canaries, so one canary
  is about 7 points. No search or optimization against the guards.
- **Reference**: 4.57.6 applies the normalizer the model was published with; it is taken as the
  intended behavior.
- **Legitimate corpus** (`data/legit.jsonl`, 680 texts): Wikipedia excerpts in 10 languages (dump
  20231101), Python standard library code, and synthetic text with emoji, URLs and copy-pasted
  typography.
- The canary set is small: lost detections show that a bypass exists, not how often it occurs in real
  traffic.
- Scores are computed with one fixed model copy under PyTorch; only tokenization changes. Inputs
  are cut to 512 tokens for scoring; "tokenized differently" compares the full id sequences.
- Model revisions are pinned (see the commands above and the `revision` field of each result).

## Licenses

Code: MIT (see `LICENSE`). Data: Wikipedia excerpts under CC BY-SA 4.0; Python standard library
excerpts under the PSF License; synthetic texts under MIT.
