"""studio.findings — RESEARCH_FINDING / PATCHES parsing, grounding, and the
section-aware STAR reducer.

Extracted from ``studio.runner`` (SRP): the finding-parsing, grounding-oracle,
patch-conversion, ranking, and reducer-closure logic is cohesive and stateless
(no runner instance state). ``_dbg`` is imported lazily from ``studio.runner``
(it stays in the runner) so this module never imports the runner at load time.
``studio.runner`` re-exports every public name here.
"""

from __future__ import annotations

import json as _json
import re as _re

from studio.prompts import _today_note


def _weakness_score(
    prior_weaknesses: list[str],
    open_weaknesses: list[str],
    embedder=None,
    threshold: float = 0.85,
) -> float:
    """Hill-climb score = solved / total over the weakness set (§11.4).

    A prior weakness is SOLVED only if NO still-open weakness is SEMANTICALLY
    similar to it. Matching must be semantic, not string: the LLM miner re-words
    the same issue every run ("no comparative metrics" -> "no systematic ranking"),
    so exact/normalized-string matching counted a re-worded-but-unsolved weakness as
    'solved' and inflated the score on an UNCHANGED artifact. total = prior + open
    issues with no prior match (genuinely new). No weakness anywhere => 1.0.

    Falls back to normalized-string matching when no embedder is available.
    """
    from studio.task_runs import _cosine, _norm_weakness
    prior = [w for w in (prior_weaknesses or []) if w and w.strip()]
    open_ = [w for w in (open_weaknesses or []) if w and w.strip()]
    if not prior and not open_:
        return 1.0

    if embedder is not None:
        try:
            pe = embedder.embed(prior) if prior else []
            oe = embedder.embed(open_) if open_ else []
            def _hit(vec, others) -> bool:
                return any(_cosine(vec, o) >= threshold for o in others)
            solved = sum(1 for pv in pe if not _hit(pv, oe))
            new_open = sum(1 for ov in oe if not _hit(ov, pe))
            total = len(prior) + new_open
            return round(solved / total, 2) if total else 1.0
        except Exception:  # noqa: BLE001 — embedding unavailable → string fallback
            pass

    pn = {_norm_weakness(w) for w in prior}
    on = {_norm_weakness(w) for w in open_}
    total_set = pn | on
    return 1.0 if not total_set else round(len(total_set - on) / len(total_set), 2)


#: Max cited URLs to prefetch per reduce phase (bounds added fetch latency/cost).
_PREFETCH_LIMIT = 8


def _prefetch_cited(drafts: list[str], limit: int = _PREFETCH_LIMIT) -> int:
    """Fetch cited-but-uncached URLs from the worker drafts so genuine sources pass the
    grounding guard (the fetch-density fix). No-op when the fetch cache is empty — that
    means no grounding drop happens (cache_active is False), so there is nothing to fix,
    and tests stay offline. Bounded by ``limit`` to cap latency. Returns how many cited
    URLs are now cached."""
    from studio.runner import _dbg
    from studio.tools import _fetch_cache, prefetch_url
    if not _fetch_cache:
        return 0
    seen: list[str] = []
    for d in drafts:
        for m in _re.finditer(r'URL:\s*(https?://\S+)', d):
            u = m.group(1).strip().rstrip('.,)')
            if u not in seen:
                seen.append(u)
    fetched = sum(1 for u in seen[:limit] if prefetch_url(u))
    _dbg(f"prefetch cited={len(seen)} fetched_ok={fetched} (cap {limit})")
    return fetched


