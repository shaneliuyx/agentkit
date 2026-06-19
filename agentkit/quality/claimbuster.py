"""agentkit.quality.claimbuster — real labeled exemplars for the claim classifier.

Instead of hand-written claim / non-claim exemplars, sample them from the
**ClaimBuster** benchmark, whose human labels are exactly our split:

    Verdict =  1  → CFS  (check-worthy factual sentence)  → claim_examples
    Verdict = -1  → NFS  (non-factual: subjective / interrogative) → nonclaim_examples
    Verdict =  0  → UFS  (unimportant factual) → ignored

NFS is defined as "sentences that do not contain any factual assertions" — the
exact residual the `EmbeddingPrototypeClassifier` is meant to catch.

The CSV is downloaded at runtime to a cache (NOT vendored), respecting its
licence. The parse core is pure and unit-testable with a local fixture.

Dataset: Arslan, Hassan, Li, Tremayne — "A Benchmark Dataset of Check-worthy
Factual Claims", ICWSM 2020. Zenodo: https://doi.org/10.5281/zenodo.3609356
Licence: CC-BY-4.0 (attribution required; do not vendor — fetch at setup).
"""

from __future__ import annotations

import csv
import urllib.request
from pathlib import Path

from agentkit.quality.claim_classifier import EmbeddingPrototypeClassifier
from agentkit.types import Embedder

GROUNDTRUTH_URL = (
    "https://zenodo.org/api/records/3609356/files/groundtruth.csv/content"
)
DEFAULT_CACHE = Path.home() / ".cache" / "agentkit" / "claimbuster_groundtruth.csv"


def parse_exemplars(csv_path: str | Path, n: int = 20) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """PURE: read the ClaimBuster CSV → (claim_examples, nonclaim_examples).

    Maps ``Verdict`` 1 → claims, -1 → non-claims (0 ignored), takes the first
    ``n`` of each in file order (deterministic — no randomness, so the same CSV
    always yields the same exemplars). No network.
    """
    claims: list[str] = []
    nonclaims: list[str] = []
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            text = (row.get("Text") or "").strip()
            if not text:
                continue
            verdict = (row.get("Verdict") or "").strip()
            if verdict == "1" and len(claims) < n:
                claims.append(text)
            elif verdict == "-1" and len(nonclaims) < n:
                nonclaims.append(text)
            if len(claims) >= n and len(nonclaims) >= n:
                break
    return tuple(claims), tuple(nonclaims)


def _ensure_csv(cache_path: str | Path, url: str) -> Path:
    """Download the CSV to ``cache_path`` once; reuse it thereafter."""
    path = Path(cache_path)
    if path.exists() and path.stat().st_size > 0:
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "agentkit-claimbuster/0.1"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = resp.read()
    path.write_bytes(data)
    return path


def load_claimbuster_exemplars(
    n: int = 20,
    cache_path: str | Path = DEFAULT_CACHE,
    url: str = GROUNDTRUTH_URL,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Download (once, cached) + parse → (claim_examples, nonclaim_examples)."""
    return parse_exemplars(_ensure_csv(cache_path, url), n)


def claimbuster_classifier(
    embedder: Embedder,
    n: int = 20,
    margin: float = 0.05,
    cache_path: str | Path = DEFAULT_CACHE,
    url: str = GROUNDTRUTH_URL,
) -> EmbeddingPrototypeClassifier:
    """Build an ``EmbeddingPrototypeClassifier`` whose centroids come from real
    ClaimBuster CFS/NFS exemplars instead of the hand-written defaults."""
    claims, nonclaims = load_claimbuster_exemplars(n, cache_path, url)
    if not claims or not nonclaims:
        raise RuntimeError("ClaimBuster CSV yielded no exemplars for one class")
    return EmbeddingPrototypeClassifier(
        embedder, claim_examples=claims, nonclaim_examples=nonclaims, margin=margin
    )


if __name__ == "__main__":
    # Live check: download + parse, print counts and one example per class.
    claims, nonclaims = load_claimbuster_exemplars(n=10)
    print(f"claims={len(claims)} nonclaims={len(nonclaims)}")
    print(" CFS:", claims[0] if claims else "(none)")
    print(" NFS:", nonclaims[0] if nonclaims else "(none)")
    assert claims and nonclaims, "expected both classes from ClaimBuster"
    print("claimbuster loader OK")
