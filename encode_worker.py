"""Tokenize texts with whatever transformers version is installed in the running interpreter.

Run in a separate process for each version, because two major versions of transformers cannot be
imported in the same interpreter.

    stdin  {"texts": [...], "model": "<repo id or local path>", "revision": "<commit sha or null>"}
    stdout {"transformers": "<version>", "class": "<tokenizer class>", "ids": [[...], ...]}
"""
import json
import os
import sys

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

from transformers import AutoTokenizer, __version__  # noqa: E402


def main():
    req = json.load(sys.stdin)
    tok = AutoTokenizer.from_pretrained(req["model"], revision=req.get("revision"))
    ids = [tok.encode(t, add_special_tokens=False) for t in req["texts"]]
    json.dump({"transformers": __version__, "class": type(tok).__name__, "ids": ids}, sys.stdout)


if __name__ == "__main__":
    main()