def _apply_ranking(doc: str, findings: list) -> str:
    """F4/F5: replace the source-selection section with the honest split ranking table.

    Ranks EVERY source cited in the doc (this phase's rich findings supply title/popularity;
    others are added bare from the doc's URLs for completeness). Best-effort: no findings / no
    target section / a metric failure → doc unchanged. The S2/GitHub lookups go through
    ``fetch_metrics`` (ONE S2 batch, cached in .web_cache.json under metric:<url>, degrade to
    None) so they never block or crash a run."""
    if not findings:
        return doc
    import json as _json
    import os as _os
    from agentkit.artifacts.metrics import fetch_metrics
    from agentkit.artifacts.patcher import DocPatch, reduce_patches
    from agentkit.artifacts.ranking import synthesize_ranking_table
    from agentkit.artifacts.sections import split_sections
    from agentkit.artifacts.types import Finding

    target = next((h for h, _b in split_sections(doc)
                   if 'source selection' in h.lower()
                   or h.lower().strip().endswith('sources')
                   or 'popularity' in h.lower()), None)
    if target is None:
        return doc
    body = dict(split_sections(doc)).get(target, '')
    if not body:
        return doc
    # rank ALL sources cited in the doc, enriched by this phase's findings
    rich = {f.url: f for f in findings}
    all_findings = [rich.get(u) or Finding(url=u)
                    for u in {x.rstrip('.,)') for x in _re.findall(r'https?://\S+', doc)}]
    if not all_findings:
        return doc
    from agentkit.artifacts.metrics import source_kind
    # Only touch the network/cache file when a source actually HAS a fetchable metric
    # (arxiv/github). A blog-only doc (e.g. offline tests) skips file I/O entirely → all
    # sources are 'reported', no network, no .web_cache.json read/write.
    if not any(source_kind(f.url)[0] for f in all_findings):
        metrics = {f.url: None for f in all_findings}
    else:
        cache: dict = {}
        try:
            if _os.path.exists('.web_cache.json'):
                with open('.web_cache.json') as _cf:
                    cache = _json.load(_cf)
        except Exception:  # noqa: BLE001
            cache = {}
        try:
            metrics = fetch_metrics([f.url for f in all_findings],
                                    s2_key=_os.environ.get('SEMANTIC_SCHOLAR_API_KEY'), cache=cache)
            with open('.web_cache.json', 'w') as _cf:   # persist metric:<url> entries
                _json.dump(cache, _cf)
        except Exception:  # noqa: BLE001 — metrics best-effort; never block
            metrics = {}
    table = synthesize_ranking_table(all_findings, metrics)
    return reduce_patches(
        doc, [[DocPatch(op='replace', anchor=body, content=f"{target}\n\n{table}\n",
                        source='ranking')]]
    ).text


