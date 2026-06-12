# Research: AI-Personalized Early Reading (PreK–Grade 3) with a Knowledge Graph

_Last updated: 2026-06-11_

## Executive Summary

A POC that *truly* personalizes early reading is buildable today with mature components at every layer — but the hard parts are not the graph. They are (1) **child speech recognition**, which degrades 2–5× versus adult ASR and tops out around F1 0.52 for phoneme-level miscue classification, and (2) **COPPA/FERPA compliance**, where the June 2025 COPPA amendments now treat children's audio as personal information and bar using it for AI training without separate parental consent. The knowledge graph itself is the *easy, high-leverage* layer: Neo4j is the right choice, and a hand-authored phonics prerequisite DAG + Bayesian Knowledge Tracing (BKT) per skill + a ZPD "next-best-word" selector is the validated pattern from the intelligent-tutoring literature. Recommendation: build the graph + mastery + recommender loop now (no child-data risk), defer live ASR to a later phase behind Azure Pronunciation Assessment with a self-hosted Whisper fallback.

## Problem Statement

Listen to a PreK–Grade 3 child read aloud → detect which grapheme–phoneme correspondences (GPCs) they have/haven't mastered → map that mastery onto a prerequisite skill graph rooted in structured-literacy science → recommend the next decodable word/text at the child's zone of proximal development (i+1), and schedule review for consolidation. Four sub-problems: child ASR + phoneme miscue analysis; mastery modeling over a skill DAG; ZPD next-item selection; spaced repetition. The existing assets — a 114k-word dataset already decomposed into graphemes/blends/digraphs/syllables/ARPABET phonemes/audio/leveling, plus an assessment platform — cover the *content graph*; the missing pieces are the *learner model* and the *recommender*.

## Technology Evaluation

### Layer 1 — Graph database (RECOMMENDED: Neo4j)

| Option | Status | Verdict |
|---|---|---|
| **Neo4j** (driver 6.2, GDS 2026.05, `neo4j-graphrag` 1.17) | Active, 14-yr ecosystem, native vector index (HNSW) | **Use** — already prototyped; GDS gives PageRank/Louvain/Node2Vec for skill clustering |
| Memgraph | BSL 1.1, sub-10ms latency | Consider only if live dashboards need it |
| Kùzu | **Archived Oct 2025 (Apple acquisition)** | Hard avoid |
| ArangoDB | AQL not Cypher, BSL | Avoid for this use case |

### Layer 2 — Mastery / knowledge tracing (RECOMMENDED: pyBKT now, DKT later)

| Option | Maturity | Verdict |
|---|---|---|
| **pyBKT 1.4.2** | sklearn-style, ~50 obs/skill, multi-skill items | **Use for POC** — maps 1:1 onto per-node skill mastery; explainable for LLM rationale |
| pyKT/DKT 0.0.38 | PyTorch, higher AUC at >10k sequences | Scale-up only; black-box, needs GPU |
| PFA | No maintained package | Avoid |

BKT assumes skill independence — false for phonics. Mitigate by encoding prerequisites as directed Neo4j edges and gating BKT updates with a topological traversal.

### Layer 3 — Child speech assessment (RECOMMENDED: Azure PA managed; Whisper self-host path)

| Option | Phoneme-level | Child WER (zero-shot) | Verdict |
|---|---|---|---|
| **Azure Pronunciation Assessment** | Yes (accuracy/fluency/prosody) | ~30% | **POC managed choice** — $1.32/hr; has FERPA DPA; set lenient child thresholds |
| **Whisper large-v3 + WhisperX/wav2vec2** | Via forced alignment | ~21% → ~8% fine-tuned | **OSS/production path** — audio never leaves VPC (COPPA-safe); needs GPU |
| SoapBox Labs | Yes (best-in-class) | ~5–15% | **Dead end** — acquired by Curriculum Associates 2023, no new external contracts |
| Google STT / Amazon Transcribe | No phoneme scoring | 49% / unpublished | Avoid |

Miscue *classification* (substitution/omission/insertion/self-correction per literacy frameworks) is **not provided by any API** — it must be built on top of phoneme scores. Amira's patented CFG + "garbage model" is the production-validated passage-level pattern; for word-level, force-align against the expected ARPABET sequence (which you already have).

### Layer 4 — LLM recommender/content (RECOMMENDED: Claude Haiku 4.5 + prompt caching; Sonnet 4.6 for reasoning)

| Model | In/Out $/MTok | Role |
|---|---|---|
| **Claude Haiku 4.5** | $1 / $5 (cache read $0.10) | **Per-session recommendation** — ~$0.48/classroom/session, ~$72/classroom/yr |
| Claude Sonnet 4.6 | $3 / $15 | Complex reasoning / planning |
| Claude Opus 4.8 | $5 / $25 | Reserve for content generation |

