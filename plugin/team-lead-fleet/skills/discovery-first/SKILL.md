---
name: discovery-first
description: Use when planning, proposing or scoping a product improvement, a new feature or a new idea, including a request that already names its solution ("add X to the Y page") and a conversation that drifts into "let's build X". Also use for policy, advocacy or campaign ideas.
user-invocable: true
---

# Discovery First

**No solution, plan or design until the Discovery section is written.** A request that names its solution is still a product improvement. "Add a dark mode toggle" tells you what someone asked for, not who needs it or why.

## Pick the mode

- **Civic mode** if the project's `CLAUDE.md` has this line: `Discovery mode: civic`. Use it for policy, advocacy and campaign projects.
- **Product mode** otherwise.

## Product mode questions

1. **User:** who is the user?
2. **Workflow and pain:** what is their current workflow, and where does it hurt?
3. **Today:** what do they do today instead?
4. **Success:** what does success look like, and how is it measured?
5. **Alternatives:** what else could solve it, including doing nothing?

## Civic mode questions

1. **Endpoint:** what change in the world is the goal?
2. **Theory of change:** how does that change happen?
3. **Levers:** which levers does this push on? A lever is a decision-maker, a rule, a budget, a public opinion or a deadline.
4. **Pushing them:** how do those levers get pushed successfully? Name who has done it before and what worked.
5. **Simplest novel thing:** working back from the levers, what is the smallest new thing that does the job?

## How to run it

- **Draft the answers yourself first** from the project's docs: README, goals, research, support data, past decisions. Mark each answer as **from docs** (name the doc) or **inferred**.
- **Ask the person only for the gaps, in one batch.** Never one question at a time.
- **Write the strongest case against the proposal** before you agree to it. Use the evidence you found, such as low demand, no link to current goals, or a cheaper alternative. Make it a real argument, not a footnote.
- **Put it in the planning doc** as a short `## Discovery` section above any design: the five answers, the case against, and the open questions.
- **Then design**, or recommend a different path if discovery points to one.

## Rationalizations

| Thought | Reality |
|---|---|
| "The problem is clear, so fast-track it" | The fast-track applies to the design. It does not skip discovery. |
| "They asked for it explicitly" | A request names a solution. Discovery checks the problem behind it. |
| "It's small and reversible" | Small work on the wrong problem is still the wrong work, and it delays the right work. |
| "I flagged the priority concern, then started" | A one-line flag after a full plan is not discovery. Discovery comes before the plan. |
| "Asking questions is a pause" | Answers you can draft from the docs need no question. Only the gaps go to the person, once. |

## Red flags

- A plan, file list or time estimate appears before a Discovery section.
- "I'm starting on it now unless you tell me to stop" on an improvement nobody has examined.
- The case against is one sentence at the end of the reply.