def _make_section_reducer(client, artifact_text: str, weaknesses: list[str], embedder=None):
    """Build the section-aware STAR reducer closure (DESIGN §4.5; Lever 3).

    Returns ``run_plan``'s reducer hook ``(worker_drafts) -> (merged_text, tokens)``.

    PATCH-BASED (Lever 3): instead of re-emitting the full ~38K document — whose
    output a completion cap (``max_tokens``) truncates mid-section (the v29
    truncation/incomplete weaknesses) — the reducer asks the model for a SMALL list
    of section PATCHES and applies them MECHANICALLY via ``reduce_patches``. The
    model never re-emits the document, so truncation is impossible and output tokens
    drop ~10x. As a deterministic floor, the workers' own RESEARCH_FINDING blocks are
    converted to additive patches too — so a phase always makes grounded progress even
    if the model emits no usable PATCHES. Additive only; the runner's grow-only
    writeback ratchet still rejects any shrink. Sections are the artifact's ``##``
    headings — the structure lives in the markdown + weakness tags, no Section type.
    """
    from studio.runner import _dbg
    wk_block = "\n".join(f"- {w}" for w in (weaknesses or [])) or "(none)"
    art_block = artifact_text.strip()

    def reduce(drafts: list[str]) -> tuple[str, int]:
        workers = "\n\n".join(f"[worker {i + 1}]\n{d}" for i, d in enumerate(drafts))
        prompt = (
            _today_note() +
            "You are the section-aware reducer of a multi-worker research phase.\n"
            "Do NOT re-emit the document. Emit a SMALL JSON list of PATCHES that fold "
            "each worker's SOURCED finding into the CURRENT ARTIFACT, section by "
            "section (sections are the '##' headings).\n\n"
            "Each patch is one object:\n"
            '  {"op": "insert_after", "anchor": "## <exact section heading from the '
            'artifact>", "content": "<a substantiating SENTENCE woven from the '
            'finding: its central claim + a short verbatim quote + the source URL>"}\n'
            '  - op is "insert_after" (add prose under a heading) or "replace" (swap a '
            "placeholder line for grounded prose).\n"
            "  - anchor MUST be text that already exists in the CURRENT ARTIFACT.\n"
            "  - content ADDS grounded prose and keeps every source URL.\n\n"
            "PATCH CONTENT CONTRACT:\n"
            "  - content is one short paragraph or sentence, not a markdown section.\n"
            "  - content MUST include at least one http(s) source URL copied from a worker.\n"
            "  - content MUST NOT contain '#', '##', a report title, a full document, "
            "a template placeholder, or an empty-section marker.\n"
            "  - If a worker did not provide a real URL for a claim, emit no patch for "
            "that claim.\n"
            "  - If you cannot satisfy this contract, output an empty JSON list: [].\n\n"
            "RULES — violating these REGRESSES the deliverable:\n"
            "  - Additive only: a patch may ADD substance, never delete or shorten "
            "existing sourced content.\n"
            "  - Every added claim keeps its source URL from the worker's "
            "RESEARCH_FINDING.\n"
            "  - A weakness below resolved by a worker (with a real URL) → weave it in "
            "as a sentence, not a bare citation line.\n"
            "  - No worker content for a section → emit no patch for it.\n\n"
            f"SECTION WEAKNESSES (review checklist):\n{wk_block}\n\n"
            f"CURRENT ARTIFACT:\n--- BEGIN ---\n{art_block or '(empty)'}\n--- END ---\n\n"
            f"WORKER OUTPUTS:\n{workers}\n\n"
            "Output ONLY:\nPATCHES:\n```json\n[ ... ]\n```\n"
            "Nothing else — no document, no preamble, no commentary."
        )
        res = client.chat([{"role": "user", "content": prompt}])
        tokens = int(getattr(res, "total_tokens", 0) or 0)
        llm_patches = _sanitize_llm_patches(
            art_block, _parse_patches_from_output(res.text or "")
        )
        # Deterministic floor: convert the workers' own RESEARCH_FINDING blocks to
        # additive patches. Guarantees grounded progress when the model emits no
        # usable PATCHES, and folds in any finding it skipped. The reduce_patches
        # duplicate-guard makes the overlap idempotent.
        # Fetch-density fix: spokes cite ~12 URLs/phase but fetch ~1, so the grounding
        # guard dropped 80-100% of real findings. Fetch the cited-but-uncached URLs now
        # so genuine sources survive grounding (a 404/fabricated URL still drops).
        _prefetch_cited(drafts)
        findings: list = []
        raw_findings = 0
        for d in drafts:
            raw_findings += len(_re.findall(r'#{0,6}\s*RESEARCH_FINDING', d))
            findings += _parse_findings(d)
        # F1: collapse near-duplicate findings (STRUM merge) BEFORE they become patches, so
        # the additive merge stops dumping ~26 repetitive citations as an unordered block.
        from agentkit.artifacts.dedup import consolidate_findings, dedupe_findings
        from studio.task_runs import _normalize_url
        findings, n_dedup = dedupe_findings(findings, embedder)
        # F6 (reducer-side dedup): against-doc — drop a finding whose URL is ALREADY cited
        # in the artifact (re-citing the same source is the bulk of the quote-wall).
        _cited = {_normalize_url(u) for u in _re.findall(r'https?://\S+', art_block)}
        _n_doc = len(findings)
        findings = [f for f in findings if _normalize_url(f.url) not in _cited]
        n_doc_dup = _n_doc - len(findings)
        # F6: same-URL merge + scaffolding strip + per-section density cap. Thins the wall
        # at its source so _findings_to_patches emits ~1 woven sentence per real source.
        findings, _cstats = consolidate_findings(findings, norm_url=_normalize_url)
        floor_patches = _findings_to_patches(findings)
        patches = llm_patches + floor_patches
        if not patches:
            _dbg(f"reduce drafts={len(drafts)} raw_findings={raw_findings} "
                 f"llm={len(llm_patches)} floor=0 dedup={n_dedup} doc_dup={n_doc_dup} "
                 f"url_merged={_cstats['url_merged']} capped={_cstats['capped']} → NO PATCHES")
            return art_block, tokens  # nothing to add → unchanged (no truncation)
        # Resolve anchors before merging: a finding's PATCH_TARGET that is not a real
        # heading in the doc would otherwise become a '<!-- conflict -->' marker that
        # pollutes the artifact (the throughput fix surfaced 13 such markers in one
        # phase). Demote deterministic finding patches with a missing anchor to a clean
        # append. LLM patches were already validated above; if their anchor was missing,
        # they were dropped instead of being allowed to invent document structure.
        for p in patches:
            if getattr(p, "op", "") == "insert_after" and p.anchor and p.anchor not in art_block:
                if str(p.anchor).lstrip().startswith("##"):
                    p.content = f"\n\n{str(p.anchor).strip()}{p.content}"
                p.op, p.anchor = "append", None
        from agentkit.artifacts.patcher import reduce_patches
        rr = reduce_patches(art_block, [patches])
        # F4/F5: replace the source-selection section with the honest split ranking table.
        merged = _apply_ranking(rr.text, findings)
        _dbg(f"reduce drafts={len(drafts)} raw_findings={raw_findings} "
             f"llm={len(llm_patches)} floor={len(floor_patches)} dedup={n_dedup} "
             f"doc_dup={n_doc_dup} url_merged={_cstats['url_merged']} capped={_cstats['capped']} "
             f"applied_delta={len(rr.text) - len(art_block)} conflicts={len(rr.conflicts)} "
             f"ranked_delta={len(merged) - len(rr.text)}")
        return merged.strip(), tokens

    return reduce


