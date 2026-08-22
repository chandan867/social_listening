# REQUIREMENTS — Social Listening → Trust Content + Lead Engine

**Owner:** Chandan · **Status:** v1 · **Date:** 2026-08-22  
**Prime metric:** conversations with ICP per week. Everything below exists to move that number.  
**Non-goals:** dashboards, auto-DM/auto-posting, embeddings/clustering, multi-tenant anything.

---

## 1. ICP (who a "lead" is)

**Owner or GM of a US local-service business** (roofing, solar, HVAC, painting) or a **recruitment/staffing agency principal**, expressing a problem our services solve: website/SEO, custom software, AI automation.

Not ICP: practitioners (devs, marketers, other agencies), hobbyists, employees venting, students. A post asking "how do I build websites for clients" is a **competitor**, not a lead. This distinction is the #1 current failure mode.

---

## 2. R1 — Source targeting

**Requirement:** every lead-feed source must be a place where ICP *owners* post, not where our craft is discussed. Craft/trend sources are allowed but must be tagged `trend`, never scored for leads.

### 2.1 Reddit lead sources

| Subreddit | Rationale | Status |
|---|---|---|
| r/sweatystartup | local-service owner-operators, marketing/website questions constantly | **verified live 2026-08-22** (RSS fetched, owner post about cleaning-business website) |
| r/smallbusiness | keep — broad but real owners | already in config |
| r/Entrepreneur | keep on probation — noisy, audit at week 2 | already in config |
| r/Contractor | GC/trade owners | verify via RSS before trusting |
| r/Roofing | mixed techs+owners; owners ask marketing Qs | verify |
| r/HVAC | mixed; probation | verify |
| r/solar | mixed industry/consumer; probation | verify |
| r/recruiting | agency recruiters + founders | verify |
| r/agencyowners / r/staffingagency | if exists/active | verify |

**Remove from lead feed:** r/webdev, r/web_design, r/agency, r/automation, r/artificial, r/SaaS → move to `TREND_SUBREDDITS` feeding `daily.py` only.

**Verification protocol (per candidate, uses existing RSS fetcher):** pull `/new/.rss` once; PASS if ≥10 posts in last 7 days AND ≥2 of newest 25 are owner-voice ("my business/my crew/my customers"). FAIL → drop, log why in config comment.

Config implements this as `LEAD_SUBREDDITS` + `TREND_SUBREDDITS` (see `config.py`).

### 2.2 Facebook

Fact: public keyword search (current opencli path) skims strangers' public posts; real ICP density is in **closed groups**, which require a joined member account.

- **FR-1.1:** Rewrite `FACEBOOK_QUERIES` vertical-first, owner-voice: `roofing business slow season leads`, `hvac company website customers`, etc. Service-first queries ("need a website") attract freelancer spam — demoted.
- **FR-1.2:** With founder profile, manually join 5–8 owner groups (search FB for: "Roofing business owners", "HVAC business owners", "Painting contractors business", "Recruitment agency owners", "Solar sales professionals"; pick size >5k + posts today). Group monitoring stays **manual, 15 min/day** — logged-in scraping of joined groups risks the profile needed for posting.

### 2.3 Acceptance for R1

After 14 days: manual audit of `leads.csv` → **≥5 true ICP leads/week and ≥40% precision** among qualified rows. Below that, fix sources before touching any other layer.

---

## 3. R2 — Intent engine (LLM classification)

Keyword scoring stays as cheap pre-filter. LLM (existing local gateway at `LLM_BASE_URL`) becomes the qualifier.

- **FR-2.1:** Each 3h cycle, every captured post with keyword score ≥ `INTENT_MIN_SCORE_FOR_LLM` (10) **or** containing a `VERTICAL_TERMS` hit goes through batched LLM calls (`INTENT_BATCH_SIZE` 16). Output per post, strict JSON: `{"id","icp","author_role","intent","urgency","one_line","confidence"}`.
- **FR-2.2 (qualified lead):** `author_role ∈ {owner,unknown}` AND `intent ∈ {buying,pain}` AND `icp ≠ not_icp` AND `confidence ≥ 0.6`. Replaces `score>=25` as CSV gate; keyword score kept as column for comparison.
- **FR-2.3 (fast lane):** `intent=buying` + `urgency=now` → immediate Telegram ping with permalink + `one_line` + drafted reply, outside 3h batch.
- **FR-2.4:** LLM gateway down → degrade to keyword gate, tag rows `classifier=keyword`, warn in digest. Never lose a cycle.
- **FR-2.5 (golden set):** 40 hand-labeled posts in `data/golden.jsonl` — 10 true buying-intent, 10 owner-pain, 10 practitioner traps, 10 noise. `--self-test` runs classifier fixtures against it; **gate: ≥80% precision, ≥70% recall on qualified**. Prompt edits that fail gate don't ship.

---

## 4. R3 — Trust content engine

Current `daily.py` voice rules are good. Keep.

- **FR-3.1:** Weekly pain-theme digest: cluster week's `intent=pain` items by `icp` + theme, output top 5 with **verbatim quotes**. LLM summary must carry ≥1 verbatim quote per theme or theme dropped.
- **FR-3.2:** Every published post gets a row in `data/posts.csv`: date, platform, theme, source-pain-link.
- **FR-3.3:** Content mix rule: ≥50% posts must originate from a logged pain theme. Checked at weekly review, by eye.

---

## 5. R4 — Loop closure

- **FR-4.1:** `leads.csv` gains `status` (`new|contacted|replied|call|won|dead`) and `note`. Hand-edited. No CRM until >20 active leads/week forces one.
- **FR-4.2:** Weekly 15-min review ritual: count qualified leads, precision (spot-check 10), replies, calls; which post themes drew ICP comments; one config/prompt tweak max per week.
- **FR-4.3:** Kill criteria: source 0 qualified leads for 3 weeks → cut. Vertical 0 replies in 6 weeks → halve its queries.

---

## 6. Test plan (what "working" means)

| Layer | Test | Gate |
|---|---|---|
| Sources | RSS verification protocol (2.1) | each source PASSes before entering config |
| Classifier | golden-set self-test (FR-2.5) | ≥80% precision / ≥70% recall (fixture mode) |
| Pipeline | `--self-test` + `--dry-run` | stays green; classifier-down degradation covered |
| Leads e2e | 14-day audit (2.3) | ≥5 ICP leads/wk, ≥40% precision |
| Content | weekly review (FR-3.3) | ≥50% pain-sourced posts; track ICP comments/post |
| Business | monthly | conversations with ICP/wk trending up; ≥1 call by day 45 or revisit ICP |

**Build order:** R1 (config-only) → FR-4.1 status column → R2 classifier + golden set → FR-2.3 fast lane → FR-3.1 weekly pain digest. Nothing else until 14-day audit passes.

