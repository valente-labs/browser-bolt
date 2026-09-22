# Security and data boundary

This is an experimental browser controller, not a credential manager. Evaluate it on synthetic pages and accounts whose data may be sent to the configured providers.

## Data flow

The controller sends goals, observed page text, element labels and values, and recent action history to the configured decision or text provider. Provider API keys stay in the server-side process and are used in provider authorization headers. This does not make the page content private: observed content and generated text can also appear in local inspector state, recordings, and diagnostic artifacts.

The inspector binds to `127.0.0.1`. Keep it local. Browser Harness connects to Chrome, and owned tabs share that Chrome profile. A connected authenticated profile therefore grants access beyond the synthetic fixtures.

There is no general PII redaction, credential broker, or local secret-reference boundary. Do not place passwords, confidential account data, or other sensitive material in goals or test pages. `.env` and `artifacts/` are ignored by git; files elsewhere require their own review before sharing.

## Execution boundary

Models choose from observed operation and action identifiers. The browser executor owns its selectors, code, target validation, and freshness checks. Model-generated selectors or executable code are not accepted. These checks reduce accidental execution against stale or incompatible elements; they are not a complete defense against malicious page content or harmful choices among valid actions.

Browser mutations are not automatically retried. Verify outcomes independently, especially after a navigation interruption or `DONE` decision. Do not use unattended runs for purchases, external messages, account changes, or other consequential actions.

## Reporting a problem

For ordinary bugs, use the repository issue template with a minimal synthetic reproduction and the relevant version or commit. For a security-sensitive issue, use GitHub's private vulnerability reporting option on the repository Security page when available. If that option is absent, open an issue requesting a private contact method without describing the vulnerability. Do not publish exploit details while arranging private contact.

Omit API keys, personal page content, screenshots of private accounts, and raw environment files from reports. There is no guaranteed response time or paid incident-response service. The release checklist requires checking the private reporting option before announcing the public fork.

## MCP decision service

The optional stdio MCP service proposes typed actions and field text. It does not attach to Chrome, click, type, read a vault, or authorize a transaction. The calling host must supply an appropriate observed state, check its freshness, authorize any action, and independently verify completion. A returned `DONE` operation is a model judgment, not a verified outcome.

Only fixed provider routes and dedicated environment keys are accepted. Input limits and a bounded connection pool limit individual calls; they do not constitute a tenant quota or billing system. Page content still goes to inference providers. This is a BYOK preview for approved non-sensitive inputs, not a production credential-autofill product.

The local Agent executor has cooperative deadlines, cancellation, and tick budgets. An operation already in synchronous I/O can finish; stopping prevents the next operation. These safeguards do not add origin-scoped authorization or make arbitrary untrusted websites safe.
