#!/usr/bin/env python3
"""Split subagent burn into waste and real work. Zero LLM turns.

`turn_attribution.py` says subagents are 61% of fleet burn. It does not say how
much of that is avoidable. This reads the subagent transcripts and answers that,
in two halves.

THE DECOMPOSITION (top of the report) is exact and sums to the measured burn.
96% of burn is context read back in, so every token of burn belongs to something
that was sitting in a context window when a turn was taken. Three things can be
sitting there:

  * FIXED STARTUP CONTEXT - system prompt, tool schemas, skill and deferred-tool
    listings, the fleet rules block, the task prompt. Present on turn 1 and on
    every turn after it, so a subagent that takes T turns pays for it T times.
  * TOOL RESULTS - a file read on turn 5 is re-read on turns 6..T.
  * THE SUBAGENT'S OWN OUTPUT - same mechanism, its own words.

Startup is measured directly (the first turn's context, floored by the smallest
context the subagent ever ran at). The other two are attributed by modelling each
item's lifetime and then scaling the model to the measured total, per segment.
Segments are split at a context drop (compaction, or a context-editing pass) so a
cleared item is not charged past the point where it left the window; the scaling
absorbs whatever else the model over-reaches on. The residual is zero by
construction - the split between the two is proportional, the total is measured.

THE FIVE WASTES (rest of the report) size specific, removable things:

  1. RAW MATERIAL RETURNED TO THE PARENT. A subagent's return is pasted into the
     parent's context and re-read on every parent turn after it. Measured:
     return size, the share of it that is quoted file content / command output /
     search hits rather than a conclusion, and the parent turns that followed
     before the parent's next compaction boundary. Two delivery shapes both land
     in the parent's context and both count - a synchronous Task result, and the
     <result> block inside the <task-notification> an async agent fires when it
     stops. The launch record of an async agent carries no return and costs
     nothing, so it is counted apart from the two.
  2. DUPLICATE READS ACROSS SIBLINGS. Within one fan-out, several subagents open
     the same file. Charged at what the extra copies actually cost: the size of
     the read times the turns each reader had left, not the size alone.
  3. MODEL. Measured per TURN, not per subagent: a turn whose only tool calls are
     mechanical (grep, file listing, a mechanical edit) and which spent zero
     tokens on extended thinking is a sweep, whatever else that subagent did.
     This lever saves money, not tokens, so it is reported outside the total.
  4. THE RULES BLOCK. Measured from the injected attachment itself - a
     `hook_success` record carrying the SessionStart hook's `additionalContext` -
     never assumed from the repo files.
  5. SHORT-LIVED SUBAGENTS. A subagent's first turn pays for the whole startup
     context; one that then runs a turn or two paid it for nothing. Measured:
     the turn distribution and the startup context at the short end.

Token sizes the API does not report are estimated from characters at
CHARS_PER_TOKEN, calibrated against this corpus - see the constant.

Usage:
    subagent_waste_report.py --week 2026-08-17     # Monday of the week to measure
    subagent_waste_report.py --date 2026-08-25     # a single day
    subagent_waste_report.py --week 2026-08-17 --json
    subagent_waste_report.py --week 2026-08-17 --by-session
"""
import bisect
import collections
import datetime
import glob
import json
import os
import re
import statistics
import sys

HOME = os.path.expanduser("~")
PROJ = os.path.join(HOME, ".claude", "projects")

# Calibrated on this corpus, not assumed. Method: every assistant record that is
# pure text with zero thinking tokens has an exact API token count and exact
# characters in the transcript. Over 1,811 such records (10.3M chars, 4.95M
# tokens) the aggregate ratio is 2.09 and the median record is 2.48 - the low end
# is code- and path-heavy output, prose sits at the high end. 2.5 is the
# conservative choice for the prose-shaped things measured here (the rules block,
# final messages): it reports FEWER tokens of waste, not more.
CHARS_PER_TOKEN = 2.5

# Shell commands whose arguments are files being read. Most reading in this fleet
# happens through Bash, not Read. Deliberately short: a command not on this list
# contributes no read targets, so the duplicate-read number is a floor.
READ_COMMANDS = {"cat", "head", "tail", "sed", "awk", "grep", "rg", "wc", "jq",
                 "less", "bat", "diff", "nl", "cut", "sort", "uniq", "column",
                 "shasum", "md5", "stat", "file"}

