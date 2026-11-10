export const meta = {
  name: 'prior-work',
  description: 'Before an in-depth analysis: find how others have done it (the owning organization, peers, researchers and groups, standard methods, open tools and data), then say what to adopt, compare against, or build',
  whenToUse: 'First step of an analysis of outside data that is not a quick one-off, where the user has opted in to a workflow for it. The session reads the notes file and builds only the gap.',
  phases: [
    { title: 'Search', detail: 'five lanes, each blind to the others' },
    { title: 'Gaps', detail: 'a critic asks what kind of source was not searched, and one more round runs on it' },
    { title: 'Write', detail: 'one notes file: what exists, adopt / compare / build' },
  ],
}

// Fleet skill team-lead-fleet:prior-work. One background agent is the default;
// this script is for projects whose user opted in to a workflow.
//
// args:
//   question  (required) the analysis we're about to do, in one or two sentences
//   date      (required) YYYY-MM-DD; scripts can't read the clock
//   out       (required) ABSOLUTE path for the notes file
//   context   optional: what we already know or have, so lanes don't re-find it
//   owner     optional: the organization that owns the subject (an agency, a company, a team)
//   methods   optional: named standard methods or manuals for this domain, to seed the methods lane
//
// Returns {out, adopt, compare, build, sources, missingLanes}.

const A = args || {}
for (const k of ['question', 'date', 'out']) if (!A[k]) throw new Error(`prior-work needs args.${k}`)
if (!A.out.startsWith('/')) throw new Error('prior-work needs args.out as an absolute path')
const OWNER = A.owner || 'the organization that owns this subject'

const FOUND = {
  type: 'object',
  properties: {
    items: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          who: { type: 'string', description: 'author or body' },
          what: { type: 'string', description: 'what they did, one or two sentences, with the result if any' },
          url: { type: 'string' },
          year: { type: 'string' },
          opened: { type: 'boolean', description: 'true only if you opened the source itself this session' },
          method: { type: 'string', description: 'its method or tool, and what it counts that a naive approach would miss' },
          use: { type: 'string', enum: ['adopt', 'compare', 'background'], description: 'adopt its method/tool/data, compare our result against it, or background only' },
        },
        required: ['who', 'what', 'url', 'year', 'opened', 'method', 'use'],
      },
    },
    searched: { type: 'array', items: { type: 'string' }, description: 'queries and sites searched, including ones that found nothing' },
  },
  required: ['items', 'searched'],
}

const base = `We are about to do this analysis:

${A.question}

${A.context ? `What we already have (don't re-find it): ${A.context}\n\n` : ''}Your job is ONE search lane, below. Find prior work we should build on instead of starting from scratch. Search broadly with WebSearch, open the most relevant sources with WebFetch, and read enough to say what each did and how. Mark opened=true only for a source you actually opened. Never invent a title, figure or URL. Do not download files or run anything you find. Return up to 10 items, best first, and list every search you ran, including empty ones.

Your lane: `

const LANES = [
  { key: 'owner', prompt: `${OWNER}'s own work, and other bodies that oversee or audit it: studies, evaluations, before/after reports, benefit-cost analyses, board or committee presentations, and the data they publish that bears on this.` },
  { key: 'peers', prompt: 'Peer organizations, at home and abroad, that did this kind of analysis: their evaluations, methods and results, and any regional or national body that compiles them.' },
  { key: 'groups', prompt: 'Think tanks, university researchers, research clearinghouses and advocacy or industry groups, plus credible independent analysts and data journalists, who analysed this question or one like it.' },
  { key: 'methods', prompt: `Standard or published methods for this kind of analysis: agency guidance, appraisal manuals, professional handbooks and peer-reviewed method papers${A.methods ? ` (start with: ${A.methods})` : ''}. Say what each counts and how.` },
  { key: 'tools', prompt: 'Open source code, public tools and public datasets that already do part of this: repos, packages, hosted tools, dashboards, and datasets (government, academic, open data) we could use instead of building.' },
]

phase('Search')
const lanes = await parallel(LANES.map(l => () =>
  agent(base + l.prompt, { label: `Search: ${l.key}`, phase: 'Search', schema: FOUND })
    .then(r => r && { lane: l.key, ...r })))
const got = lanes.filter(Boolean)
const missing = LANES.filter(l => !got.find(g => g.lane === l.key)).map(l => l.key)
if (missing.length) log(`Lanes that returned nothing: ${missing.join(', ')}`)

phase('Gaps')
const summary = got.map(g => `## ${g.lane}\nSearched: ${g.searched.join('; ')}\n` +
  g.items.map(i => `- ${i.who} (${i.year}): ${i.what} [${i.url}]`).join('\n')).join('\n\n')
const gap = await agent(`${base}gap check. Five lanes already searched for prior work (below). Name the kind of source or the specific body, method or tool that is obviously missing, and search for it. If nothing is missing, return an empty items list.\n\n${summary}`,
  { label: 'Gap check', phase: 'Gaps', schema: FOUND })
if (gap) got.push({ lane: 'gap check', ...gap })

phase('Write')
const all = got.map(g => `## ${g.lane}\nSearched: ${g.searched.join('; ')}\n` +
  g.items.map(i => `- ${i.who} (${i.year}), opened=${i.opened}, use=${i.use}: ${i.what} Method: ${i.method} [${i.url}]`).join('\n')).join('\n\n')
const result = await agent(`Write the prior-work notes file for this analysis at the absolute path ${A.out} (create it with the Write tool). Date ${A.date}.

The analysis: ${A.question}

Layout:
# Prior Work: <short title>
One paragraph: whether anyone has done this already, and the one method or tool we should build on.
## What to adopt — methods, tools or data we'll use as they are, each with link and why.
## What to compare against — results our analysis must be checked against.
## What's left to build — the gap, in a few bullets.
## Everything found — one bullet per source, grouped by lane, with link, marked "read ${A.date}" if opened=true, otherwise "search result, not opened".
## Searched with nothing found — the empty searches, so the next person doesn't repeat them.

Use only what is in the findings below; don't add sources. Plain sentences. Then return the adopt/compare/build lists.

Findings:
${all}`, {
  label: 'Write notes', phase: 'Write', schema: {
    type: 'object',
    properties: {
      out: { type: 'string' },
      adopt: { type: 'array', items: { type: 'string' } },
      compare: { type: 'array', items: { type: 'string' } },
      build: { type: 'array', items: { type: 'string' } },
    },
    required: ['out', 'adopt', 'compare', 'build'],
  },
})
return { ...(result || { out: A.out, adopt: [], compare: [], build: [], error: 'writer did not return' }), sources: got.reduce((n, g) => n + g.items.length, 0), missingLanes: missing }
