export const meta = {
  name: 'pre-send',
  description: 'Gate before figures leave the session: evidence and pipeline review, a blind skeptic, then close every unverified item',
  whenToUse: 'Before a doc, page, comment or post with figures goes to Bryan for sign-off or to anyone outside the session, where the user has opted in to a workflow for it. Started by the team-lead-fleet:pre-send skill, which files the sign-off item afterwards.',
  phases: [
    { title: 'Review', detail: 'evidence fit, numbers agreeing and the data pipeline, re-derived from source' },
    { title: 'Skeptic', detail: 'a fresh agent with only what the reader gets, plus the model and its data' },
    { title: 'Verify', detail: 'one agent per unverified item, looping until verified or shown impossible' },
  ],
}

// Fleet skill team-lead-fleet:pre-send. The skill holds the why; this script runs its three stages.
//
// args:
//   target   (required) repo path or URL of what the reader gets: the doc, page or comment
//   readers  (required) who reads it, e.g. ["Bryan", "a non-technical reader", "agency staff"]
//   model    paths to the model code that produces the figures (scripts, site/src modules)
//   data     paths to the data the model reads (inputs, caches, assumption and study files)
//   send     where it goes next, e.g. "a public comment to a city committee" (shapes the skeptic's reading)
//   notes    anything the reviewer must know; never shown to the skeptic
//   checks   project commands the reviewer runs and reports (assumption checks, citation audits), e.g. ["npm run check in site/"]
//   evidence path of the project's study log, if any
//   decisions path of the project's decision log, if any
//   blind    paths the skeptic must not read (notes, scratch, review docs); git history, PR threads and board comments are always off limits
//   reviewModel  optional model for the review and skeptic agents; omitted, they inherit the session's
//   maxConcurrent  cap on verify agents at once (default 3)
//
// Returns { verdict, review, skeptic, verified } for the pre-send skill to act on.

const A = typeof args === 'string' ? { target: args } : (args || {})
if (!A.target) throw new Error('pre-send needs args.target: the doc or page the reader gets')
const readers = (A.readers && A.readers.length) ? A.readers : ['Bryan', 'a non-technical reader']
const modelPaths = A.model || []
const dataPaths = A.data || []
const checks = A.checks || []
const blind = A.blind || []
const list = xs => xs.length ? xs.map(x => `- ${x}`).join('\n') : '- (none given; find them from the target if it has figures, and say which you used)'

const MAX_VERIFY_ROUNDS = 3

const FINDING = {
  type: 'object',
  properties: {
    id: { type: 'string', description: 'short stable id, e.g. R1' },
    severity: { type: 'string', enum: ['blocks-send', 'should-fix', 'minor'] },
    where: { type: 'string', description: 'file and line, section, or figure' },
    problem: { type: 'string' },
    evidence: { type: 'string', description: 'what you opened or ran that shows it' },
    fix: { type: 'string' },
  },
  required: ['id', 'severity', 'where', 'problem', 'evidence', 'fix'],
}
const UNVERIFIED = {
  type: 'object',
  properties: {
    item: { type: 'string', description: 'the claim or check that could not be run' },
    why: { type: 'string', description: 'what stopped it' },
    how: { type: 'string', description: 'what would settle it: a query, a document, a rerun' },
  },
  required: ['item', 'why', 'how'],
}

const REVIEW_SCHEMA = {
  type: 'object',
  properties: {
    checks_run: { type: 'array', items: { type: 'string' }, description: 'each check run, with its result' },
    findings: { type: 'array', items: FINDING },
    unverified: { type: 'array', items: UNVERIFIED },
  },
  required: ['checks_run', 'findings', 'unverified'],
}

const SKEPTIC_SCHEMA = {
  type: 'object',
  properties: {
    headline: { type: 'string', description: 'the headline claim as a reader takes it away' },
    gaps: {
      type: 'array',
      items: {
        type: 'object',
        properties: {
          kind: {
            type: 'string',
            enum: ['unanswered-question', 'unsourced-claim', 'unstated-assumption', 'missing-comparison', 'figure-mismatch'],
          },
          severity: { type: 'string', enum: ['blocks-send', 'should-fix', 'minor'] },
          where: { type: 'string' },
          gap: { type: 'string', description: 'the gap in the reader\'s words where possible' },
          evidence: { type: 'string' },
          fix: { type: 'string' },
        },
        required: ['kind', 'severity', 'where', 'gap', 'evidence', 'fix'],
      },
    },
    sensitivity: {
      type: 'object',
      description: 'the rerun of the input that moves the headline most, at the edge of its evidence; null fields when there is no model',
      properties: {
        input: { type: 'string' },
        value_used: { type: 'string' },
        evidence_edge: { type: 'string', description: 'the value at the weak edge of its evidence, and the source for it' },
        headline_now: { type: 'string' },
        headline_at_edge: { type: 'string' },
        how_rerun: { type: 'string', description: 'the command or edit used, so it can be repeated' },
        survives: { type: 'boolean', description: 'does the headline as written still hold at the edge' },
      },
      required: ['input', 'value_used', 'evidence_edge', 'headline_now', 'headline_at_edge', 'how_rerun', 'survives'],
    },
    unverified: { type: 'array', items: UNVERIFIED },
  },
  required: ['headline', 'gaps', 'sensitivity', 'unverified'],
}