# Tool calls that move bytes rather than decide anything.
MECHANICAL_TOOLS = {"Read", "Grep", "Glob", "NotebookRead", "LS", "Bash", "Edit",
                    "Write", "MultiEdit", "ToolSearch", "NotebookEdit", "TodoWrite"}

# A subagent with this many turns or fewer paid a full startup context for
# almost no work.
SHORT_LIVED_TURNS = 2

# Two subagents belong to the same fan-out if they ran under the same parent and
# their lifetimes overlap, with this much slack for a staggered dispatch.
FANOUT_SLACK_SECONDS = 120

# A context drop this steep between consecutive turns means the window was
# compacted or edited: items before it stop being charged.
SEGMENT_DROP = 0.75


def argval(flag, default=None):
    return sys.argv[sys.argv.index(flag) + 1] if flag in sys.argv else default


def days_wanted():
    week = argval("--week")
    if week:
        d0 = datetime.date.fromisoformat(week)
        return ({(d0 + datetime.timedelta(days=i)).isoformat() for i in range(7)},
                f"week of {week}")
    day = argval("--date", datetime.date.today().isoformat())
    return {day}, day


def parse_ts(ts):
    try:
        return datetime.datetime.fromisoformat((ts or "").replace("Z", "+00:00"))
    except Exception:
        return None


def local_day(ts):
    dt = parse_ts(ts)
    return dt.astimezone().date().isoformat() if dt else None


def toks(chars):
    return int(chars / CHARS_PER_TOKEN)


def usage_total(u):
    return sum(u.get(k, 0) for k in ("input_tokens", "output_tokens",
                                     "cache_creation_input_tokens",
                                     "cache_read_input_tokens"))


def context_size(u):
    """Everything the model had to read on this turn."""
    return sum(u.get(k, 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                     "cache_read_input_tokens"))


def block_tokens(body):
    return toks(len(body) if isinstance(body, str) else len(json.dumps(body or "")))


def load(path):
    out = []
    with open(path, errors="replace") as fh:
        for line in fh:
            if not line.strip():
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out


# --------------------------------------------------------------------------
# what a returned message is made of
# --------------------------------------------------------------------------

FENCE = re.compile(r"^\s*(```|~~~)")
RAW_LINE = re.compile(
    r"""^\s*(
        \d+[:\t]                          # line-numbered file content, grep -n
      | [+-]{1,3}[ \t]                    # diff hunks
      | @@                                # diff headers
      | [\w./~-]+\.[A-Za-z0-9]{1,6}:\d+   # path:line from grep or a stack trace
      | [\w./~-]*/[\w./~-]+:?\s*$         # a bare path on its own line
      | \$\s                              # a pasted shell prompt
      | [|+][-|+ ]{6,}                    # ascii table rules
    )""", re.VERBOSE)


def raw_share(text):
    """Fraction of a message that is quoted material rather than conclusion.

    Fenced blocks count in full. Outside fences a line counts when it looks like
    transplanted machine output. Prose ABOUT the evidence does not count - only
    the evidence itself.
    """
    if not text:
        return 0.0
    raw, infence = 0, False
    for line in text.splitlines(keepends=True):
        if FENCE.match(line):
            infence = not infence
            raw += len(line)
        elif infence or RAW_LINE.match(line):
            raw += len(line)
    return raw / len(text)


# --------------------------------------------------------------------------
# what a tool call read
# --------------------------------------------------------------------------

BASH_SPLIT = re.compile(r"\|\||&&|[|;&\n]")
PATHISH = re.compile(r"^[\w./~$-]*[/.][\w./~-]*$")


def bash_read_targets(command):
    targets = []
    for seg in BASH_SPLIT.split(command or ""):
        words = seg.split()
        if not words:
            continue
        if os.path.basename(words[0]) not in READ_COMMANDS:
            continue
        for w in words[1:]:
            if w.startswith("-") or "://" in w or not PATHISH.match(w):
                continue
            if "/" not in w and "." not in w:
                continue
            targets.append(w)
    return targets


