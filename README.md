# TrustDeal

### The BASIX Deal Agent on Omega

> **Every Decision Has a Reason. Every Reason Has Proof.**

TrustDeal is an **Omega-based commerce agent** built for the SingularityNET × Omega × BASIX.Market hackathon challenge:

> **One Agent Producing an Auditable Decision**

TrustDeal handles discount requests and produces more than an answer. It produces a **decision, the rules behind it, the evidence used, and an audit trail that explains why the decision happened.**

---

## The Idea

When an AI agent makes a decision involving money, the decision alone is not enough.

A seller should be able to ask:

* Why was this discount approved?
* Which business rules were checked?
* Was the seller's tier verified?
* Did payment history affect the decision?
* Was the margin protected?
* Why was a request rejected or countered?
* What would need to change for the request to be approved?

TrustDeal is designed to answer those questions directly.

### The core principle

**The LLM does not decide the discount.**

The LLM handles natural-language interaction, while the actual business decision is made by **MeTTa rules running through Omega's PeTTa runtime**.

This separates language from business authority.

---

# What TrustDeal Does

A discount request can result in four decisions:

* **APPROVE** — the requested discount is allowed.
* **REJECT** — the request violates a hard business constraint.
* **COUNTER** — the requested discount is too high, but a lower discount is possible.
* **ESCALATE** — a human needs to make or approve the next decision.

Every decision includes:

* Final decision
* Allowed discount
* Confidence
* Rule-by-rule audit trail
* Evidence used
* Warnings and skipped checks
* Human override information when applicable

The original automated decision is never silently replaced by a human override.

---

# Why MeTTa + Omega?

TrustDeal separates the system into clear responsibilities:

```text
Customer / Seller
       │
       ▼
 Web / Telegram / Voice
       │
       ▼
 TrustDeal Agent
       │
       ├── Natural-language interaction
       │       └── ASI:One
       │
       ▼
 Omega Runtime
       │
       ▼
 PeTTa
       │
       ▼
 MeTTa Rules
       │
       ├── Policy
       ├── Business rules
       ├── Trust
       ├── What-if reasoning
       └── Agent state
       │
       ▼
 Decision + Audit Trail
       │
       ├── Web
       ├── Telegram
       └── Quote / Order / Invoice
```

### Responsibility boundary

**MeTTa decides.**

The business facts, rules and decision logic live in the `.metta` files.

**Omega executes.**

The decision runs inside Omega's PeTTa environment.

**Python coordinates.**

Python transports data between the application and the decision engine and stores results. It does not contain the business decision rules.

**ASI:One handles language.**

It can interpret customer messages, choose information tools and draft customer-facing responses.

**The LLM is not the source of truth for money decisions.**

---

# How a Decision Works

For every request, TrustDeal gathers the relevant business context:

* Seller tier
* Product category
* Cost and selling price
* Requested discount
* Quantity
* Payment history
* Order history
* Requests made this month
* Competitor evidence when available

MeTTa then evaluates the request against the business policy.

The decision follows the configured rule system, including:

### R1 — Margin protection

The decision must respect the configured margin floor.

### R2 — Seller tier limits

Different seller tiers have different discount limits.

### R3 — Payment history

Eligible payment history can provide an additional discount bonus.

### R4 — Volume

Large quantities can qualify for an additional bonus.

### R5 — Competitor evidence

Competitor pricing can affect the decision only when the evidence is verified.

### R6 — Escalation

Requests above the configured escalation threshold are sent for human review.

### R7 — Repeat requests

Repeated requests can reduce confidence and generate a warning.

Additional checks handle:

* Tier conflicts
* Seller trust
* Allowed maximum discount
* Final decision selection

---

# Example

A seller requests:

```text
60 units
20% discount
Seller claims: Gold
Recorded tier: Silver
```

TrustDeal detects the tier conflict.

Instead of simply trusting the message, it uses the **recorded seller information** and evaluates the request using the actual policy.

The result can be:

```text
COUNTER
12% discount
```

The audit trail explains why.

This is the important difference:

> **TrustDeal does not just tell you what happened. It shows why it happened.**

---

# Auditable Decision

The audit trail records the individual checks used to reach the result.

Each rule can be shown as:

```text
PASS
FAIL
WARNING
SKIPPED
```

with:

* Rule ID
* Input value
* Result
* Reason

Example rule IDs include:

```text
R1–R7
CONFLICT
TRUST
ALLOWED
DECISION
```

This makes the final decision traceable instead of treating the AI output as a black box.

---

# Three Differentiators

## D1 — "What Would It Take?"

When a request is rejected, countered or escalated, TrustDeal can ask:

> **What would need to change for this deal to be approved?**

MeTTa re-evaluates the decision with controlled changes such as:

* Verify the seller tier
* Verify competitor evidence
* Increase quantity
* Lower the requested discount

The resulting options are checked by the same decision rules.

TrustDeal does not suggest an option that breaks the configured margin floor.

---

## D2 — Learning From Human Overrides

TrustDeal records human overrides.

When repeated overrides reveal a consistent pattern, MeTTa can propose a policy change.

For example:

