---
alwaysApply: true
gate: never
---

# Public Content Scrubbing

For any agent drafting external content — blog posts, READMEs, public docs, personal-site pages, marketing copy, or a message to someone outside the user's immediate team — or updating an already-public repo.

## Core rule

**A public GitHub repo is fine to name. A private one always needs a review pass before it is mentioned.** When in doubt, anonymize or omit.

- **Public repo + public artifact**: name freely — GitHub URL, version numbers, commit hashes.
- **Private repo or artifact**: describe it generically ("a personal CRM tool I built"). Never name it, and never let the description enable enumeration of related private work.
- **A private repo about to flip public**: treat as private until the flip actually lands. Schedule the mention for after, not in anticipation.
- **Other people's names**: only with their explicit consent for that specific artifact. Consent for one venue does not carry to another.

## The review pass

Six surfaces leak, and the last four are the ones that get missed because they look internal: **commit history** (`git log --all --full-history -- <path>` — a deleted file is still in the clone), **PR descriptions and comments** (visible after merge, and unremovable), **commit messages**, **code comments**, **test fixtures and seed data**, and **CI config and scripts** (deploy targets, internal dashboards).

## Flipping a repo public

**Agents do not flip repos public** — the flip itself is manual, in the GitHub UI, after the user greenlights the redacted state. Use the `flip-public` skill for the procedure; it carries the full checklist.

## Already-public content that should not be

Surface it to the user immediately — never silently rewrite history. Current files get a PR. Anything in commit history, PR descriptions or closed-but-public discussions is the user's call: rewrite and force-push, edit the offending body, or accept the exposure.

## Escalate immediately

- A draft about to publish that names a private repo or peer
- A request to flip a repo public without a review pass
- Cross-repo leakage found in an already-public artifact