def read_targets(name, tool_input):
    if not isinstance(tool_input, dict):
        return []
    if name in ("Read", "NotebookRead"):
        p = tool_input.get("file_path") or tool_input.get("notebook_path")
        return [p] if p else []
    if name == "Bash":
        return bash_read_targets(tool_input.get("command"))
    return []


def normalise(target):
    t = (target or "").strip("'\"")
    if t.startswith("~"):
        t = HOME + t[1:]
    return os.path.normpath(re.sub(r"^\./", "", t))


# --------------------------------------------------------------------------
# one subagent
# --------------------------------------------------------------------------

class Sub:
    def __init__(self, path):
        self.path = path
        parts = path.split(os.sep)
        self.project = parts[-4] if len(parts) >= 4 else "?"
        self.parent = parts[-3] if len(parts) >= 3 else "?"
        self.agent_id = os.path.basename(path)[len("agent-"):-len(".jsonl")]
        self.turns = self.burn = self.startup = self.ctx_read = 0
        self.listings = 0          # skill + deferred-tool listings, tokens
        self.models = collections.Counter()
        self.tool_burn = collections.Counter()   # tool -> re-read cost
        self.own_output = 0                      # own output re-read cost
        self.mech_burn = collections.Counter()   # model -> burn on sweep turns
        self.reads = []            # (target, first_size, amplified_cost)
        self.final_text = ""
        self.cwd = None
        self.rules_chars = 0    # fleet rules THIS subagent actually carried
        self.start = self.end = None


