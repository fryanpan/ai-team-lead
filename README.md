# AI Team Lead (April 2026 Version)

This repo is how I run multi-agent Claude Code workflows that aren't yet supported natively — custom skills and automations, plus integrations with official Anthropic plugins and a few of my own.

Ultimately, for any workflow or functionality that's generally useful enough, I assume Anthropic will release something to cover the use case within 1-2 months after I try it out.  They're fast like that and their success depends on catering to the enterprise market.  

But until they do, this repo helps me test out improvements that Anthropic hasn't gotten around to yet.

# Warning: Orchestrating Agents Sucks Up Time

Do not bother running multiple agents in parallel if you don't have a good reason to!  Keep it simple. Only make things more complicated if the simpler solution(s) don't work for you.

I've scaled out to multiple agents only to tackle more projects in parallel and get more done; but I've tried to keep each project simple -- one long-running agent per project (with a repo attached that has specific skills and context).  One team lead agent across projects.  If I need to parallelize work within a project, I ask the agent on that project to spin up an [agent team](https://code.claude.com/docs/en/agent-teams) temporarily.

For all the benefits this workflow gives me, it has also regularly added 1-2 hours a week of agent orchestration and troubleshooting time to my plate.

Most of the instability comes from building on top of shaky foundations.  The workflow depends on multiple "research preview" features in Claude Code that aren't 100% solid.  Plus some of the custom plugins (especially mine!) are barely even half baked...

P.S. This setup has been way lower maintenance than running my own Claude Agent SDK team on Cloudflare driven via Slack...which is what I did for most of March 2026.  That was quite entertaining, and also a much, much bigger time sink...

## What I Want From My Workflow

I've been spending half of my weekdays and all of my weekends exploring Berlin, so I'm out and about a lot.  But I also want to be able to keep my half a dozen side projects each week humming even when I'm not attending to them.

This sounds a bit like OpenClaw, but I get the full power (and security layers) of Claude Code instead.

So this repo has evolved over the last few months to help cover all of these goals:

1. Work from anywhere (at home, on a laptop, or on my phone)
2. Have a quick way to give human guidance on decisions when I have a moment
3. Minimize the need to manually shuffle context Each agent responds to key events (Sentry, Github, Notion) related to their work immediatelyAgents can pass context with each other (and argue with each other) efficiently
4. Help the team introspect and improve regularly
5. Manage agent lifecycles from anywhere(start, stop, reconfigure & respawn, troubleshoot)

# How The Workflow Works

1. **Project Setup**For each new project I add into the team, the team lead has skills like `/new-project` or `/add-project` to register the project and propagate a standard setup of effective Claude practices This enables top plugins like [superpowers](https://github.com/obra/superpowers), special shipping skills that automate code reviews and deployments, and so on 
2. **Weekly Team Goals**Each week, I use the `/weekly-plan` skill to create top goals across the whole team. This includes capacity planning my hands-on time (15-20 hours each week). And also identifying which agents need to coordinate to tackle each goal. The plan goes into Notion and each agent that's involved has a chance to review and discuss. I can do this from anywhere using my [notion-channel-mcp](https://github.com/fryanpan/notion-channel-mcp) plugin that lets me work on the weekly plan in a live Notion doc together with my team lead
3. **Delegate Agent Management to Team Lead**I ask the team lead to kick off work, and it orchestrates agents against the plan The team lead uses [claude-hive](https://github.com/KevinLyxz/claude-hive-mcp) to send messages to other agents (and agents can also use this to communicate with each other)Note: if you don't need quite the power / customization of this workflow, [Claude Dispatch](https://support.claude.com/en/articles/13947068-assign-tasks-from-anywhere-in-claude-cowork) can already be your "team lead"
4. **Human Input When I'm Available**When I have time, I ask the team lead "what's next" and it uses the `/daily-review` skill to see where we are on the weekly goals and gives me an organized list of which items need my attention so I can run through this list quickly. To access Claude Code sessions running on my Mac Mini, I use [Claude Remote Control](https://code.claude.com/docs/en/remote-control)And I depend heavily on my [claude-live-feedback-plugin](https://github.com/fryanpan/claude-live-feedback-plugin) that lets me work live with Claude from anywhere on markdown docs, interactive mockups, and development servers by commenting on the doc or site (without having to jump back to Claude to figure out how to type a prompt)
5. **Agents Respond to Key Events Without My Help**Agents work with each other and respond to events in the world (e.g. Sentry, Github) This depends on my plugins [sentry-claude-channel](https://github.com/fryanpan/sentry-claude-channel) and [github-claude-channel](https://github.com/fryanpan/github-claude-channel)
6. **Retrospection and Self-Improvement**When the week's done, I run skills like `/aggregate` to analyze that week's Claude Code chat history (via JSONL transcripts) to understand how the work went and identify improvements

## What this is NOT

- **Production-grade** — it's my working setup, not a product. Things break and I fix them.
- **Universal** — heavily opinionated toward one human + many side projects + one Mac mini at home
- **A harness** — Claude Code itself is the harness, and I want to depend on the harness continuing to improve. This is config + glue + a few skills wrapped on top to make things work even better for me.

## Getting started

1. Clone the repo. `git clone git@github.com:your-username/ai-team-lead.git ~/dev/ai-team-lead`
2. Start Claude Code. `cd ~/dev/ai-team-lead``claude`
3. Ask Claude to run setup — it'll invoke the /setup skill to enable git hooks, seed registry.yaml, install dependencies, and walk you through optional Discord + Notion config: Please help me do setup

## Skills

Team-lead-only (live in `.claude/skills/`):

| Skill               | Purpose                                                      |
| ------------------- | ------------------------------------------------------------ |
| `/team-lead`        | Coordinate peer Claude Code sessions across managed products |
| `/spawn-session`    | Open a single new peer session in a detached tmux pane       |
| `/respawn-sessions` | Bring back all long-running sessions after a reboot          |
| `/weekly-plan`      | Set this week's goals in a Notion page the team-lead watches |
| `/daily-review`     | Intra-day prioritized status doc under live-feedback         |
| `/shutdown-session` | Cleanly stop one or more peer sessions                       |
| `/aggregate`        | Pull learnings from peer repos into the metaproject          |
|                     |                                                              |
| `/new-project`      | Scaffold a new project from scratch                          |
| `/add-project`      | Register an existing repo                                    |

Shared across the fleet (live in `plugin/team-lead-fleet/skills/`, symlinked into `.claude/skills/`):

| Skill             | When to use                                                  |
| ----------------- | ------------------------------------------------------------ |
| `/ship-auto`      | Personal / non-production repos. Full pipeline review → PR → CI → Copilot → merge → deploy with no mid-flow pauses. |
| `/ship-guarded`   | Tools you rely on in production. Same pipeline plus a risk-surface assessment before merge. |
| `/ship-push-only` | Advisory / team-owned repos. Push the branch and stop; humans own PR + merge + deploy. |
| `/retro`          | Transcript analysis and retrospectives.                      |
| `/persist-plan`   | Persist an internal plan to `docs/product/plans/`.           |
| `/ux-review`      | Walk a UI feature as a real user before shipping it.         |

## Privacy & leak prevention

Three layers protect against private content leaking into a public push:

1. **Private files are gitignored.** `registry.yaml`, `docs/process/retrospective.md`, `docs/process/aggregation-log.md`, `docs/process/propagation-log.md`, `.claude/discord/`, `.claude/reviews/`.
2. **Plugin wording is generic.** The `team-lead-fleet` plugin avoids specific project names; the registry drives ship-skill selection per project.
3. **Pre-push leak gate.** `.githooks/pre-push` runs `scripts/scrub-check.py` (regex layer) + optionally `scripts/scrub-haiku.py` (AI layer — Claude Haiku 4.5) on the diff being pushed. Blocks the push if either flags a project name or PII pattern. Bypass: `SCRUB_SKIP=1 git push ...` (rare).

See `CLAUDE.md` for fuller setup conventions.

# Related Work & Future Directions

- Sonjaya's [project-creator](https://github.com/Consortium-team/project-creator) was an inspiration to use a meta project to manage other projects.
- [Claude Design](https://www.anthropic.com/news/claude-design-anthropic-labs) (released Apr 2026) lets a team collaboratively work with Claude Opus to do design (e.g. create a UX workflow).  I've tested Design and it's a neat product -- but I feel like I get similar results working with Opus directly in repo with the live feedback plugin.  And then I can skip the step of transferring designs back to the code repo.  Plus I get the same power of Claude Design also for markdown docs and development servers, so I cover more steps in the dev lifecycle beyond design!
- [Claude Agent Teams](https://code.claude.com/docs/en/agent-teams) (released Feb 2026) also support many of the messaging and discussion patterns in my team.  But the agents in the team are typically optimized for working on one project.  And until recently, it wasn't possible to chat with the subagents directly in Claude Remote (but this was added in the last few weeks) 
- [Claude Managed Agents](https://platform.claude.com/docs/en/managed-agents/overview) (released Apr 2026) supports long-running (up to three hours currently) agents running Claude Code to do harder tasks.  The three hour limit is too short for my teams where each agent is often running for days, with restarts only when there's new plugins to integrate.
- [Claude Code Auto-Merge and Auto-Fix](https://claude.com/blog/preview-review-and-merge-with-claude-code) (released March 2026) supports automating parts of the PR and CI processes.  My `/ship-*` skills though can automate even more (on projects low risk enough to not require human review on every PR).

I fully expect Anthropic will improve all of these tools, and integrate them better, and make them easier to access remotely and with team collaboration.  Plus over time make all of these workflows more available to non-technical users.

## License

[MIT](LICENSE)
