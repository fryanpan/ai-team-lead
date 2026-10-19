# Every number must agree with the numbers it sits beside

Bryan, 2026-10-19, on a demo video made for a blog post that already states the benchmark: *"Why did you miss such a simple detail that took me 15s of looking at your results to go, oh that looks wrong? When we write a blog post, the parts must line up with each other or else we have a reason they don't."*

The demo showed a 77 s regular build and an 18.7 s fast build on a test phone. The benchmark for that phone in the same post says about 10 s and 2.7 s. The ratio (4.6x) matched the headline, so the agent took it as a confirmation. Its checks compared the video with its own event log. Neither check compared the video with the post.

## The rule

- **Before you hand over a result, put each of its numbers beside the published or earlier figure for the same quantity.** That means absolute values, not only ratios. A ratio can match while both of its terms are several times off.
- **Write the comparison into the deliverable's notes:** each figure from this result next to the reference figure, with the source of each.
- **If a figure is off by more than about 1.5x, explain the gap before you hand it over,** or fix the setup and measure again. "Warm", "typical" and "same as a developer mid-session" are claims. Each one needs a number behind it.
- **The reviewer should never be first to spot that the parts disagree.** If you hand over results that disagree with each other and you have not said why, the handover isn't done.

## How to apply

- Make a list of the numbers your deliverable shows. Do the same for the doc it supports (the post, the PR body, the report).
- For each pair, check the units, the conditions (cold or warm, thermal state, device, build) and the size.
- Self-consistency (video against log, frame against caption) is necessary but not sufficient. The check that matters is against what the reader has already been told.