const VERIFY_SCHEMA = {
  type: 'object',
  properties: {
    status: {
      type: 'string',
      enum: ['verified', 'wrong', 'impossible', 'still-open'],
      description: 'verified: it holds; wrong: it fails and the doc must change; impossible: no public source or run can settle it; still-open: more work would settle it',
    },
    result: { type: 'string', description: 'what you found, with the figure or quote' },
    evidence: { type: 'string', description: 'the source opened or command run, so Bryan can rerun it' },
    tried: { type: 'string', description: 'everything attempted, so the next round does not repeat it' },
    fix: { type: 'string', description: 'the change to the doc when status is wrong; empty otherwise' },
  },
  required: ['status', 'result', 'evidence', 'tried', 'fix'],
}

// The model is the caller's choice. If a named model returns nothing, retry once on the session's model and say so.
async function reviewer(prompt, opts) {
  if (!A.reviewModel) return agent(prompt, opts)
  const r = await agent(prompt, { ...opts, model: A.reviewModel })
  if (r) return r
  log(`${opts.label}: ${A.reviewModel} unavailable, retrying on the session's model`)
  return agent(prompt, { ...opts, label: `${opts.label} (fallback)` })
}

// ---------------------------------------------------------------------------
phase('Review')

const review = await reviewer(`You are the pre-send reviewer for ${A.target}. Nothing in it has left the session yet. It goes next to: ${A.send || 'Bryan for sign-off, then outside'}.
Its readers: ${readers.join('; ')}.

Model code:
${list(modelPaths)}
Data it reads:
${list(dataPaths)}
${A.notes ? `\nFrom the builder:\n${A.notes}\n` : ''}
Do these in order and record each in checks_run with its result. Re-derive figures yourself. A passing check from the builder is not evidence.

1. Publisher's own definitions. For every figure about an organization, open that organization's own source (its plan, data dictionary, published total, or the law) and check the doc measures it the way the organization does: the same distance, unit, area, period and counting rule.
2. Evidence. Every study a figure leans on${A.evidence ? ` needs a row in ${A.evidence} (a missing row is a finding)` : ': with no study log, list each one you find with its design'}. For each: does it transfer? Check GRADE's four kinds of indirectness (population, intervention, comparison, outcome), and that the size of change applied matches the study's own. Print the applied effect at one real case beside the study's effect, in the study's own units. An applied effect larger than the study's is a finding unless the doc states why. Its cost must come from the same program as its effect. A local series with a wide interval or one spanning zero must not set an effect size, and no figure may use one end of an interval as a point estimate.
3. Assumptions. List each input and causal link the headline needs and whether it has a source, a low and a high, and a data test. Flag every input carrying more than a third of a headline.
4. Data pipeline. Trace every transformation from source to figure. Open the source data and check the fields, row counts, date ranges and nulls the script relies on. Look for joins that drop or duplicate rows, units that change mid-pipeline, filters that remove the interesting cases, and seats or eligibility counted as people who changed behavior.
5. Numbers agree. Put each number beside the published or earlier figure for the same quantity (in the doc it supports, a prior report, a benchmark), in absolute values and not only ratios, with units and conditions matched. A gap over about 1.5x that the doc does not explain is a finding.
6. One figure, one source. Each headline number must come from one place. If two scripts or two pages compute it, report both values.
7. Citations. Every cite is an inline link, and every quotation and every attribution ("he said", "per the spec") came from a document you opened.
${A.decisions ? `8. Settled decisions. Check the doc does not reopen a ruling in ${A.decisions}.\n` : ''}${checks.length ? `Also run each of these project checks and report its result:\n${list(checks)}\n` : ''}
Report each defect as a finding. blocks-send means a reader would be misled about the headline or a figure. Report every check you could not run as unverified, with what would settle it. Do not edit files.`,
  { label: 'Review', phase: 'Review', schema: REVIEW_SCHEMA, effort: 'high' })

log(`Review: ${review ? review.findings.length : 0} findings, ${review ? review.unverified.length : 0} unverified`)

// ---------------------------------------------------------------------------
phase('Skeptic')

