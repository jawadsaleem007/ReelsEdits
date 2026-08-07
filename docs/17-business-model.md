# 17 — Business Model

---

## 1. Pricing

| Tier | Price | Renders/mo | Resolution | Key features |
|---|---:|---:|---|---|
| **Free** | $0 | 3 | 720p, watermark | Catalogue music only. Enough to judge output quality, not enough to run a channel. |
| **Creator** | $29/mo | 40 | 1080p | No watermark, style library, full catalogue, project export |
| **Pro** | $79/mo | 150 | 4K | Blueprint editing, custom LUTs and fonts, priority GPU, OTIO/EDL export, VLM re-rank |
| **Team** | $249/mo (5 seats) | 600 | 4K | Shared style libraries, brand kits, review workflow, SSO, admin |
| **Enterprise** | from $2,000/mo | negotiated | — | DPA, private deployment options, custom licensing, SLA, support |
| **API** | usage-based | — | — | $0.60–$1.40 per render-minute ([docs/12 §9](12-api-design.md#9-api-tier-pricing)) |

Annual billing at 2 months free (17% discount).

### 1.1 Why $29 and not $15

$15 is where the category sits, and matching it would be a mistake for three reasons.

**Our COGS is structurally higher.** Per-reference GPU analysis is a real cost that decoration tools do not carry ([docs/14](14-cost-model.md)). Pricing at $15 with an $0.24 blended cost per render and a 40-render quota leaves no room for the free tier, support, or a sales motion.

**We are not selling the same thing.** OpusClip excerpts, Submagic decorates. We assemble from a specification extracted from an arbitrary reference. Pricing at parity would signal parity, and the entire positioning ([docs/02 §5](02-competitive-analysis.md#5-positioning)) depends on not doing that.

**The comparison set is a freelance editor.** A creator paying $200–400 per edited video is our actual alternative, not another $15 SaaS. At $29 for 40 renders, the comparison is favourable by two orders of magnitude, and framing it that way is more honest than competing on price with tools that solve a different problem.

**Free tier at 3 renders, watermarked.** Enough to see whether the output is good, not enough to run a channel. Costs $0.33/user/month ([docs/14 §4](14-cost-model.md#4-margin-by-tier)) — at 9% conversion, each paying user covers ~11 non-converting ones.

---

## 2. The marketplace

Creators list blueprints as products. 70/30 split in the creator's favour.

**Why it matters more than the revenue:**

**It makes the blueprint format valuable to third parties**, which is the path to it becoming a standard ([docs/01 §7](01-product-vision.md#7-success-defined)).

**It gives successful creators a reason to stay.** A creator earning $400/month from blueprint sales does not churn.

**It solves cold-start for new users.** A new user with no reference in mind can buy a proven style, which converts a blank-page problem into a shopping problem.

**Only possible because renders are deterministic.** A blueprint someone buys must produce what they previewed. This is a concrete example of an unglamorous infrastructure decision ([docs/10 §1](10-rendering-engine.md#1-the-determinism-requirement)) enabling a business model eighteen months later.

**Requirements before launch:** review queue (moderation, quality floor, originality check), preview renders on stock footage, payouts via Stripe Connect, refunds, and a dispute process. Realistically a phase-3 item ([docs/15 §5](15-engineering-roadmap.md#5-phase-3--platform-months-1520)).

**Projection:** 500 live listings by month 18, ~3.2 purchases per listing per month at a $12 median → ~$19k GMV/month, ~$5.7k net. Small revenue, large retention effect.

---

## 3. Revenue projection

Conservative case. Assumes 9% free→paid, 4.5% monthly churn on Creator, 2.8% on Pro, 1.4% on Team.

| Month | Free | Creator | Pro | Team | API | MRR | ARR run-rate |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 6 | 800 | 45 | 8 | 0 | $0 | **$1,937** | $23k |
| 9 | 3,200 | 210 | 41 | 3 | $200 | **$10,146** | $122k |
| 12 | 9,500 | 720 | 155 | 14 | $1,800 | **$38,501** | $462k |
| 15 | 21,000 | 1,680 | 390 | 38 | $5,400 | **$93,282** | $1.12M |
| 18 | 41,000 | 3,400 | 860 | 92 | $14,000 | **$203,548** | $2.44M |
| 21 | 68,000 | 5,900 | 1,540 | 178 | $28,000 | **$364,032** | $4.37M |
| 24 | 105,000 | 9,200 | 2,580 | 310 | $52,000 | **$599,410** | **$7.19M** |

**Series A milestone (month 18):** ~$204k MRR, 45k MAU, 250k blueprint corpus, matcher outperforming sequential at >75% preference, API live with three integration partners.

**Aggressive case** (14% conversion, 3% churn, viral coefficient >0.3 from marketplace and social sharing): **$1.4M MRR at month 24**.

**Pessimistic case** (6% conversion, 7% churn): **$210k MRR at month 24** — still a business, but a Series A that needs a story about churn rather than growth.

---

## 4. Unit economics

| Metric | Creator | Pro | Team |
|---|---:|---:|---:|
| ARPU | $29 | $79 | $249 |
| Gross margin (median use) | 86% | 79% | 77% |
| Monthly churn | 4.5% | 2.8% | 1.4% |
| Lifetime (months) | 22 | 36 | 71 |
| **LTV** | **$549** | **$2,246** | **$13,616** |
| Blended CAC | $61 | $184 | $1,420 |
| **LTV:CAC** | **9.0** | **12.2** | **9.6** |
| Payback (months) | 2.4 | 2.9 | 7.4 |

**Blended LTV:CAC ≈ 9.8** — well above the 3.0 threshold, which reflects a low-CAC creator motion rather than unusual product economics. The number to watch is Creator churn: at 4.5% it is healthy; at 8% LTV halves to $293 and the ratio falls to 4.8, which is fine but changes how much can be spent on paid acquisition.

---

## 5. Go-to-market

### Phase 1 — Automotive wedge (months 1–9)

Not a broad launch. A specific, concentrated community.

**Why automotive:** reference-imitation is already the dominant behaviour; the visual grammar is tight and repeatable (rolling shots, wheel details, exhaust close-ups, sunset b-roll); the community is loud, concentrated on two platforms, and demonstrably willing to spend money on their hobby.

**Tactics:**
- 30 hand-picked creators (5k–80k followers) given free Pro and direct access to the team. Not influencer marketing — a design partnership.
- Publish side-by-side comparisons: reference style, user footage, our output. The product demonstrates itself better than any copy describes it.
- Presence in car-community Discords and subreddits, participating rather than advertising.
- A free "shoot list" tool built from the coverage report — genuinely useful standalone, and a natural top of funnel.

**Target:** 800 free, 45 paid by month 6. Small numbers on purpose; the goal is a *tight* loop with real users, not a vanity chart.

### Phase 2 — Adjacent verticals (months 9–15)

Fitness, then travel. Same playbook: 30 creators, comparison content, community presence.

The strategic test is whether expansion is a **configuration** change — new SAM 3 concept prompts, re-weighted matcher — or an **engineering** change. If it is engineering, the expansion thesis is wrong and needs rework before scaling GTM spend ([docs/16 §R8](16-risks.md)).

### Phase 3 — Horizontal (months 15–24)

- **Product-led growth.** Watermarked free-tier output carries attribution; the marketplace creates social proof.
- **Content marketing.** Technical writing on editing craft — the material in [docs/08](08-algorithms.md) is genuinely interesting to editors, and publishing it builds credibility that advertising cannot.
- **API and integrations.** Every integration partner is distribution we do not pay for.
- **Agency motion.** Outbound to social agencies for Team and Enterprise. Higher CAC, much higher LTV.

---

## 6. What we are not doing

**No freemium-to-enterprise leap.** Enterprise sales before month 15 would consume the engineering focus that phase 0 and 1 need.

**No paid acquisition before month 9.** Buying users before the product retains them is how startups convert runway into a churn chart. CAC is only meaningful once LTV is measured rather than modelled.

**No white-label.** It is the fastest way to become a commodity supplier to someone else's brand, and it forfeits the swap data that trains the matcher.

**No advertising model.** The product's value is craft, not attention, and monetising attention would put the product's incentives against its users'.

---

## 7. Funding

**Seed: $4.5M for 24 months** ([docs/00](00-executive-summary.md#the-ask)).

| Allocation | Amount |
|---|---:|
| Engineering (7 FTE) | $2.5M |
| Compute (training + inference) | $900k |
| Music licensing (minimums + legal) | $350k |
| GTM | $450k |
| Legal, ops, G&A | $300k |

**Series A: $18–25M at month 18–20**, against ~$204k MRR, 45k MAU, and the three compounding assets. Use of funds: multi-region, the learned blueprint model, marketplace scale, and the agency sales motion.

**Milestones the Series A story rests on:** matcher measurably beating sequential from real swap data (not a lab study), a blueprint corpus large enough to train on, and a second vertical validating that expansion is configuration rather than engineering.

---

Next: [18 — Legal & Ethics](18-legal-ethics.md)
