# Lessons Learned

Non-obvious pitfalls and design lessons.  Read before hitting the same
wall twice.

---

## Build & Environment

- **Nested package layout**: hatchling requires `src/kernel/kernel/`
  (outer is project root, inner is the Python package).
- **cloc**: PyPI's `cloc` package has no CLI.  Install the Perl tool
  via `apt install cloc`.
- **httpx HTTP/2 optional dependency**: `http2=True` needs package
  `h2`.  In environments without `h2`, web tools must gracefully
  downgrade to HTTP/1.1 instead of raising an internal error.
- **npm `--silent` removed in v11**: use `node --import tsx` directly
  instead of `npm run` to avoid noisy script banners.

---

## Async / Concurrency

- **`asyncio.gather(return_exceptions=True)` swallows `CancelledError`**:
  `CancelledError` is a `BaseException`, not an `Exception`.  With
  `return_exceptions=True`, it lands in the results list instead of
  propagating.  Always scan gather results for `BaseException`
  subclasses and re-raise `CancelledError` explicitly.

- **Sub-agent timeout must cancel the child task**: catching
  `TimeoutError` and emitting an error result is not enough — the
  child coroutine keeps running in the background.  Wrap the child
  query in an `asyncio.Task` and explicitly `.cancel()` it on timeout,
  then `await` the task to let cleanup run.

- **MCP reconnect race**: reject all pending futures *before*
  reconnecting, not after.  Otherwise in-flight callers unblock with
  stale state mid-reconnect.

- **Hook fire-and-forget leaks**: `asyncio.create_task()` without
  storing the reference allows tasks to be GC'd before completion.
  Keep task references in a set; remove on done callback; drain set
  on shutdown.

---

## Config & Serialisation

- **Never use `value or default` for config fields**: `0`, `""`, and
  `False` are all falsy, so users can never intentionally set those
  values.  Always use `value if value is not None else default`.

- **Env var substitution should warn on missing vars**: silently
  returning an empty string for `${UNDEFINED_VAR}` hides
  misconfiguration.  Log a warning so the operator sees it at startup.

---

## Security

- **SSRF via redirect chain**: checking the domain only on the
  *initial* URL is insufficient.  An attacker redirects `safe.com →
  169.254.169.254`.  The request to the private IP has already been
  sent by the time the final URL is inspected.  Solution: set
  `follow_redirects=False`, manually follow each hop, and check the
  domain *at every hop* before issuing the next request.

---

## Implementation Discipline

- **Never silently skip plan items**: if the plan lists 12 test files,
  all 12 must be written.  During SkillManager implementation, 5 of 12
  planned test files were skipped without explanation — including
  `test_skill_tool.py` which would have caught a missing `display`
  parameter on `ToolCallResult`.  The bug shipped and was only found
  during manual probe testing.  Rule: cross-check the plan's file list
  against `tests/` before marking done.  If a plan item is genuinely
  unnecessary, update the plan with the reason — don't silently drop it.

- **E2E tests must exercise actual code paths**: a test that sends a
  prompt and only asserts `stop_reason == "end_turn"` proves nothing
  about the feature.  E2E tests must assert on observable output —
  returned text content, tool call events in the stream, specific error
  messages for invalid input.  "Kernel didn't crash" is a smoke test,
  not feature verification.

- **Closures that cross subsystem boundaries REQUIRE a probe against
  the real subsystem — mock tests of the closure only prove your
  mental model is internally consistent**.  If a closure calls out to
  LLMManager, HookManager, MCP, or any subprocess/API, write a probe
  that actually invokes that subsystem.  Full procedure in
  [`workflow/definition-of-done.md`](workflow/definition-of-done.md)
  (five gates) and [`workflow/workflow.md`](workflow/workflow.md)
  Phase 4.5 (closure-seam inventory).  The `/done-check` skill
  (`.claude/skills/done-check/`) runs the enumeration automatically.
  Caught during Phase 1 CC alignment — 3 bugs lived in 3 such
  closures, all covered by passing mock tests:

  1. `_make_summarise_closure` iterated `async for chunk in
     llm_manager.stream(...)`, but `LLMManager.stream()` is
     `async def` returning a generator — must `await` first.  Mock
     returned a plain async generator so the bug was invisible.

  2. Same closure sent `PromptSection(text="")`.  Anthropic/Bedrock
     reject empty system text ("system: text content blocks must
     be non-empty").  Mock LLM accepted it fine.

  3. `fire_hook` closure called `hooks.fire(event, ctx)`, but
     `HookManager.fire()` only takes `ctx` (reads `ctx.event`
     internally).  Mock accepted any arg arity.

  Rule: for every such closure, there is a `scripts/probe_<name>.py`
  or `tests/e2e/test_<name>_e2e.py` that runs it against the real
  thing.  "Unit tests pass" is necessary but never sufficient.

---

## Kernel Design-debt Backlog

- **Hook executor dispatch**: hardcodes executor types.  Refactor to
  self-registering dispatch when `agent` type joins.
- **Orchestrator permission injection**: imports `needs_permission`
  directly.  Replace with injectable callable.
- **Glob `**` on huge directories**: slow scan, no guardrail yet.
- **web_fetch anti-bot fallback**: some modern sites require browser
  execution.  HTTP fetch is primary; add optional headless-browser
  fallback (Playwright) for timeout/empty-content failures.