// Deliberately gets none of the review above, and none of the build history.
const skeptic = await reviewer(`You are an informed critic reading ${A.target} the day after it was sent. You have never seen how it was built.
Its readers: ${readers.join('; ')}. It goes to: ${A.send || 'readers outside this project'}.

You get only what the reader gets, plus the model and its data:
Model code:
${list(modelPaths)}
Data:
${list(dataPaths)}

Do not read ${blind.length ? blind.join(', ') + ', ' : ''}git history, PR threads, board comments or any review or notes about this work. They tell you what the builder thought, and you are here to find what the builder missed.

1. Write the headline as a reader takes it away.
2. Find the gaps a sharp reader would hit: a question a reader would ask that the doc doesn't answer; a claim with no source; an assumption the conclusion leans on that's never stated or tested; a comparison it leaves out (the baseline, the publisher's own figure, the cheaper option, the do-nothing case); and a figure that doesn't match its source. Open the source to check that last one.
   For each stated conclusion also ask: is the data complete for every group compared, and is each denominator from the same years and area? Would another reasonable method choice (dedup rule, window, threshold, grouping) flip or halve it? Does an outside figure for the same thing land within about 3x of ours? Is a causal word ("because", "explains", "worked") backed by more than a correlation?
3. Where there's a model, find the input that moves the headline most. Find the weak edge of its evidence: the low end of the studies it cites, the measured rate where the doc used a guess, or the agency's definition where the doc used its own. Rerun the model at that value and report the headline then. Use a copy or a command-line override, and do not change tracked files. If there's no model, set every sensitivity field to "none" and survives to true.
4. List anything you could not check as unverified, with what would settle it.

blocks-send means the headline as written would not survive a reader who did what you did.`,
  { label: 'Skeptic', phase: 'Skeptic', schema: SKEPTIC_SCHEMA, effort: 'high' })

if (skeptic && skeptic.sensitivity && skeptic.sensitivity.input !== 'none') {
  log(`Skeptic: ${skeptic.sensitivity.input} at ${skeptic.sensitivity.evidence_edge} gives ${skeptic.sensitivity.headline_at_edge}${skeptic.sensitivity.survives ? '' : ' (headline does not survive)'}`)
}

// ---------------------------------------------------------------------------
phase('Verify')

const open = [
  ...((review && review.unverified) || []).map(u => ({ ...u, from: 'Review' })),
  ...((skeptic && skeptic.unverified) || []).map(u => ({ ...u, from: 'Skeptic' })),
]
log(`Verify: ${open.length} unverified items`)

// Each item loops on its own; no barrier between items.
const verifyOne = async (u, _orig, i) => {
  let tried = ''
  let last = null
  for (let round = 1; round <= MAX_VERIFY_ROUNDS; round++) {
    last = await agent(`An unverified item from the pre-send gate on ${A.target}. Settle it. Do not hand it back as a list for Bryan.

Item: ${u.item}
Why it was not run: ${u.why}
What would settle it: ${u.how}
${tried ? `\nAlready tried in earlier rounds (do not repeat; find another way):\n${tried}\n` : ''}
Model code: ${modelPaths.join(', ') || '(none given)'}
Data: ${dataPaths.join(', ') || '(none given)'}

Run the query, open the document, or rerun the model. Fetch public data if it's needed. Do not change tracked files. Return verified or wrong only with the source opened or command run. Return impossible only when no public source or run could settle it, and name what is missing (for example, the data was never published, which is a records request). Return still-open when more work would settle it and say what is left.`,
      { label: `Verify ${i + 1}${round > 1 ? ` round ${round}` : ''}`, phase: 'Verify', schema: VERIFY_SCHEMA, effort: 'high' })
    if (!last) break
    if (last.status !== 'still-open') break
    tried += `- round ${round}: ${last.tried} -> ${last.result}\n`
  }
  return { ...u, ...(last || { status: 'still-open', result: 'verify agent did not return', evidence: '', tried, fix: '' }), tried: tried + (last ? last.tried : '') }
}
// Fleet cap on parallel fan-out is three (workflow-conventions.md); a caller may raise it.
const CAP = A.maxConcurrent || 3
const verified = []
for (let s = 0; s < open.length; s += CAP) {
  verified.push(...await parallel(open.slice(s, s + CAP).map((u, k) => () => verifyOne(u, null, s + k))))
}

const results = verified.filter(Boolean)
const stillOpen = results.filter(v => v.status === 'still-open')
if (stillOpen.length) log(`${stillOpen.length} items still open after ${MAX_VERIFY_ROUNDS} rounds each; listed in the result, not dropped`)

// ---------------------------------------------------------------------------
const blocking = [
  ...((review && review.findings) || []).filter(f => f.severity === 'blocks-send').map(f => `${f.id} ${f.where}: ${f.problem}`),
  ...((skeptic && skeptic.gaps) || []).filter(g => g.severity === 'blocks-send').map(g => `${g.kind} ${g.where}: ${g.gap}`),
  ...(skeptic && skeptic.sensitivity && skeptic.sensitivity.survives === false
    ? [`headline does not survive ${skeptic.sensitivity.input} at ${skeptic.sensitivity.evidence_edge}: ${skeptic.sensitivity.headline_at_edge}`] : []),
  ...results.filter(v => v.status === 'wrong').map(v => `verified wrong: ${v.item}: ${v.result}`),
  ...stillOpen.map(v => `still open: ${v.item}`),
]
if (!review) blocking.push('Review did not return')
if (!skeptic) blocking.push('Skeptic did not return')

return {
  verdict: blocking.length ? 'blocked' : 'ready-for-sign-off',
  blocking,
  review,
  skeptic,
  verified: results,
}