def _sanitize_llm_patches(artifact_text: str, patches: list) -> list:
    """Keep only scoped, sourced reducer patches before structural merge.

    The active failure mode is a weak reducer returning a whole document or skeleton
    fragment inside a JSON patch. Waiting for writeback to reject it is too late:
    the merge step may already stack duplicate sections. This validator enforces the
    reducer contract mechanically and keeps the generic behavior topic-neutral.
    """
    from agentkit.artifacts.sections import split_sections
    from studio.task_runs import _normalize_url

    headings = set(_re.findall(r"(?m)^#{1,6}\s+.+$", artifact_text or ""))
    section_urls = {
        heading: {
            _normalize_url(u.rstrip(".,);]"))
            for u in _re.findall(r"https?://\S+", body)
        }
        for heading, body in split_sections(artifact_text or "")
    }
    seen_by_anchor: dict[str, set[str]] = {}
    out: list = []
    for p in patches or []:
        op = getattr(p, "op", "")
        anchor = getattr(p, "anchor", None)
        content = getattr(p, "content", "") or ""
        if op not in {"insert_after", "replace"}:
            continue
        if not anchor or anchor not in (artifact_text or ""):
            continue
        if op == "insert_after" and headings and anchor not in headings:
            continue
        if not content.strip() or len(content) > 2500:
            continue
        urls = {
            _normalize_url(u.rstrip(".,);]"))
            for u in _re.findall(r"https?://\S+", content)
        }
        if not urls:
            continue
        target_seen = set(section_urls.get(anchor, set())) | seen_by_anchor.setdefault(anchor, set())
        if urls & target_seen:
            continue
        if _re.search(r"(?m)^#{1,6}\s+", content):
            continue
        if _re.search(r"(?i)_\((?:pending|to be completed)\s*[-—][^)]*\)_", content):
            continue
        p.content = "\n\n" + content.strip() + "\n"
        out.append(p)
        seen_by_anchor[anchor].update(urls)
    return out


def _parse_findings(text: str) -> list:
    """Parse RESEARCH_FINDING blocks → grounded ``agentkit.artifacts.types.Finding`` objects.

    Bare ``RESEARCH_FINDING:`` and ``## RESEARCH_FINDING`` both parse. Dual grounding oracle
    (Lever 1): keep a finding iff its URL is http(s) AND — when the fetch cache holds pages —
    its URL was fetched OR its verbatim quote appears on a fetched page; else drop (a true
    fabrication: invented URL AND invented quote). ``quote_verified`` gates whether the
    verbatim quote is later woven. CLAIM/CONTENT/KEY_INSIGHT fold into ``why`` (the schema
    dropped the rephrased CLAIM — the verbatim QUOTE is the evidence)."""
    from agentkit.artifacts.types import Finding
    from studio.tools import _fetch_cache, _quote_in_cache, _url_in_cache

    cache_active = bool(_fetch_cache)
    out: list = []
    for m in _re.finditer(
        r'#{0,6}\s*RESEARCH_FINDING(.*?)(?=#{0,6}\s*RESEARCH_FINDING|\Z)', text, _re.DOTALL
    ):
        block = m.group(1)

        def _f(name: str) -> str:
            fm = _re.search(rf'{name}:\s*(.+)', block)
            return fm.group(1).strip() if fm else ''

        url = _f('URL')
        if not url.lower().startswith('http'):
            continue  # not a sourced finding → not content
        quote = _f('QUOTE').strip().strip('"')
        quote_verified = bool(quote) and _quote_in_cache(quote)
        grounded = (not cache_active) or _url_in_cache(url) or quote_verified
        if cache_active and not grounded:
            continue
        out.append(Finding(
            url=url,
            title=_f('ARTICLE_TITLE'),
            quote=quote,
            why=_f('WHY') or _f('CLAIM') or _f('CONTENT') or _f('KEY_INSIGHT'),
            popularity=_f('POPULARITY'),
            patch_target=_f('PATCH_TARGET'),
            quote_verified=quote_verified,
            grounded=grounded,
        ))

    # JSON-wrapped findings: oMLX local models (qwen2.5-coder) emit the finding as a
    # JSON object — fenced ```json {"RESEARCH_FINDING": {...}}``` or bare — which the
    # plain ARTICLE_TITLE:/URL: parse above misses (``"URL":`` doesn't match ``URL:`` so
    # the url comes out empty and the finding is dropped → 0 findings, fetched pages
    # never reach the doc). Parse those too, through the SAME grounding oracle. Haiku
    # uses the plain format + native tool_calls, so its text has no fences → no-op here.
    import json as _json
    _blocks = _re.findall(r"```[a-zA-Z_]*\s*\n?(.*?)```", text, _re.DOTALL)
    _stripped = text.strip()
    if _stripped.startswith("{") and _stripped.endswith("}"):
        _blocks.append(_stripped)
    for _blk in _blocks:
        try:
            _obj = _json.loads(_blk.strip())
        except Exception:  # noqa: BLE001 — a non-JSON fence is not a finding
            continue
        for _rec in (_obj if isinstance(_obj, list) else [_obj]):
            _r = _rec.get("RESEARCH_FINDING", _rec) if isinstance(_rec, dict) else None
            if not isinstance(_r, dict):
                continue

            def _jf(*keys: str, _r: dict = _r) -> str:
                for k in keys:
                    v = _r.get(k)
                    if v not in (None, ""):
                        return str(v)
                return ""

            j_url = _jf("URL", "url")
            if not j_url.lower().startswith("http"):
                continue
            j_quote = _jf("QUOTE", "quote").strip().strip('"')
            j_qv = bool(j_quote) and _quote_in_cache(j_quote)
            j_grounded = (not cache_active) or _url_in_cache(j_url) or j_qv
            if cache_active and not j_grounded:
                continue
            out.append(Finding(
                url=j_url,
                title=_jf("ARTICLE_TITLE", "title"),
                quote=j_quote,
                why=_jf("WHY", "why", "CLAIM", "CONTENT", "KEY_INSIGHT"),
                popularity=_jf("POPULARITY", "popularity"),
                patch_target=_jf("PATCH_TARGET", "patch_target"),
                quote_verified=j_qv,
                grounded=j_grounded,
            ))
    return out