Cache the static curriculum graph + phonics scope/sequence (~8–10k tokens); only learner state varies per call (90% cost reduction). Use `tool_use` for guaranteed structured output. Note: the **graph traversal answers "next unmastered skill whose prerequisites are met" with zero LLM calls** — reserve the LLM for narration, content selection/generation, and teacher-facing rationale.

## Architecture Patterns Found

1. **Layered skill DAG** — Phoneme (44) → GPC (e.g. /sh/→"sh") → word-pattern (CVC/CVCe/CCVC) → decodable text. Edges are directed prerequisites. UK *Letters and Sounds* (Phases 1–6) and Orton-Gillingham agree on top-level ordering; ~100–150 nodes hand-authorable in a week.
2. **BKT per node + ZPDES pool** — mastery threshold 0.8–0.95; the "active pool" = nodes whose prerequisites are all mastered; multi-armed bandit (Thompson sampling) selects within the pool. Most-validated ITS pattern (OATutor, ZPDES-KS arXiv 2402.01672).
3. **Decodability as an item feature** — `decodability(word, learner) = |mastered GPCs| / |total GPCs|`. **Next-best word = maximize mastered-unit reuse while introducing ≤1 new GPC.** This operationalizes i+1 at the word level and is exactly the query the existing word-graph supports.
4. **Three-agent loop (GraphMASAL, arXiv 2511.11035)** — Diagnostician (query mastery) → Planner (multi-source/multi-sink path over the DAG) → Tutor (retrieve/generate content), orchestrated in LangGraph with teacher human-in-the-loop.
5. **Forgetting/decay + spaced repetition** — exponential mastery decay (~0.08/day) before BKT is fitted; **FSRS (`py-fsrs`)** for word review scheduling, `desired_retention≈0.85` for early readers.

## Key APIs and Services

| Service | Purpose | Pricing | Compliance |
|---|---|---|---|
| Neo4j AuraDB Pro | Learner + content graph | ~$65/mo smallest; Free tier ~200k nodes (POC) | DPA on request; self-host for sovereignty |
| Azure AI Speech PA | Phoneme/word scoring | $1.32/hr (+$0.30/hr/feature real-time) | FERPA DPA via Online Services Terms; no training on customer data |
| Anthropic Claude API | Recommendation/content | Haiku $1/$5 per MTok | DPA available; API data not trained on by default — **verify & sign DPA first** |
| cmudict + `g2p-en` | G2P fallback for OOV words | Free | n/a |
| `py-fsrs`, `pyBKT` | Scheduling, mastery | Free (MIT) | local |

## Known Pitfalls and Risks

- **Child ASR false positives** (HIGH): generic models flag normal child disfluency as errors. Mitigate with confidence thresholds + "try again" fallback + 2-read confirmation before scoring a miscue.
- **COPPA June 2025 amendments** (HIGH/legal): children's **audio = PII**; the voice-command exception requires immediate deletion, so longitudinal storage needs verifiable parental consent or the school-consent DPA pathway. **Separate** consent is required before any vendor may train on child data — confirm Azure/Anthropic DPAs prohibit training. Delete raw audio post-transcription; store only phoneme scores.
- **FERPA/SOPIPA**: every sub-processor (Azure, Anthropic, Neo4j) needs a signed DPA before student data flows; LLM/ASR calls are "disclosures." SOPIPA categorically bars targeted ads/profiling/sale of student data.
- **Phonics DAG disagreement** (~20–30% of ordering): start with an expert-reviewed DAG; use learnable-prerequisite methods (PKT) only after 50k+ interactions.
- **BKT cold start**: needs ~50 obs/skill and informative priors; pre-readers have high guess (picture/memory cues) and slip rates. Budget a 12–15 item diagnostic screener (model on DIBELS NWF + PSF, ~8 min).
- **Decodable-text scarcity**: no large public algorithmically-tagged corpus; most are IP-protected. Build a decodability tagger over public-domain/OER text using your GPC set.
- **GPU/ops** for self-hosted Whisper (managed GPU: Modal/RunPod/Replicate).

## Recommended Stack

**Phase 0 — Graph + recommender (NOW, zero child-data risk):**
- Neo4j (local Docker → AuraDB) for the content graph you already have + a learner overlay (`Learner`, `Skill/Grapheme/Phoneme`, `HAS_MASTERY`, `PREREQUISITE`, `REQUIRES`).
- pyBKT per skill node; mastery-decay fallback before fit.
- Deterministic Cypher ZPD selector for next-best-word; FSRS for review.
- Claude Haiku 4.5 (tool_use + prompt caching) for rationale/content; LangGraph 3-agent loop (Diagnostician/Planner/Tutor) when session state is needed.
- Pydantic v2 models; uv; structlog.

