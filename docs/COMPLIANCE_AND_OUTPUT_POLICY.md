# WC26 Predict — Compliance and Output Policy

WC26 Predict must maintain a strict boundary between internal research and public-facing content.

---

## 1. Core rule

WC26 Predict is a football research system, not a gambling product.

The system may use market odds and bookmaker consensus as important research signals. Public or creator-facing output must never turn those signals into betting advice, guaranteed outcomes, or gambling promotion.

---

## 2. Output modes

### 2.1 internal_research

Allowed:

- model probabilities
- calibration diagnostics
- Brier score / RPS / log loss
- internal market consensus comparison
- feature importance notes
- raw debugging information

Not allowed:

- public marketing claims
- betting advice
- guaranteed-outcome language

### 2.2 creator_safe

Allowed:

- team context
- form summaries
- historical comparisons
- uncertainty notes
- safe talking points
- data source references
- market odds / bookmaker consensus as data evidence
- probabilities and uncertainty ranges
- source/provenance notes

Not allowed:

- betting advice or calls to action
- guaranteed wins / profit claims
- bookmaker promotion or affiliate-style language
- "pick" or "best bet" phrasing
- hit-rate claims

### 2.3 public_safe

Allowed:

- educational football analysis
- rankings
- historical trends
- schedule context
- public-safe charts
- explainable uncertainty

Not allowed:

- raw probabilities if they are framed as guaranteed outcomes
- score guarantees
- betting-related calls to action
- gambling terms

---

## 3. Forbidden public terms

Public-facing pages, reports, README marketing sections, and social content should avoid advice-like or promotional terms:

```text
赔率推荐
竞彩
带单
稳赚
爆单
必胜
推单
best bet
betting tips
odds pick
guaranteed prediction
sure win
```

Allowed when used as evidence, not advice: `赔率`, `盘口`, `博彩`, `odds`, `bookmaker`, `sportsbook`, market consensus tables, and bookmaker-count diagnostics.

Technical docs may mention these terms only when describing compliance restrictions.

---

## 4. Market consensus data policy

Market consensus data is an important research and prediction signal.

Allowed internal uses:

- compare model probability with market consensus
- estimate calibration gaps
- run shadow-mode evaluation
- study uncertainty

Forbidden uses:

- encourage betting
- monetize betting signals
- imply profitable betting recommendations

---

## 4.1 Information-state evidence policy

News, injuries, lineups, weather, travel context, and market odds must be stored
as traceable evidence before they influence analysis. V4.10 evidence records must
carry a source URL or internal snapshot URI, an availability time, and a
reliability score.

LLM systems may extract and classify signals from evidence. They must not
directly create final match probabilities, betting calls to action, or automatic
production-weight changes.

---

## 5. Safe report language

Recommended wording:

- "model-based analysis"
- "uncertainty remains high"
- "historical data suggests"
- "the system currently rates this matchup as balanced"
- "creator-safe summary"

Avoid:

- "guaranteed win"
- "best bet"
- "high hit-rate pick"
- "bet this side"

---

## 6. Required checks before public release

Before publishing any report or demo:

```bash
cd backend
python scripts/audit_public_outputs.py
```

The audit allows market odds and bookmaker consensus as research evidence. It only blocks unsafe betting-advice or guaranteed-outcome language.

Also manually inspect:

- README
- docs
- dashboard UI
- generated reports
- social media screenshots
- landing page copy

---

## 7. Disclaimer template

Use this disclaimer in public pages:

> WC26 Predict is an AI-assisted football research and analytics project. Outputs are uncertain and based on available data, model assumptions, and system configuration. They are provided for research, education, and content preparation only. They are not betting advice, financial advice, or guaranteed predictions.
