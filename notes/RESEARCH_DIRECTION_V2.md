# Research Direction V2 — The New North Star

> This document supersedes the signal-screening framing in
> `ARCHITECTURE_REVIEW.md`'s "what's next" section. The infrastructure
> described in that file (WS1-WS5) remains valid and built. What changes here
> is the *research thesis* — what we're looking for and why.
>
> Written for a researcher joining tomorrow. Read this first. It explains not
> just what we're building, but how we now think about the problem after
> testing four signals to null, interrogating our own premises, and making
> the deliberate call to start paper trading in parallel with continued
> research rather than waiting for research to fully resolve first.

---

## 1. The one-paragraph version

We built a rigorous backtesting harness and used it to test simple, well-known
directional signals (time-series momentum at short AND long horizons,
open-interest extremes, cross-sectional momentum) on Binance USDT-M perpetual
futures, with honest costs and out-of-sample validation. All four showed no
edge — confirmed across full calendar years, across hold times, and across
lookback horizons. This is the *expected* result, not a failure: simple
directional prediction from public price data is the most competed-away,
least likely place to find surviving alpha. The central lesson is a reframe:
**we were asking "what can I predict?" when the higher-value question is
"what am I getting paid to provide, who is paying me, and how long until they
stop?"** A real structural premium (funding carry) was found and partially
disproven (the unhedged version fails to directional risk); a deployable,
edge-independent path (a vol-targeted portfolio) was identified and is now
being **paper traded**, in parallel with continued research into whether a
properly hedged carry strategy is viable. The project moved from
sequential ("research fully, then maybe deploy") to parallel ("deploy the one
thing already justified, keep researching the rest alongside it").

---

## 2. What we conclusively learned

- **Simple directional signals on liquid crypto perps show no edge after honest
  testing.** Three structurally different ideas, all null. This is robust: it
  held across full years, across 1h and 24h holds, and the null was confirmed
  by the signal-vs-control gap (the metric that was always valid), not just by
  deflated PF.
- **A "no edge" result, honestly obtained, is the normal outcome of honest
  research — not a sign of doing it wrong.** Most strategies that *look* good in
  retail backtests are artifacts of self-deception (lookahead, survivorship,
  small samples, optimistic fills). Our results are unglamorous precisely
  because we removed those artifacts.
- **The hardest bugs live in "finished" infrastructure, and pass their own
  gates.** The calendar-vs-sample-count splitter bug and the binary-switch
  deflated-PF bug both passed their original decision gates and silently
  corrupted results for an entire working session. They were caught only by
  asking "given this exact output pattern, is that even mathematically
  possible?" — not by re-running the gate.
- **Costs are decisive and were nearly modeled away.** Real round-trip costs at
  this turnover (~19k trades/year) are punishing. Our execution model currently
  charges slippage only (no exchange fees) — making it ~3.5x optimistic versus
  real Binance taker costs. This doesn't affect signal-vs-control screening
  (costs cancel) but is fatal to any "is this live-ready" claim.

---

## 3. Assumptions strengthened

- **Out-of-sample / point-in-time / deflation rigor is non-negotiable and it
  works.** Every time it cost us a comfortable-looking number, it was right to.
- **The signal-vs-control comparison is the correct screening primitive.**
  Because both legs pay identical costs, the gap isolates genuine directional
  content and is immune to the absolute cost level. This survived every test
  and caught every false positive.
- **"Verify what a number is built on" is the highest-value research habit we
  have.** Every real bug, every retracted conclusion, came from one more layer
  of that question after a clean-looking pass.

---

## 4. Assumptions weakened or rejected

- **REJECTED: that edge lives primarily in the time-series signal.** It mostly
  doesn't. At serious firms the signal is often the least important component;
  execution, inventory, and structure dominate. We spent ~all our research
  budget on signal discovery and ~none on execution edge. That allocation was
  backwards.
- **REJECTED: that OHLCV is sufficient to ask the questions that matter.** OHLCV
  is the *exhaust* of the real market process. The events that generate edge in
  perps (order-flow, queue position, liquidity provision, who's forcing whom)
  happen at a resolution our data cannot represent. We have been doing forensics
  on smoke and concluding there is no fire.
- **WEAKENED: that "find the strategy" is even the right unit.** The
  professional unit of edge is a *pipeline* that manufactures and retires many
  weak, decaying, uncorrelated micro-edges — not one durable strategy. Our
  harness is well-suited to be that pipeline; our mental model was still
  "find the one that works."
- **REJECTED: that the market is a natural phenomenon to be predicted.** Crypto
  perps are an *adversarial* system with reflexive behavior — liquidation
  hunting, funding games, wash trading. Predicting it like weather ignores that
  participants profit from our model being wrong.
- **WEAKENED: that finding edges that "work in general" is the goal.** Our one
  potential structural advantage is being *small* — able to harvest edges with
  capacity too tiny to interest firms, and to hold over horizons where latency
  doesn't matter. We were searching for general edges and ignoring the
  small-only niche that is actually ours.

---

## 5. Mistakes we made and what they taught us

- **We repeated unverified "facts" until they felt true.** "9 signal engines"
  (never existed) and "funding data already downloaded" (it was OI data, not
  funding) both propagated across sessions unchecked. *Lesson: any background
  fact not traceable to an actual file inspection is a hypothesis, not a fact —
  including facts stated confidently by an AI assistant.*
- **We let agents loosen definitions to manufacture results.** An OI signal's
  "extreme" threshold got walked from z>=2.0 to z>=0.0 to produce trades,
  silently destroying the hypothesis being tested. *Lesson: the parameter that
  defines the event is locked before running and only ever tightened, never
  loosened to hit a trade count. Trade scarcity is information, not a bug.*
- **We trusted "decision gate passed" before checking what the gate actually
  exercised.** Synthetic-data tests, curated (non-random) "random" controls,
  and gates run on toy data that hid production bugs all passed cleanly while
  being wrong. *Lesson: a passing gate proves the gate ran, not that the thing
  is correct. Suspicious-clean numbers (PF=0, PF=inf, deflated-Sharpe=1.0)
  deserve more scrutiny than ugly ones.*
- **We almost mistook motion for progress.** After three nulls the instinct was
  "build signal #5." Stepping back to question the *premise* was worth more than
  any incremental signal would have been. *Lesson: a streak of clean nulls is
  itself a finding about the question, not just about the signals.*

---

## 6. Principles for every future research decision

1. **Name the counterparty.** Before testing anything, answer: *who is
   systematically on the other side of this trade, and why do they keep losing
   or not caring?* If there's no clear answer, it's not edge — it's noise we
   might get lucky on. The structural strategies survive precisely because they
   have an answer (the leverage-seeker pays funding; the margin engine forces
   the liquidation).
2. **Prefer compensation over prediction.** Getting paid to provide something
   (liquidity, immediacy, insurance, a counterparty to forced flow) survives
   honest testing far more often than guessing direction. Prediction is the
   retail instinct; provision is the professional one.
3. **Lock definitions before running; tighten only, never loosen.** Trade
   scarcity is a finding, not a problem to engineer around.
4. **Screen on the signal-vs-control gap, not absolute PF/Sharpe**, until a
   metric is proven graded (deflated PF was a binary switch for an entire
   session before this was caught).
5. **Treat every "passed" from infrastructure as provisional** until the output
   pattern itself is sanity-checked against what's mathematically possible.
6. **Account for capacity and decay, not just historical fit.** Ask how fast an
   edge dies once others find it and how much capital it absorbs before our own
   trading destroys it. Our smallness may be our only structural advantage —
   design for it deliberately.
7. **Cost realism before any live claim.** The execution model must charge real
   fees before any result is called paper-ready, even though costs cancel in
   screening.
8. **A clean null is a complete, respectable result.** Bank the learning and
   move; do not torture a dead signal into looking alive.

---

## 7. How the research philosophy evolved

- **From:** find a predictive signal in the price history → deploy a bot.
- **To:** identify a structural compensation we can name a counterparty for,
  that is too small or too slow-horizon for firms to bother with, harvest it,
  and monitor it for decay — running a *process*, not seeking a single answer.

The emotional reorganization matters as much as the technical one: success is
no longer "found the edge" but "ran an honest process that either found a
nameable structural premium or correctly concluded none is accessible from our
seat."

---

## 8. Questions — resolved, and still genuinely open

**Resolved since this document was first written:**

- **Is there a harvestable funding-carry premium?** Partially answered.
  Real funding-rate data was fetched (2022-2025, full universe). A real gross
  premium exists — median ~12.5%/yr, present in every calendar year, with a
  nameable counterparty (leveraged longs paying to hold exposure). But the
  **unhedged** version (simply holding the short side) fails 2 of 4 years —
  directional P&L (±40%/yr swings) completely dominates the funding income
  (+1-5%/yr). The clean, delta-neutral version (short perp + long spot) was
  never tested — we lack spot data and spot execution infrastructure. **Open
  sub-question, not yet answered: does the hedged version clear real,
  two-legged costs with margin?**
- **Are liquidation-cascade overshoots harvestable?** Resolved: NO usable
  public data exists. `data.binance.vision` has no liquidation archive; the
  relevant Binance REST endpoint is decommissioned. Third-party sources are
  paid/gated. This direction is closed, not pending — don't revisit without a
  paid data source.
- **Does a longer-horizon version of momentum work, where the short-horizon
  version didn't?** Resolved: NO. Tested explicitly at 20d/60d/90d/180d
  lookbacks against a signal-vs-control gap. Longer horizons performed *worse*,
  not better (180d was the worst-performing combo) — the opposite of what real
  trend persistence would produce. The one year that looked promising (2022)
  was explained: the random control also won that year, because 2022's crash
  was a single uninterrupted trend any directional bet would have caught.
  This closes the "maybe momentum just needed a longer lookback" question for
  good — four signals (WS6 short, WS6 long-horizon, WS7, WS8) are now null.

**Still genuinely open:**

- **Does delta-neutral funding carry (with a real spot hedge) clear real,
  two-legged costs with margin?** The single most important open question —
  see Section 9.
- **Does crypto have persistent cross-sectional factor structure** beyond
  momentum (e.g. low-vol, size), or is the 30-name universe dominated by
  BTC-beta with no real cross-section? Untested, lower priority than the
  carry-hedge question.
- **Can our smallness be turned into a named advantage** — a specific niche too
  small for firms — rather than a vague hope? Still unaddressed.
- **Does disciplined vol-targeting alone (no directional edge) beat
  buy-and-hold BTC on a risk-adjusted basis, in live conditions?** This is no
  longer a research question to backtest further — see Section 9, it's now a
  paper-trading question.

---

## 9. Roadmap — current status and what's actually next

**Step 0 (fix infrastructure debts) — mostly done.** Real exchange fees were
wired into the WS5 execution model during the carry investigation. *Verify
this is applied consistently across all harnesses (WS6/WS7/WS8 paths), not
just the script it was first added to, before relying on it elsewhere.* The
deflated-PF per-trade fix is confirmed in place.

**Step 1 (characterize structural premia, no building) — done.** Funding:
real premium exists but unhedged version fails 2/4 years to directional risk.
Liquidations: no usable public data, direction closed. Long-horizon momentum:
tested as a side-investigation, confirmed null. See Section 8.

**The roadmap below supersedes the original Step 2/Step 3 ordering** — a
deliberate cofounder decision was made (see Section 9a) to promote the
vol-targeted portfolio ahead of the carry-hedge build, and to stop sequencing
research strictly before deployment. Two tracks now run **in parallel**:

### Track A — Paper trading (start now, do not wait on Track B)

Build and paper-trade the **vol-targeted portfolio** (no directional signal,
volatility-targeted sizing across the WS4 universe). This needs no new data
and no unresolved research question — it was always the "humble but
deployable" option and is now the priority deliverable, not a fallback.
Success metric: better risk-adjusted return than holding BTC, measured over
real paper-trading weeks, not backtest years. See Section 9a for the full
reasoning and Section 9b for what must be true before switching it on.

### Track B — Research (parallel, not blocking Track A)

Investigate whether **delta-neutral funding carry** (short perp + long spot,
or equivalent hedge) clears real, two-legged transaction costs with margin.
This requires new infrastructure we don't have: spot price data and a spot
execution model. Treat this as a real, possibly multi-session build — don't
let it block or delay Track A.

**Explicitly closed, not "deprioritized":** liquidation-cascade strategies (no
data exists). **Deprioritized, revisit only if Track A/B stall:** more
directional prediction signals (four nulls is enough evidence for now);
cross-sectional factors beyond momentum; latency-dependent games (arb,
market-making) we structurally cannot win; on-chain data (cost/effort).

---

## 9a. The paper-trading decision (cofounder call, recorded)

A direct question was put to the research process: *given everything learned,
should we paper trade now, or insist on more research first?* The answer was
**yes, start now**, reasoned as follows — recorded here so the decision isn't
silently relitigated later:

- Four directional signals are null. The marginal value of a fifth backtest is
  low; the value of *operational* data (real fills, real latency, real
  unattended-uptime, whether WS5's cost model is still optimistic once tested
  against real fills) is high and **only obtainable by actually running
  something live (on paper).**
- Backtesting further risks "infinite refinement of a map we never walk."
  Paper trading risks nothing financially and teaches a different, necessary
  category of lesson that no backtest can.
- The thing going to paper — the vol-targeted portfolio — was deliberately
  chosen *because* it doesn't depend on any unresolved research question. It
  is not "paper trading a guess"; it is "operating the one idea that's already
  fully justified by Section 9's logic, while research continues on the
  ideas that aren't yet justified."

---

## 9b. What the first paper-trading version must and must not do

**Must attempt:** vol-targeted sizing only, across the real WS4 point-in-time
universe, no directional signal. This is the entire scope.

**Must NOT attempt:** any of WS6/WS7/WS8 (confirmed null — don't paper-trade a
disproven idea), the unhedged carry trade (confirmed to lose 2/4 years —
already disproven, not a paper-trading candidate), or any use of real capital.

**Must be in place before switching it on:**
- Step 0's fee fix confirmed applied to this path specifically.
- A dead-man's switch / alerting mechanism — something that flags if the
  system stops logging or stops trading unexpectedly. This is the single most
  likely real failure mode for an unattended system, and the most important
  thing to catch quickly.

**What success looks like in the first few weeks:** NOT "made money" — three
weeks is too short to judge magnitude. The real bar: the system ran
continuously without manual intervention, sizing actually tracked realized
volatility the way WS5 predicts, and the shape of paper P&L is plausible given
real market conditions. A silent multi-hour outage that goes unnoticed is a
bigger failure than a red week.

**What to log on every single trade** (this is what makes future iteration
evidence-driven rather than another guess): exact signal/sizing inputs at
decision time; intended vs. actual fill price and timestamp (the only way to
learn whether WS5's slippage model, already found 3.5x optimistic once, is
still wrong); latency from decision to order placement; any error or retry.
This data is what eventually allows a real backtest-vs-live reconciliation
study — closing the loop this project has never had.

---

## 10. Decision gates — fired, current, and forward-looking

**The Step 1 gate has already fired.** Result: **MARGINAL / PIVOT** — neither
clean CONTINUE nor clean STOP. The funding premium is real (supports
CONTINUE), but the unhedged version fails the cost-with-margin test in 2/4
years (blocks CONTINUE as originally specified). Per the standing stop-rule,
the unhedged carry trade does **not** get built. The resolution: split into
the two parallel tracks in Section 9 rather than forcing a single verdict.

**Forward-looking gates, now that paper trading has started:**

- **Track A (vol-targeted portfolio) continues** as long as it runs reliably
  and its risk-adjusted performance is plausible given conditions — see the
  weekly-success criteria in 9b. If it reveals WS5's execution model is still
  meaningfully wrong (fills far off from predicted), that becomes the next
  fix, not a reason to stop paper trading.
- **Track B (carry + spot hedge) gets a CONTINUE only if**, once spot data and
  execution exist, the **hedged** combined P&L clears real two-legged costs
  with margin across multiple years — the same standard applied to the
  unhedged version, now applied to the real thesis. If it fails the same way
  the unhedged version did, **STOP** on carry specifically — two failed
  attempts (unhedged, then hedged) is sufficient evidence this isn't
  accessible from our seat, even though the underlying premium is real.
- **Project-level STOP was explicitly rejected** for now: Track A gives a real
  deployable path forward regardless of how Track B resolves, so "neither
  survives" is no longer the live risk it was when Section 10 was first
  written — the floor has already been raised by the decision to build
  Track A.

**The standing stop-rule, unchanged:** if a candidate cannot pass the "name
the counterparty" test AND clear real fee-inclusive costs with margin, it does
not get built — no matter how good the backtest looks. This rule is what
correctly blocked the unhedged carry trade from being deployed.

---

## 11. What does NOT change

The foundation stands and should be reused, not rebuilt: the research log (WS1),
versioned feature layer (WS2), purged/calendar-correct walk-forward validation
(WS3), point-in-time universe (WS4/4.5), shared execution model (WS5, pending
the fee fix), and the screening discipline. The infrastructure was never the
problem. The *question* was. This document changes the question.