**Phase 1 — Assessment ingestion:** wire the existing assessment platform's responses into BKT updates (no audio yet).

**Phase 2 — Speech (gated on compliance):** Azure PA managed first; self-hosted fine-tuned Whisper large-v3 + WhisperX forced alignment against ARPABET for sovereignty. Build the miscue classifier in-house.

## Open Questions

1. **Decodable-text corpus** — license (Bob Books/Dandelion/Flyleaf) vs. build a tagger over OER? This gates the recommendation layer.
2. **Compliance posture** — is the POC synthetic-data-only (recommended) until DPAs + parental-consent flow exist? Who owns legal review?
3. **In-house child audio** — does the existing assessment platform already capture labeled child speech usable (with consent) for fine-tuning?
4. **Mastery threshold policy** — p(mastery) by grade band (PreK lower, Gr3 ≥0.95)?
5. **Graph scope** — word-recognition strands only, or full Scarborough Rope (5–10× graph size)?
6. **Phoneme→standards crosswalk** — no public map from Azure phoneme scores to Science-of-Reading skills; needs a curriculum expert.

## Sources

Graph/KT: [neo4j driver 6.2](https://pypi.org/project/neo4j/) · [GDS 2026.05](https://neo4j.com/docs/graph-data-science/current/) · [neo4j-graphrag 1.17](https://pypi.org/project/neo4j-graphrag/) · [Apple acquires Kùzu](https://betakit.com/apple-strikes-deal-to-acquire-canadian-database-software-startup-kuzu/) · [pyBKT releases](https://github.com/CAHLR/pyBKT/releases) · [pyKT](https://pykt-toolkit.readthedocs.io/en/latest/models.html)
ITS/architecture: [OATutor](https://github.com/CAHLR/OATutor) · [PKT/ZPDES-KS arXiv 2402.01672](https://arxiv.org/abs/2402.01672) · [GraphMASAL arXiv 2511.11035](https://arxiv.org/abs/2511.11035) · [KG+DRL path rec PMC12494970](https://pmc.ncbi.nlm.nih.gov/articles/PMC12494970/) · [Duolingo HLR](https://github.com/duolingo/halflife-regression) · [py-fsrs](https://github.com/open-spaced-repetition/py-fsrs) · [Letters and Sounds (DfES)](https://assets.publishing.service.gov.uk/government/uploads/system/uploads/attachment_data/file/190599/Letters_and_Sounds_-_DFES-00281-2007.pdf)
Speech: [Amira science of reading](https://amiralearning.com/science-of-reading) · [Amira patent US8306822](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8306822) · [WhisperX](https://github.com/m-bain/whisperX) · [Wav2TextGrid child alignment (PubMed 2025)](https://pubmed.ncbi.nlm.nih.gov/40163771/) · [Kid-Whisper](https://dl.acm.org/doi/10.5555/3716662.3716669) · [Child ASR WER arXiv 2404.17394](https://arxiv.org/pdf/2404.17394) · [Miscue detection arXiv 2406.07060](https://arxiv.org/abs/2406.07060) · [SoapBox acquired](https://www.prnewswire.com/news-releases/curriculum-associates-expands-student-focused-ai-capabilities-with-purchase-of-speech-recognition-leader-soapbox-labs-301999761.html) · [Azure PA limits](https://learn.microsoft.com/en-us/azure/foundry/responsible-ai/speech-service/pronunciation-assessment/characteristics-and-limitations-pronunciation-assessment)
LLM/compliance: [Claude pricing](https://platform.claude.com/docs/en/about-claude/pricing) · [Claude prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) · [Anthropic minors guidelines](https://support.claude.com/en/articles/9307344-responsible-use-of-anthropic-s-models-guidelines-for-organizations-serving-minors) · [COPPA Final Rule (Apr 2025)](https://www.federalregister.gov/documents/2025/04/22/2025-05904/childrens-online-privacy-protection-rule) · [FTC COPPA AI-training consent](https://www.dataprotectionreport.com/2025/06/ftcs-coppa-rule-changes-include-ai-training-consent-requirement/) · [Azure FERPA](https://learn.microsoft.com/en-us/compliance/regulatory/offering-ferpa) · [SOPIPA](https://www.commonsensemedia.org/kids-action/about-us/our-issues/digital-life/sopipa)