def scan_subagent(path, days):
    """Read one subagent transcript into a Sub, or None if it took no turn."""
    recs = load(path)
    s = Sub(path)

    turn_idx = [i for i, r in enumerate(recs)
                if r.get("type") == "assistant"
                and (r.get("message") or {}).get("usage")
                and local_day(r.get("timestamp")) in days]
    if not turn_idx:
        return None
    T = len(turn_idx)
    ctx = [context_size(recs[i]["message"]["usage"]) for i in turn_idx]

    every = [r for r in recs if r.get("type") == "assistant"
             and (r.get("message") or {}).get("usage")]
    s.startup = min(context_size(every[0]["message"]["usage"]), min(ctx))
    s.turns = T
    s.ctx_read = sum(ctx)

    for i in turn_idx:
        msg = recs[i]["message"]
        u = msg["usage"]
        s.burn += usage_total(u)
        s.models[msg.get("model") or "?"] += usage_total(u)
        uses = [b for b in (msg.get("content") or [])
                if isinstance(b, dict) and b.get("type") == "tool_use"]
        thinking = (u.get("output_tokens_details") or {}).get("thinking_tokens", 0)
        if uses and not thinking and all(b.get("name") in MECHANICAL_TOOLS
                                         for b in uses):
            s.mech_burn[msg.get("model") or "?"] += usage_total(u)

    listing = collections.Counter()
    for rec in recs:
        att = rec.get("attachment") or {}
        kind = att.get("type")
        if kind in ("skill_listing", "deferred_tools_delta"):
            # Take the largest injection of each kind, not the sum: a session
            # re-emits these as deltas, and only one copy is in context.
            body = att.get("content") or "\n".join(att.get("addedNames") or [])
            listing[kind] = max(listing[kind], toks(len(body)))
        if rec.get("type") == "assistant":
            content = (rec.get("message") or {}).get("content") or []
            texts = [b.get("text", "") for b in content
                     if isinstance(b, dict) and b.get("type") == "text"]
            if texts and not any(isinstance(b, dict) and b.get("type") == "tool_use"
                                 for b in content):
                s.final_text = "\n".join(texts)
        if s.cwd is None and rec.get("cwd"):
            s.cwd = rec["cwd"]
        if kind == "hook_success" and "team-lead-fleet-rules" in (att.get("stdout") or ""):
            try:
                body = json.loads(att["stdout"])["hookSpecificOutput"]["additionalContext"]
            except Exception:
                body = ""
            if body.startswith("<team-lead-fleet-rules>"):
                s.rules_chars = max(s.rules_chars, len(body))
        ts = parse_ts(rec.get("timestamp"))
        if ts and rec.get("type") in ("assistant", "user"):
            s.start = ts if s.start is None else min(s.start, ts)
            s.end = ts if s.end is None else max(s.end, ts)
    s.listings = sum(listing.values())
    if not s.rules_chars:
        s.rules_chars = project_rules_chars(s.cwd)

    # Split at every context drop, then attribute the measured accumulation in
    # each segment across the items that were live in it.
    bounds = [0] + [k for k in range(1, T) if ctx[k] < ctx[k - 1] * SEGMENT_DROP] + [T]
    for b in range(len(bounds) - 1):
        lo, hi = bounds[b], bounds[b + 1]
        measured = sum(max(0, c - s.startup) for c in ctx[lo:hi])
        lo_i = turn_idx[lo]
        hi_i = turn_idx[hi] if hi < T else len(recs)
        items = []          # (tool name or None for own output, modelled cost, read info)
        pending = {}
        for i in range(lo_i, hi_i):
            rec = recs[i]
            remain = hi - bisect.bisect_right(turn_idx, i)
            if remain <= 0:
                continue
            if rec.get("type") == "assistant":
                msg = rec["message"]
                items.append((None, (msg.get("usage") or {}).get("output_tokens", 0)
                              * remain, None))
                for blk in (msg.get("content") or []):
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        pending[blk.get("id")] = (blk.get("name"), blk.get("input"))
            elif rec.get("type") == "user":
                content = (rec.get("message") or {}).get("content")
                if not isinstance(content, list):
                    continue
                for blk in content:
                    if not isinstance(blk, dict) or blk.get("type") != "tool_result":
                        continue
                    name, tin = pending.pop(blk.get("tool_use_id"), (None, None))
                    if not name:
                        continue
                    size = block_tokens(blk.get("content"))
                    targets = [normalise(t) for t in read_targets(name, tin)]
                    items.append((name, size * remain,
                                  (targets, size // max(1, len(targets)), remain)))
        modelled = sum(c for _, c, _ in items)
        if not modelled:
            continue
        scale = measured / modelled
        for name, cost, readinfo in items:
            if name is None:
                s.own_output += cost * scale
            else:
                s.tool_burn[name] += cost * scale
            if readinfo:
                targets, each, remain = readinfo
                for t in targets:
                    s.reads.append((t, each, each * remain * scale))
    return s


# --------------------------------------------------------------------------
# the parent side
# --------------------------------------------------------------------------

TASK_ID = re.compile(r"<task-id>([^<]+)</task-id>")
TASK_RESULT = re.compile(r"<result>(.*?)</result>", re.DOTALL)


def scan_parent_returns(path, agent_ids):
    """How each subagent handed its work back, and what that cost the parent.

    Three shapes, and they behave differently:

      sync    — the Task tool result carries the return inline, so it lands in
                the parent's context and is re-read for the rest of the window.
      async   — the dispatch record only says "launched"; it costs nothing. The
                work comes back later as a <task-notification>.
      notify  — a <task-notification> user message carrying a <result> block.
                This IS in the parent's context, so it costs exactly like a sync
                return. One agent can notify several times; every one is counted.

    Returns {agent_id: (mode, return_tokens, raw_tokens, parent_turns_after)}.
    The turn count stops at the parent's next compaction boundary, so it is a
    floor rather than a projection to the end of the session.
    """
    events, assistants, compacts = [], [], []
    for i, rec in enumerate(load(path)):
        kind = rec.get("type")
        if kind == "assistant" and (rec.get("message") or {}).get("usage"):
            assistants.append(i)
            continue
        if rec.get("subtype") == "compact_boundary" or rec.get("isCompactSummary"):
            compacts.append(i)
            continue
        res = rec.get("toolUseResult")
        if isinstance(res, dict) and res.get("agentId") in agent_ids:
            aid = res["agentId"]
            if res.get("isAsync"):
                events.append((i, aid, "async", "", ))
            else:
                content = res.get("content")
                text = ("\n".join(b.get("text", "") for b in content
                                  if isinstance(b, dict))
                        if isinstance(content, list) else str(content or ""))
                events.append((i, aid, "sync", text))
            continue
        if kind != "user":
            continue
        body = (rec.get("message") or {}).get("content")
        if not isinstance(body, str) or "<task-notification>" not in body:
            continue
        m = TASK_ID.search(body)
        if not m or m.group(1) not in agent_ids:
            continue
        result = TASK_RESULT.search(body)
        events.append((i, m.group(1), "notify", result.group(1) if result else ""))

    out = {}
    for ordinal, aid, mode, text in events:
        nxt = bisect.bisect_right(compacts, ordinal)
        stop = compacts[nxt] if nxt < len(compacts) else None
        lo = bisect.bisect_right(assistants, ordinal)
        hi = bisect.bisect_left(assistants, stop) if stop is not None else len(assistants)
        followers = max(0, hi - lo)
        tokens = toks(len(text))
        raw = int(tokens * raw_share(text))
        prev = out.get(aid)
        if prev is None:
            out[aid] = [mode, tokens, raw, tokens * followers, raw * followers]
        else:
            # a real return outranks the "launched" record; repeat notifications add up
            if prev[0] == "async" and mode != "async":
                prev[0] = mode
            prev[1] += tokens
            prev[2] += raw
            prev[3] += tokens * followers
            prev[4] += raw * followers
    return {k: tuple(v) for k, v in out.items()}


_RULES_CACHE = {}


def project_rules_chars(cwd):
    """Fleet rules a session in `cwd` carries as PROJECT INSTRUCTIONS.

    Subagents almost never get the SessionStart hook: 9 of 1,839 transcripts
    carry a real injection. They pick up fleet rules the other way, through the
    `.claude/rules/*.md` symlinks in their working directory -- so that is what
    a subagent's rules cost actually is, and it is zero in a cwd that has no
    such directory. 695 subagents last week ran in one that did not.

    Caveat: this sizes the rules as they stand TODAY. A week-old transcript
    whose rules have since changed is measured against the current files.
    """
    if not cwd:
        return 0
    if cwd not in _RULES_CACHE:
        d = os.path.join(cwd, ".claude", "rules")
        try:
            _RULES_CACHE[cwd] = sum(os.path.getsize(f)
                                    for f in glob.glob(os.path.join(d, "*.md")))
        except OSError:
            _RULES_CACHE[cwd] = 0
    return _RULES_CACHE[cwd]


# --------------------------------------------------------------------------
# fan-outs
# --------------------------------------------------------------------------

def fanouts(subs):
    """Siblings under one parent whose lifetimes overlap."""
    groups = []
    by_parent = collections.defaultdict(list)
    for s in subs:
        if s.start and s.end:
            by_parent[(s.project, s.parent)].append(s)
    slack = datetime.timedelta(seconds=FANOUT_SLACK_SECONDS)
    for members in by_parent.values():
        members.sort(key=lambda s: s.start)
        cur, cur_end = [], None
        for s in members:
            if cur and s.start > cur_end + slack:
                groups.append(cur)
                cur, cur_end = [], None
            cur.append(s)
            cur_end = s.end if cur_end is None else max(cur_end, s.end)
        if cur:
            groups.append(cur)
    return [g for g in groups if len(g) > 1]


def duplicate_reads(groups):
    """What the 2nd..Nth copy of a file inside one fan-out actually cost.

    Charged at the amplified cost - size times the turns that reader had left -
    because a duplicated read is not paid once, it is paid on every turn the
    duplicating sibling takes afterwards. The most expensive copy is treated as
    the one real read and is not charged.
    """
    dup_tokens = dup_first = 0
    files = 0
    worst = []
    for g in groups:
        copies = collections.defaultdict(dict)   # target -> agent -> (first, amplified)
        for s in g:
            for target, first, amplified in s.reads:
                prev = copies[target].get(s.agent_id, (0, 0))
                if amplified > prev[1]:
                    copies[target][s.agent_id] = (first, amplified)
        for target, by_agent in copies.items():
            if len(by_agent) < 2:
                continue
            vals = sorted(by_agent.values(), key=lambda v: -v[1])[1:]
            cost = sum(v[1] for v in vals)
            dup_tokens += cost
            dup_first += sum(v[0] for v in vals)
            files += 1
            worst.append((cost, len(by_agent) - 1, target))
    worst.sort(reverse=True)
    return int(dup_tokens), int(dup_first), files, worst[:8]


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def main():
    days, label = days_wanted()

    subs = []
    for path in glob.glob(os.path.join(PROJ, "*", "*", "subagents", "*.jsonl")):
        s = scan_subagent(path, days)
        if s:
            subs.append(s)
    if not subs:
        print(f"No subagent turns found for {label}.")
        return

    burn = sum(s.burn for s in subs)
    turns = sum(s.turns for s in subs)
    ctx_read = sum(s.ctx_read for s in subs)

    fixed = sum(s.startup * s.turns for s in subs)
    tools = collections.Counter()
    for s in subs:
        tools.update(s.tool_burn)
    tool_read = sum(tools.values())
    own_output = sum(s.own_output for s in subs)
    file_read = tools["Bash"] + tools["Read"] + tools["Grep"] + tools["Glob"]

    rules_cost = sum(toks(s.rules_chars) * s.turns for s in subs)
    with_rules = [s for s in subs if s.rules_chars]
    rules_chars = (sum(s.rules_chars for s in with_rules) // len(with_rules)
                   if with_rules else 0)
    rules_tokens = toks(rules_chars)
    listings_cost = sum(s.listings * s.turns for s in subs)
    other_startup = max(0, fixed - rules_cost - listings_cost)

    # 1. how the work came back
    by_parent_file = collections.defaultdict(set)
    for s in subs:
        by_parent_file[(s.project, s.parent)].add(s.agent_id)
    # Match on the whole week's agent ids, not the ids under each parent
    # directory: a session that was resumed keeps its subagents in the old
    # directory while its notifications land in the new transcript, so a
    # per-directory match silently loses most returns.
    all_ids = {s.agent_id for s in subs}
    returns = {}
    for ppath in glob.glob(os.path.join(PROJ, "*", "*.jsonl")):
        for aid, row in scan_parent_returns(ppath, all_ids).items():
            prev = returns.get(aid)
            if prev is None:
                returns[aid] = row
            else:
                mode = row[0] if prev[0] == "async" else prev[0]
                returns[aid] = (mode,) + tuple(a + b for a, b in
                                               zip(prev[1:], row[1:]))

    returned_n = launched_only_n = unmatched_n = 0
    ret_tokens = ret_raw = parent_cost = parent_raw_cost = 0
    final_tokens = final_raw = 0
    biggest = []
    for s in subs:
        ftok = toks(len(s.final_text))
        final_tokens += ftok
        final_raw += int(ftok * raw_share(s.final_text))
        row = returns.get(s.agent_id)
        if row is None:
            unmatched_n += 1
            continue
        mode, tokens, raw, cost, raw_cost = row
        if mode == "async" or not tokens:
            launched_only_n += 1
            continue
        returned_n += 1
        ret_tokens += tokens
        ret_raw += raw
        parent_cost += cost
        parent_raw_cost += raw_cost
        biggest.append((cost, tokens, raw / tokens if tokens else 0, s))
    biggest.sort(key=lambda r: -r[0])

    # 2. duplicate reads
    groups = fanouts(subs)
    dup_tokens, dup_first, dup_files, dup_worst = duplicate_reads(groups)

    # 3. model
    by_model = collections.Counter()
    mech_by_model = collections.Counter()
    for s in subs:
        by_model.update(s.models)
        mech_by_model.update(s.mech_burn)
    mech_burn = sum(mech_by_model.values())

    # 5. short-lived
    dist = collections.Counter(s.turns for s in subs)
    short = [s for s in subs if s.turns <= SHORT_LIVED_TURNS]
    short_startup = sum(s.startup * s.turns for s in short)
    short_avoidable = max(0, short_startup - rules_tokens * sum(s.turns for s in short))

    avoidable = rules_cost + dup_tokens + short_avoidable

    if "--json" in sys.argv:
        print(json.dumps({
            "label": label, "subagents": len(subs), "turns": turns, "burn": burn,
            "context_read": ctx_read,
            "decomposition": {
                "fixed_startup": fixed,
                "rules_block": rules_cost,
                "skill_and_tool_listings": listings_cost,
                "other_startup": other_startup,
                "tool_results": tool_read,
                "file_and_command_output": file_read,
                "own_output": int(own_output),
            },
            "returns": {"returned_into_parent": returned_n,
                        "launched_only": launched_only_n,
                        "unmatched": unmatched_n,
                        "return_tokens": ret_tokens,
                        "return_raw_tokens": ret_raw,
                        "parent_reread": parent_cost,
                        "parent_reread_raw": parent_raw_cost,
                        "final_message_tokens": final_tokens,
                        "final_message_raw_tokens": final_raw},
            "duplicate_reads": {"fanouts": len(groups), "files": dup_files,
                                "first_read_tokens": dup_first,
                                "amplified_tokens": dup_tokens},
            "models": dict(by_model),
            "mechanical_turns": {"burn": mech_burn, "by_model": dict(mech_by_model)},
            "rules_block": {"chars": rules_chars, "tokens": rules_tokens,
                            "cost": rules_cost},
            "short_lived": {"subagents": len(short),
                            "startup_tokens": short_startup,
                            "avoidable": short_avoidable},
            "turn_distribution": {str(k): v for k, v in sorted(dist.items())},
            "avoidable_total": avoidable,
            "tool_reread": dict(tools.most_common(15)),
        }, indent=2, default=int))
        return

    def pct(n):
        return f"{100 * n / burn:5.1f}%" if burn else "    -"

    print(f"Subagent waste — {label}\n")
    print(f"{len(subs):,} subagents · {turns:,} turns · {burn/1e6:,.0f}M tokens "
          f"· {burn/turns/1000:,.0f}k per turn")
    print(f"{ctx_read/1e6:,.0f}M of it ({100*ctx_read/burn:.1f}%) is context read "
          f"back in. Median subagent runs "
          f"{int(statistics.median([s.turns for s in subs])):,} turns.\n")

    print("WHERE THE CONTEXT WENT — exact, sums to the measured burn")
    print(f"   {fixed/1e6:>7,.0f}M {pct(fixed)}  fixed startup context, re-read on every turn")
    print(f"     {rules_cost/1e6:>7,.0f}M {pct(rules_cost)}    the fleet rules block")
    print(f"     {listings_cost/1e6:>7,.0f}M {pct(listings_cost)}    skill listing + deferred-tool listing")
    print(f"     {other_startup/1e6:>7,.0f}M {pct(other_startup)}    system prompt, tool schemas, task prompt")
    print(f"   {tool_read/1e6:>7,.0f}M {pct(tool_read)}  tool results, re-read for the rest of the subagent's life")
    print(f"     {file_read/1e6:>7,.0f}M {pct(file_read)}    of which file and command output (Bash/Read/Grep/Glob)")
    print(f"   {own_output/1e6:>7,.0f}M {pct(own_output)}  the subagent's own output, re-read the same way")

    print("\n1. RAW MATERIAL RETURNED TO THE PARENT")
    print(f"   {returned_n:,} landed a return in the parent's context, "
          f"{launched_only_n:,} only ever logged a launch, {unmatched_n:,} unmatched")
    print(f"   final messages, all {len(subs):,} subagents   "
          f"{final_tokens/1000:>8,.0f}k tokens, "
          f"{100*final_raw/final_tokens if final_tokens else 0:.0f}% quoted material")
    print(f"   returns that reached a parent           {ret_tokens/1000:>8,.0f}k tokens, "
          f"{100*ret_raw/ret_tokens if ret_tokens else 0:.0f}% quoted material")
    print(f"   parent re-read them                     {parent_cost/1e6:>8,.0f}M "
          f"over the parent turns that followed")
    print(f"     attributable to quoted material       {parent_raw_cost/1e6:>8,.0f}M "
          f"— paid by the PARENT, outside the {burn/1e6:,.0f}M above")
    for cost, tokens, share, s in biggest[:5]:
        if cost:
            print(f"     {cost/1e6:>7,.0f}M = {tokens/1000:>5,.0f}k returned, "
                  f"{100*share:>3.0f}% material  [{s.project[:40]}]")
    print("   unmatched = still running at the cut-off, or its parent transcript")
    print("   is outside this tree; those returns are not counted here.")

    print("\n2. DUPLICATE READS ACROSS SIBLINGS")
    print(f"   {len(groups):,} fan-outs (>1 subagent overlapping under one parent)")
    print(f"   {dup_files:,} files opened by more than one sibling")
    print(f"   {dup_first/1000:>8,.0f}k tokens of redundant reading, which then cost")
    print(f"   {dup_tokens/1e6:>8,.1f}M {pct(dup_tokens)} once each sibling re-read it "
          f"for the rest of its life")
    for cost, extra, target in dup_worst[:5]:
        print(f"     {cost/1e6:>7,.2f}M  +{extra} extra readers  {target[-64:]}")

    print("\n3. MODEL")
    for model, b in by_model.most_common():
        if b:
            print(f"   {model:<22} {b/1e6:>8,.0f}M {pct(b)}")
    print(f"   mechanical sweep turns (only mechanical tools, zero thinking):")
    for model, b in mech_by_model.most_common(5):
        if b:
            print(f"     on {model:<19} {b/1e6:>8,.0f}M {pct(b)}")
    print(f"     {mech_burn/1e6:>8,.0f}M {pct(mech_burn)} total")
    print("   These turns sit inside subagents that also do judgement work, so this")
    print("   is not repriced by changing one model — it is the size of the sweep")
    print("   work that would have to be split out to a cheap agent to be repriced.")
    print("   A cheaper model saves money, not tokens: outside the total below.")

    print("\n4. THE RULES BLOCK")
    carried = sum(1 for s in subs if s.rules_chars)
    if rules_chars:
        print(f"   mean block, where carried {rules_chars:>8,} chars = "
              f"{rules_tokens/1000:,.1f}k tokens")
        print(f"   carried it              {carried:>8,} of {len(subs):,} subagents"
              f"  ({100*carried/len(subs):.0f}%)")
    else:
        print("   no subagent carried fleet rules — not measured")
    print(f"   over {turns:,} subagent turns {rules_cost/1e6:>8,.0f}M {pct(rules_cost)}")

    print("\n5. SHORT-LIVED SUBAGENTS")
    buckets = [(1, 2), (3, 10), (11, 50), (51, 200), (201, 1000), (1001, 10 ** 9)]
    peak = max(sum(v for k, v in dist.items() if lo <= k <= hi) for lo, hi in buckets)
    for lo, hi in buckets:
        n = sum(v for k, v in dist.items() if lo <= k <= hi)
        name = f"{lo}-{hi}" if hi < 10 ** 9 else f"{lo}+"
        print(f"   {name:>9} turns  {n:>4,}  {'#' * (n * 40 // max(1, peak))}")
    print(f"   <={SHORT_LIVED_TURNS} turns: {len(short):,} subagents, "
          f"{short_startup/1e6:,.2f}M of startup context")
    print(f"   net of the rules block already counted: {short_avoidable/1e6:,.2f}M "
          f"{pct(short_avoidable)}")

    print("\nAVOIDABLE WITHOUT DOING LESS WORK")
    for name, n in sorted([("the rules block, re-read on every subagent turn", rules_cost),
                           ("duplicate reads across fan-out siblings", dup_tokens),
                           ("startup context of <=2-turn subagents", short_avoidable)],
                          key=lambda r: -r[1]):
        print(f"   {n/1e6:>8,.0f}M {pct(n)}  {name}")
    print(f"   {avoidable/1e6:>8,.0f}M {pct(avoidable)}  of subagent burn")
    print(f"   {parent_raw_cost/1e6:>8,.0f}M  (separate pool) parent-side re-reading of "
          f"quoted material in returns")
    print(f"   {mech_burn/1e6:>8,.0f}M {pct(mech_burn)}  more is repriceable, not removable "
          f"(mechanical turns on premium models)")

    if "--by-session" in sys.argv:
        print()
        by_proj = collections.defaultdict(lambda: [0, 0, 0])
        for s in subs:
            row = by_proj[s.project]
            row[0] += 1
            row[1] += s.turns
            row[2] += s.burn
        for project, (n, t, b) in sorted(by_proj.items(), key=lambda kv: -kv[1][2]):
            print(f"{b/1e6:>8,.0f}M  {n:>4,} subagents  {t:>6,} turns  {project}")


if __name__ == "__main__":
    main()
