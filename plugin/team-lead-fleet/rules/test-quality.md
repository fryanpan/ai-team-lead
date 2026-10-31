# Test quality

A test is worth keeping when it fails if, and only if, the behaviour it names breaks. Agreed across the fleet 2026-10-30, at Bryan's request. It replaces the line "Prove each regression test fails without the fix" in `workflow-conventions.md`.

## Writing a test

- **Test behaviour, not implementation.** Drive and read what a user or caller sees: the page by role, id or data attribute, API requests and replies, a function's return value. A test that regexes source, a bundle or a CSS file is not a behaviour test. Count those mechanically and ratchet the count down, because review misses them.
- **Assert a specific outcome.** Check a text, value, request body or URL. "Didn't throw" is not an assertion, and neither is one that can't fail, such as `toBeTruthy()` on an element that always exists. Test the contract, not the order of calls.
- **Spread the expectations.** If every expectation sits on one boundary value, a stub can pass the whole suite. A `MIN()` stub once did.
- **One behaviour per test, and the name says it:** who does what, and what they see.
- **Keep it deterministic.**
  - Wait on a condition, never a fixed sleep. A sleep that is itself the assertion (nothing happens inside a debounce window) is allowed: mark it, and take its length from the real constant.
  - Time comes from a fake clock. Don't assert on wall-clock duration. A per-test time budget is fine.
  - Don't depend on test order or on the network.
  - Run browsers headless, with a throwaway profile, never in the person's own window.
- **Give each test its own synthetic data,** fresh for that test, with no shared mutable state.
- **Mock only at the boundary, and match the real contract.** Check a mock's shape against the real handler. When a test works around a gap in a mock, that gap is a finding on the mock. A parser of someone else's output needs at least one input you didn't write; fixtures copied from logs carry their capture date.
- **A test that proves something is absent needs a positive control:** the same probe, run without the change, sees it present.
- **Cover what users hit:** every user-visible error message, empty and loading states, and a reply that lands after the user has left.
- **Pin a known bug on purpose.** A test that asserts today's wrong behaviour names the issue in a comment, so the fix flips it.

## Proving a test works

- **Watch every new test go red.** Revert the fix or break the code, run the test, and read the assertion message. Then restore. Check that the mutant really ran: one that doesn't compile prints nothing, and one whose edit didn't apply prints green. Repeat this after every review round that changes the fix. One project found 6 of 40 tests still green with their fix reverted.
- **Get the same result locally and in CI.** A test that passes in only one place proves nothing in the other. One PR had two tests that failed only locally, because Java was missing from PATH. Log the preconditions a run depends on, such as device, build hash and network mode. A precondition you don't log, you didn't check.
- **A skipped test counts as not run, never as passed.** Report "N of M passed, K skipped", with a named reason for each skip. Read the count, not the colour of the log.
- **A new suite passes `--repeat-each 3` on its first PR,** with one worker and with the default workers. As a standing gate that costs too much. Before you report a flake rate, find what caused each flake; concurrent suites on one machine cause false failures.
- **Coverage shows a line ran, not that it was checked.** Each project sets its floor and ratchets it per package, never down. Don't suppress anything to hit the number. List every excluded line with its reason, dead or defensive. A covered branch whose result no test asserts doesn't count.
- **A green suite doesn't prove the feature works.** Do one real walk-through per release, on the target device or browser. One app passed 593 unit tests while its main screen showed blank on the phone.
- **Name the slowest tests in the run output,** and give the suite a time budget. Give the browser launch its own timeout, separate from each test's timeout.