def _findings_to_patches(findings: list) -> list:
    """Grounded ``Finding`` objects → additive, WOVEN DocPatches (Lever 2: verbatim copy-paste
    evidence). The verbatim QUOTE is the evidence when verified; ``why`` frames it. Each becomes
    an ``insert_after`` its PATCH_TARGET (or ``append`` if none). Additive only."""
    from agentkit.artifacts.patcher import DocPatch

    patches: list = []
    for f in findings:
        cite = f"[{f.title or f.url}]({f.url})"
        has_pop = bool(f.popularity and f.popularity.lower() != 'n/a')
        pop_clause = f", {f.popularity}" if has_pop else ""
        lead = f"{f.why.rstrip('.')}: " if f.why else ""
        if f.quote_verified:  # COPY-PASTE: the verbatim source excerpt IS the evidence
            content = f'\n\n{lead}"{f.quote}" ({cite}{pop_clause}).\n'
        elif f.why:           # no verifiable quote → grounded by URL; framing + citation
            content = f"\n\n{f.why.rstrip('.')} ({cite}{pop_clause}).\n"
        else:                 # nothing to weave → bare citation line
            content = f"\n- {cite}{(' (' + f.popularity + ')') if has_pop else ''}\n"
        if f.patch_target:
            patches.append(DocPatch(op="insert_after", anchor=f.patch_target, content=content, source="finding"))
        else:
            patches.append(DocPatch(op="append", anchor=None, content=content, source="finding"))
    return patches


def _research_findings_to_patches(text: str) -> list:
    """Back-compat one-shot: parse + ground + weave (the post-loop patch path). The reducer
    uses _parse_findings + dedupe_findings + _findings_to_patches separately so it can collapse
    near-duplicate findings before they become patches (F1)."""
    return _findings_to_patches(_parse_findings(text))


def _parse_patches_from_output(text: str) -> list:
    """Extract DocPatch list from a worker's PATCHES JSON block (DESIGN §2.2).

    Returns empty list when no block is present — the caller falls through to
    the RESEARCH_FINDING reducer path unchanged.
    """
    from agentkit.artifacts.patcher import DocPatch

    m = _re.search(r'PATCHES:\s*```json\s*(\[.*?\])\s*```', text, _re.DOTALL)
    if not m:
        m = _re.search(r'"patches"\s*:\s*(\[.*?\])', text, _re.DOTALL)
    if not m:
        return []
    try:
        items = _json.loads(m.group(1))
        return [
            DocPatch(
                op=item.get("op", "append"),
                anchor=item.get("anchor"),
                content=item.get("content", ""),
                source=item.get("source", ""),
            )
            for item in items
            if isinstance(item, dict)
        ]
    except (ValueError, TypeError):
        return []