```text
Current:
Silver tier cap = 10%

Proposed:
Silver tier cap = 12%
```

The proposal includes the override evidence behind it.

### Important safety boundary

The policy does **not** change automatically.

A human must approve the proposal.

The margin floor cannot be changed through this learning mechanism.

Approved policy changes are logged and the previous policy is archived.

---

## D3 — Evidence-Based Seller Trust

TrustDeal maintains a seller trust value based on payment outcomes.

The system considers:

* On-time payments
* Total orders
* Amount of available evidence

A seller with no history is treated as:

> **Unknown — not automatically trusted or distrusted.**

Limited evidence lowers confidence rather than inventing certainty.

Recorded payment outcomes update the seller's trust value over time.

---

# From Decision to Commerce

TrustDeal connects the decision to an actual commerce flow.

```text
Customer request
      ↓
Agent interaction
      ↓
MeTTa decision
      ↓
Audit trail
      ↓
Quote
      ↓
Customer approval
      ↓
Order
      ↓
GST invoice
```

The customer-facing side is separated from the seller's internal decision information.

Customers do not see:

* Costs
* Margins
* Internal rule IDs
* Confidence
* Seller trust information
* Internal audit trail
* Internal tool logs

They receive only customer-safe information.

---

# Customer Experience

A customer can open the Customer page and interact naturally with the agent.

For example:

> "Can I get 20% off this phone?"

The agent can:

1. Understand the request.
2. Identify the product and requested discount.
3. Call the appropriate information tools.
4. Ask MeTTa to make the business decision.
5. Return the approved offer, counter-offer, rejection or escalation.
6. Continue the conversation until the deal is closed or waiting for human input.

The customer can also receive alternatives from the store catalog.

Every numerical value shown to the customer comes from MeTTa or the catalog.

---

# Quote, Order and GST Invoice

Once a deal is approved, TrustDeal can generate a customer-facing quote.

The quote contains information such as:

* Customer
* Product
* Quantity
* List price
* Discount
* Price
* Total
* Savings
* Quote ID
* Validity
* Issue date

After the customer places the order, TrustDeal can generate a linked **GST tax invoice** containing the invoice, order and quote references, product information and GST breakdown.

This connects the auditable decision to a real commerce outcome.

---

# Telegram

Telegram is another interface to the same TrustDeal agent.

It is not a separate decision system.

```text
Web
  │
  ├── same agent
  │
Telegram
  │
  └── same MeTTa decision
```

Customer Telegram interactions can:

* Discover products
* Request discounts
* Receive offers
* Accept offers
* Receive quotes
* Place orders

Seller Telegram alerts can notify the seller about:

* Escalations
* Verification tasks

Seller actions taken through Telegram use the same decision endpoints as the web application.

Telegram activity is recorded in the audit/activity history.

---

# Voice

TrustDeal also supports natural interaction through voice using the browser's speech capabilities.

Voice is another way to interact with the same application — it does not create a separate decision engine.

The business decision still comes from MeTTa running through the configured Omega runtime.

---

# Omega Integration

TrustDeal can execute its decision engine inside Omega's PeTTa runtime.

The integration uses:

```text
FastAPI
   ↓
OmegaRunner
   ↓
WebSocket /omega/engine
   ↓
TrustDeal Omega plugin
   ↓
PeTTa
   ↓
MeTTa
```

The Omega plugin loads the same TrustDeal rule files used by the application.

### Rule integrity

TrustDeal computes a SHA-256 fingerprint of the loaded rule files.

The backend verifies the fingerprint before relying on the result.

If the rules do not match, TrustDeal refuses to continue rather than silently using a different rule set.

### No silent fallback

When Omega is required, failures are explicit.

For example:

* Omega unavailable → clear error
* Rule mismatch → refused
* Timeout → refused
* Omega evaluation error → error returned

TrustDeal does not silently fall back to another decision engine.

### Hot reload

Approved policy changes can be reloaded into the Omega agent without restarting the entire system.

Changes to the core `rules.metta` logic still require an Omega restart.

---

# Omega Verification

TrustDeal was tested against the full OmegaClaw agent environment.

The verification included:

* Deal evaluations
* What-if evaluations
* Category profiles
* Trust operations
* Policy-learning operations

Across three consecutive runs:

> **111 / 111 results matched the local reference engine.**

The comparison checked both exact output text and parsed values.

Measured API latency through Omega was approximately:

> **32–86 ms per evaluation, with a 50 ms median in the measured run.**

The internal PeTTa round trip was measured separately.

---

# Security

TrustDeal treats business decisions and credentials as separate concerns.

Important protections include:

* Secrets are stored outside the repository.
* Sensitive environment files are git-ignored.
* Telegram tokens are not written to logs.
* Customer APIs expose only allowlisted customer-safe data.
* MeTTa input is validated.
* WebSocket/plugin authentication is checked.
* Rule fingerprints are verified.
* Omega failures do not silently fall back.
* Security tests cover invalid input, injection attempts, CORS, hosts, headers, WebSocket/plugin checks and log redaction.

See [`SECURITY.md`](SECURITY.md) for the security details.

---

# Testing

TrustDeal has a comprehensive automated test suite.

