"""Same guard weights, several transformers versions: how much do the guard's decisions change?

Each transformers version tokenizes the texts in its own interpreter (encode_worker.py). A single
copy of the model, loaded once, then scores every set of ids, so the only thing that differs
between versions is the input the model receives.

    python drift.py --model protectai/deberta-v3-base-prompt-injection-v2 \
        --ref .venv-v4/bin/python --new .venv-v5/bin/python --out results/protectai.json
"""
import argparse
import json
import subprocess
from pathlib import Path

import perturb
from canaries import CANARIES

ROOT = Path(__file__).resolve().parent
CONDITIONS = ["baseline", *perturb.BATTERY, *perturb.CONTROLS]
THRESHOLD = 0.5


def encode(python, model, revision, texts):
    out = subprocess.run([python, str(ROOT / "encode_worker.py")], capture_output=True, text=True,
                         input=json.dumps({"texts": texts, "model": model, "revision": revision},
                                          ensure_ascii=False))
    if out.returncode:
        raise SystemExit(f"tokenization failed with {python}:\n{out.stderr[-2000:]}")
    return json.loads(out.stdout)


def scorer(model_id, revision):
    import torch
    from transformers import AutoModelForSequenceClassification, AutoTokenizer
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    model = AutoModelForSequenceClassification.from_pretrained(model_id, revision=revision).eval()
    tok = AutoTokenizer.from_pretrained(model_id, revision=revision)
    labels = {k.upper(): v for k, v in (model.config.label2id or {}).items()}
    # ProtectAI names its positive class INJECTION; Prompt Guard 2 has no names and uses index 1
    pos = labels.get("INJECTION", labels.get("MALICIOUS", 1))
    cls, sep = tok.cls_token_id, tok.sep_token_id

    def scores(id_lists, batch=32):
        out = []
        for k in range(0, len(id_lists), batch):
            chunk = [[cls] + list(ids)[:510] + [sep] for ids in id_lists[k:k + batch]]
            n = max(map(len, chunk))
            x = torch.zeros(len(chunk), n, dtype=torch.long)
            m = torch.zeros(len(chunk), n, dtype=torch.long)
            for i, ids in enumerate(chunk):
                x[i, :len(ids)] = torch.tensor(ids)
                m[i, :len(ids)] = 1
            with torch.no_grad():
                out.extend(torch.softmax(model(input_ids=x, attention_mask=m).logits, -1)[:, pos].tolist())
        return out
    return scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", required=True, help="Hugging Face repo id or local snapshot path")
    ap.add_argument("--revision", default=None, help="model commit sha; pin it, a repo can change")
    ap.add_argument("--ref", required=True, help="python of the reference environment (transformers 4.x)")
    ap.add_argument("--new", required=True, nargs="+", help="python of each environment to compare")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    rows = [{"id": cid, "lang": lang, "family": fam, "cond": c,
             "text": {**perturb.BATTERY, **perturb.CONTROLS}[c](text) if c != "baseline" else text}
            for cid, lang, fam, text in CANARIES for c in CONDITIONS]
    legit = [json.loads(line) for line in (ROOT / "data" / "legit.jsonl").read_text().splitlines()]
    texts = [r["text"] for r in rows] + [x["text"] for x in legit]
    n = len(rows)

    enc = {"ref": encode(args.ref, args.model, args.revision, texts)}
    for k, py in enumerate(args.new):
        enc[f"new{k}"] = encode(py, args.model, args.revision, texts)
    score = scorer(args.model, args.revision)
    sc = {name: score(e["ids"]) for name, e in enc.items()}
    ref = enc["ref"]

    res = {"model": args.model, "revision": args.revision, "threshold": THRESHOLD, "n_legit": len(legit), "n_canary_rows": n,
           "reference": {"transformers": ref["transformers"], "class": ref["class"]}, "compared": []}
    flag = lambda s: s > THRESHOLD  # noqa: E731
    for name in enc:
        if name == "ref":
            continue
        e, s, s0 = enc[name], sc[name], sc["ref"]
        legit_idx = range(n, n + len(legit))
        lost = [{"id": rows[i]["id"], "family": rows[i]["family"], "cond": rows[i]["cond"],
                 "score_ref": round(s0[i], 4), "score": round(s[i], 4)}
                for i in range(n) if flag(s0[i]) and not flag(s[i])]
        res["compared"].append({
            "transformers": e["transformers"], "class": e["class"],
            "legit_tokenized_differently": sum(e["ids"][j] != ref["ids"][j] for j in legit_idx) / len(legit),
            "legit_decisions_flipped": sum(flag(s[j]) != flag(s0[j]) for j in legit_idx),
            "legit_flipped_to_alert": sum(flag(s[j]) and not flag(s0[j]) for j in legit_idx),
            "fpr_ref": sum(flag(s0[j]) for j in legit_idx) / len(legit),
            "fpr": sum(flag(s[j]) for j in legit_idx) / len(legit),
            "canary_detections_lost": lost,
            "canary_detections_gained": sum(flag(s[i]) and not flag(s0[i]) for i in range(n)),
            "injection_detection_by_condition": {
                c: {"ref": sum(flag(s0[i]) for i in idx) / len(idx), "new": sum(flag(s[i]) for i in idx) / len(idx)}
                for c in CONDITIONS
                for idx in [[i for i, r in enumerate(rows) if r["cond"] == c and r["family"] == "injection"]]},
        })
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(res, indent=1, ensure_ascii=False))

    for c in res["compared"]:
        print(f"\n{args.model}: transformers {ref['transformers']} -> {c['transformers']}")
        print(f"  legit texts tokenized differently: {c['legit_tokenized_differently']:.1%}")
        print(f"  legit decisions flipped: {c['legit_decisions_flipped']} of {len(legit)} "
              f"({c['legit_flipped_to_alert']} to alert); FPR {c['fpr_ref']:.1%} -> {c['fpr']:.1%}")
        print(f"  canary detections lost: {len(c['canary_detections_lost'])}, gained: {c['canary_detections_gained']}")
        print("  injection canaries detected, by perturbation:")
        for cond, v in c["injection_detection_by_condition"].items():
            print(f"    {cond:13} {v['ref']:5.0%} -> {v['new']:5.0%}")


if __name__ == "__main__":
    main()