Current test coverage includes:

* Core MeTTa rules
* Six representative deal scenarios
* D1 What-if
* D2 Policy learning
* D3 Seller trust
* API routes
* Customer agent
* Agent tools
* Guardrails
* Fallback behaviour
* Telegram
* Quote PDF
* CSV import
* OmegaRunner
* Omega failure conditions
* Omega hot reload
* Rule fingerprint verification
* Security checks

### Test result

```text
276 tests passed
2 optional Omega parity tests
```

The Omega parity verification additionally achieved:

```text
111 / 111 identical
```

---

# Six Representative Deals

| Case | Scenario                    | Result                        |
| ---- | --------------------------- | ----------------------------- |
| 1    | Gold seller, valid discount | APPROVE                       |
| 2    | Discount below product cost | REJECT                        |
| 3    | Tier conflict               | COUNTER                       |
| 4    | Missing payment history     | APPROVE with lower confidence |
| 5    | Unverified competitor quote | COUNTER                       |
| 6    | Repeated requests           | APPROVE with warning          |

These cases demonstrate that TrustDeal is not only testing the happy path.

---

# Project Structure

```text
TrustDeal/
│
├── engine/
│   ├── policy.metta
│   ├── rules.metta
│   ├── learning.metta
│   ├── agent.metta
│   ├── bridge.py
│   ├── metta_safe.py
│   └── omega_link.py
│
├── omega/
│   ├── TrustDeal Omega plugin
│   ├── plugins.yaml
│   ├── start/stop scripts
│   └── omega.env.example
│
├── backend/
│   ├── FastAPI application
│   ├── agent/
│   ├── telegram/
│   ├── services/
│   └── SQLite models/routes
│
├── frontend/
│   └── React + Vite application
│
├── data/
│   └── demo/seed data
│
├── samples/
│   ├── audit trail
│   ├── agent transcript
│   ├── Omega parity report
│   └── screenshots
│
└── tests/
    └── automated test suite
```

---

# Quick Start

### Requirements

* Python 3.12
* Node.js / npm
* Windows environment for the provided `.bat` launchers
* Omega environment when running Omega mode

### First-time Python setup

```bat
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

### Start TrustDeal

```bat
start.bat
```

This starts:

```text
Backend  → http://localhost:8000
Frontend → http://localhost:5173
```

### Start with fresh demo data

```bat
start.bat reset
```

### Stop

```bat
stop.bat
```

---

# Running Omega

### Omega harness

```bat
omega\start-omega.bat
```

### Full OmegaClaw agent

```bat
omega\start-omega.bat agent
```

### Run the application using Omega

```bat
start.bat omega
```

### Stop Omega

```bat
omega\stop-omega.bat
```

---

# AI Disclosure

Transparency is important to TrustDeal.

The AI systems used during development and runtime have different responsibilities.

| Tool / Service                      | Used for                                                                               | Not used for                                                               |
| ----------------------------------- | -------------------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| **Claude Code**                     | Development assistance, implementation support, debugging, tests, UI and documentation | Not part of the running TrustDeal decision engine                          |
| **ASI:One `asi1`**                  | Customer-message understanding, information-tool selection and response drafting       | Not used to determine discounts or business decision numbers               |
| **ASI:One `asi1` inside OmegaClaw** | OmegaClaw agent interaction and chat loop                                              | Not used to make TrustDeal's discount decisions                            |
| **Tavily**                          | Seller-side market-price lookup with source URLs                                       | Not used as the decision authority; results are not automatically verified |

### Decision authority

The business decision comes from the MeTTa rules.

The running architecture is:

```text
Language understanding
        ↓
ASI:One
        ↓
TrustDeal
        ↓
MeTTa rules
        ↓
Omega / PeTTa
        ↓
Decision + Audit Trail
```

The LLM does not get to choose the final discount.

### My role

I designed the business rules, decision order and differentiating mechanisms, and reviewed the resulting logic.

---

# Integrations

### Omega / PeTTa

Decision runtime for executing the MeTTa logic.

### ASI:One

Natural-language agent interaction.

### Tavily

External market-price lookup for seller-side evidence.

### Telegram Bot API

Customer communication and seller alerts.

### SQLite

Local persistence for deals, decisions, activity and related state.

### React + Vite

Web interface.

### FastAPI

Application/backend API.

---

# What Existed Before the Hackathon?

**Nothing.**

TrustDeal was built from scratch during the hackathon.

---

# What Comes Next?

Potential future improvements include:

* More advanced PLN reasoning over seller trust values
* More override patterns for policy learning
* Free-text requests on the seller Deal Check page
* Real customer and staff authentication
* Quote expiry handling
* Direct marketplace integration with BASIX.Market sellers, products and orders
* Read-only natural-language explanations of historical decisions through the agent

---

# The Goal

TrustDeal is built around a simple idea:

> **An AI system making a money-related decision should not ask you to trust the answer blindly.**

It should be able to show:

**The decision.**

**The rules.**

**The evidence.**

**The reasoning trail.**

**And the human actions taken afterward.**

That is TrustDeal.

## Every Decision Has a Reason. Every Reason Has Proof.
