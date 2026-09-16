# Triage — adversarial review of the gate-default-on diff (2026-09-16)

Reviewers: **Grok** (grok-4.6, effort high, 926 s, `…-grok-raw.md`) and
**Astra** (gpt-6-astra on the codex lane). Astra's first arm **refused** —
OpenAI's cyber gate tripped on the "break it" framing (668 chars, effort low,
42 s, `…-astra-raw.md`); a reframed retry ("input-validation review of my
own defensive code", effort high) was dispatched — see the bottom of this
file for its outcome.

Pack: the full uncommitted diff across harness/_lib/observability/wiki/
cc-skills/decisions (~64 KB) + a ten-surface attack brief.

## Grok's verdict, in its words

> The fs default-on gate is a real control. The bash and HA pieces are not.

Correct on both counts. Every CONFIRMED item was reproduced here before it
was fixed; the "fix" column names the change and the test that now pins it.

| # | sev | finding | disposition |
|---|---|---|---|
| 1 | CRIT | `ha_post` allowed `/api/config/automation/config/<id>` without reading the body — an automation body may call every denied service (shell, lock, restart). | **FIXED.** Body (dict, str or bytes) is parsed and every `service`/`action` string at any depth is run through the same denylist. Tests: `ha-automation-body-*` ×5. |
| 2 | HIGH | Percent-encoded / upper-case domain (`%6c%6f%63%6b`, `LOCK`) bypassed the domain denylist. | **FIXED.** `unquote()` + `.lower()` once, before any comparison; segments must match `[a-z0-9_.]+` so a double-encoded path fails shape. Tests: `ha-percent-encoded-domain-deny`, `ha-uppercase-domain-deny`, `ha-double-encoded-deny`. |
| 3 | HIGH | `bash_policy` used `re.match` (start-anchored), not whole-string. | **FIXED.** `fullmatch`. Test: `policy: shell_syntax=True is still a fullmatch`. |
| 4 | HIGH | `\S+` in an allowlist swallows `; \| && $() \`` — six working injections against the README's own example. | **FIXED.** An allowlist policy refuses any of `;\|&$\`()<>` unless the caller passes `shell_syntax=True`; README example rewritten. Tests: nine `allowlist-shaped injection denied` cases. |
| 5 | HIGH | `ranch_diag`'s `df( /\S*)?` admitted `df /;id` — the one unattended job. | **FIXED.** Mount arg is `/[A-Za-z0-9._/-]*`; the job opts into `shell_syntax=True` because its gateway probe is a spelled-out literal. Tests: six must-deny cases in `ranch_diag --selftest` (19/19); live re-proved on the .21 node, zero denials, 17 s. |
| 6 | HIGH | Denylist missed house-level domains: `script`, `automation`, `alarm_control_panel`, `cover`, `rest_command`, `command_line`, `backup`, `homeassistant/reload_core_config`, `set_location`; `notify/*` to any target. | **FIXED (denylist widened, not inverted).** Grok proposed inverting to an allowlist mirroring `egress-proxy/routes.json`; declined — that is a second copy of the proxy's fact (one home) and it would forbid "operate the ranch" verbs (`climate`, `switch`, `select`, `automation/turn_on`) Craig has asked the agent to use. Added the listed domains plus `persistent_notification`, `recorder`, `system_log`; `notify` service must fullmatch `mobile_app_[a-z0-9_]+`. Tests: `ha-deny-*` ×11, `ha-notify-own-device-allow`, `ha-climate-allow`. |
| 7 | HIGH | Fitness gate ran the model-under-test with `BASH_ANY` (`cat ~/.key/ha_token` allowed). | **FIXED.** Three literal `wc -l` shapes, `shell_syntax=True` for the spelled-out pipe. A model reaching for anything else is refused — that is a fitness signal. |
| 8 | HIGH | `wiki/ask_local.search_notes`: no `--` before the pattern → grep option injection (`-f/tmp/x`, live-confirmed by Grok); `read_note` policy accepted `/etc/passwd` textually. | **FIXED.** `--` in the argv; policy holds `pattern` to `[A-Za-z0-9][A-Za-z0-9._-]{0,79}` (the tool's own "single word" contract); `read_note` uses the built-in path rule via `policy.relative_path()`. |
| 9 | MED | `BASH_ANY` on the CLI is theatre (no permission card); on ACP it is honest but `allow_always` is per-tool. | **FIXED (CLI):** `--bash-any` is an explicit opt-in; the default offers bash and denies every call, naming the flag. **FIXED (ACP, same day, second pass):** `allow_always` on `run_bash` grants the command's program prefix(es) for the session (`acp_server.bash_prefixes`), the native Claude Code idiom; a compound command needs every segment granted; wrappers (`sudo`, `bash -c`, `env`, `xargs`, `python3 -c`…), substitutions and redirections are offered no standing grant at all; a forged `allow_always` on an unoffered card is a deny. Corral renders the server's option label, so no Corral change. Tests: 13 `prefix:` checks in `selftest_acp`. |
| 10 | MED | Unicode line separators (U+2028/2029/NEL/VT/FF) in `subject`/`sender` passed a `\r\n`-only check. | **FIXED.** `_has_ctrl` denies categories `Cc`/`Zl`/`Zp` (both spines, harness and `_lib`). Tests: `mail-subject-u2028-deny`, `mail-subject-nel-deny`, `policy: U+2028 denied`. |
| 11 | MED | Comma in a display name split into two "addresses"; `_check_recipient` `str()`-ed a list into one bogus address. | **FIXED.** `policy_gate.addresses()` parses with `email.utils.getaddresses` and is the ONE parser for both the schema and the authorization loop. Tests: `mail-display-name-comma-allow`, `mail-list-nonstr-deny`, `mail-broken-brackets-deny`, `mail-bad-domain-deny`, and the list/display-name cases in `test_core_policy`. |
| 12 | MED | `RecipientNotAllowed` re-wrap lost the `PolicyDenied` type. | **FIXED.** `RecipientNotAllowed(policy_gate.PolicyDenied)`; asserted in `test_core_policy`. |
| 13 | MED | `_harness_bash_policy` attribute spoof; `_BUILTIN` dict mutable at call time; the docstring's "an ungated run does not exist" is false because `gate=<permissive>` exists. | **FIXED (partly), DOCUMENTED (rest).** `_BUILTIN` is a `MappingProxyType` (test: `built-in table is read-only`). The docstring now says an *implicit* ungated run does not exist and names `gate=` as the greppable opt-out. The spoof is not defended: the trust model is git-tracked code, not the interpreter — stated in the module docstring. |
| 14 | MED | `DEFAULT = gate()` captures the spine once. | **No change.** Fail-closed stale-deny, never an allow; documented in `gate()`'s docstring. |
| 15 | LOW | Textual path holes that `_confine` still catches (backslash, drive letter, padded, `foo/.. /bar`). | **FIXED** the cheap ones: backslash, drive letter, whitespace padding, `normpath` climb. Symlink-out stays the resolve layer's job (said so in the docstring). |
| 16 | LOW | `chat_id` accepted `--100` and Arabic-Indic digits. | **FIXED.** `re.fullmatch(r"-?\d{1,20}", re.ASCII)` and `type(val) in (int, str)`. Found while fixing: Python's `\d` is Unicode-wide without `re.ASCII` — the first fix failed its own test. |
| 17 | LOW | Body cap used `repr`. | **FIXED.** `json.dumps(body, default=str)`. |
| 18 | LOW | `_lib/events.publish` POSTs to HA without `ha_post`. | **FIXED.** Wired; a denial is the same loud `False`. |
| 19 | test | Tautologies: `read_file relative ok`/`write ok`; `ha-shell-deny`/`ha-lock-deny`; `ha-dotdot-deny` failed on segment count, not the `..` branch. | **FIXED** the last (`/api/services/../unlock` now tests `..` independently). The first two are left: cheap, and each names a different tool/domain a future edit could break separately. |
| 20 | test | Untested claims in the decision record: whole-string bash, injection inside an allowlist hit, `df /;id`, HA encoding/case, automation body, missed domains, unicode headers, `run_agentic(gate=None)`, `ha.post` under `egress.active()`, telegram never-raises path. | **FIXED** all but two: `run_agentic(gate=None)` needs a live node (it is the same one-line assignment as `run`, which is tested); `egress.active()` path — the assert is the first statement of `ha.post`, before the branch, so both paths are the same line. Both stated rather than claimed. |

## Astra's retry (gpt-6-astra, effort high, 537 s)

Reviewed a **frozen copy of the original diff** (it noticed the tree changing
under it and said so). 18 findings, all marked CONFIRMED with reproductions
under `/tmp/policy-review-20260916/`. Eight converge with Grok (its #1–5,
#8, #13, #14 → Grok rows 3–8, 11, 9, 16 above — same fixes). The ten that
are new:

| # | sev | finding | disposition |
|---|---|---|---|
| A1 | HIGH | `${IFS}` variant of the metachar injection; the honest fix is "execute validated argv without a shell". | **COVERED** by the `;`/`$` refusal. The argv-exec design is right and is **PARKED**: `ranch_diag`'s gateway probe is a `bash -c '…'` literal, so no-shell is not a drop-in here. Recorded as the end-state. |
| A5 | HIGH | A domain check does not bound one call's blast radius: `light/turn_off` with `entity_id: all`. | **FIXED.** `all` (bare or in a list, top-level or under `target`) is refused on any service call. Tests: `ha-target-all-deny`, `ha-target-all-list-deny`, `ha-target-named-allow`. Per-caller service+target allowlist declined (same one-home reason as Grok #6). |
| A6 | HIGH | With the workspace as workdir, `write_file("harness/policy.py", …)` passes both layers and the next process imports the weakened default — git tracking does not protect the trust anchor from a filesystem write. | **FIXED.** `write_file` refuses any path with a `harness`, `_lib`, `.git` or `.claude` segment after normalisation (`WRITE_DENY_SEGMENTS`). Exact segments, not substrings (`notes/harnessed.md` still writes). Tests ×5. |
| A7 | MED | `str.split()` normalisation collapsed NBSP/VT/FF that bash reads as part of a word — the regex checked a command that was not the one executing. | **FIXED.** VT/FF are `Cc` and were already refused; NBSP and every other `Zs` now refused; only ASCII space runs normalise. Tests: `NBSP in a command denied`, `tab … denied like any other`. Quoted-arg collapse (`'a  b'` → `'a b'`) remains a documented limitation of regex-on-text. |
| A9 | MED | `send_proton.py`: a body piped on stdin reached `build_message()` but the gate saw `body=None` — cap bypass; files were re-read, so checked ≠ sent. | **FIXED.** The gate now validates the **message as built** (`msg.get_body()` plain/html, `msg["Subject"]`, `msg["From"]`). A CR/LF header the library refuses is now a clean `send_proton: refused …` exit, not a traceback. Exercised: dry-run with stdin body, and an injected subject under `--authorized`. |
| A11 | MED | Denial reasons interpolated argument VALUES (`recipient %r`, `path %r`, `chat_id %r`) and rode the bus via `_emit_denied`, contradicting "keys, never values". | **FIXED.** Every `_lib` reason names the rule only. Test: `reason-carries-no-value` across six tools with a poison marker; `test_core_policy` asserts the emitter is called once with keys only. (Harness reasons still echo the command back to the *model* — that is the retry loop, not the bus.) |
| A12 | MED | `DEFAULT` captured `spine=None` once; a gate built before the workspace was on `sys.path` denied spine tools forever. | **FIXED.** Re-discovery on each unknown-tool call while absent; absent stays deny. |
| A15 | LOW | `list_dir({"path": "\n"*5000})` read as "no path". | **FIXED.** Shape check precedes the empty-means-default shortcut. Test added. |
| A17 | LOW | A no-op `_emit_denied` left the "bus fires" test passing; bash tests were predicate-only. | **FIXED** the first (mock asserts the call, args, and no value). Execution-backed bash tests **DECLINED**: they would prove bash semantics, not the policy; the metachar refusal is deterministic. |
| A18 | LOW | The decision record claimed a CR/LF `to` "was a valid send"; the email library already raised `ValueError` before SMTP. | **FIXED** — the record now says the gate passed it and the library stopped it, uncaught and unpublished. Astra was right; I overclaimed. |

Astra also reported **none found** for: a filesystem escape through both layers on Linux (incl. encodings, NFC/NFD, Windows-looking names, external symlinks); any path that skips `_gate_ok`; regressions in the probe, wiki declarations, named HA callers, supergroup ids, non-str topics, or the `RecipientNotAllowed` re-wrap.

Not raised by either, found here: `build_message` can still raise `ValueError`
from the email library on a header the policy admitted — none constructed
after the `Cc/Zl/Zp` widening; left as a note, not a wrap.

## Score after the round

`policy_gate --selftest` 112/112 (36 → 59 → 101 → 112). `harness.selftest`
green with 72 policy checks. `test_core_policy` 33 OK. `ranch_diag
--selftest` 19/19. Live: `ranch_diag --force-local` on the real node, zero
denials, 17.0 s.

## Convergence

Two vendors, blind, one frozen diff. Independent agreement on: `match` vs
`fullmatch`; `\S+` swallowing metacharacters; the `df` regex in the one
unattended job; percent/case on HA domains; the automation-body hole;
`script`/`automation`/`alarm`/`cover` missing from the denylist; `BASH_ANY`
on the CLI and fitness gate; comma-in-display-name and list-`to`;
`chat_id` digit classes. That is nine converged findings — the design was
wrong in the same two places to both readers, which is the strongest
signal a panel gives.

## What this round says about the design

The defects clustered in exactly two places — the bash allowlist and the HA
surface — and both had the same shape: a *schema* claim sitting on top of a
*pattern* that could not carry it (`\S+`, a five-item denylist). The fix
was not more patterns; it was to make the policy refuse the class (shell
syntax, unknown encodings) and make the caller opt into exceptions by name.
That is the rule worth keeping: an allowlist entry is a literal, and
anything that lets it be less than a literal is a caller's explicit,
greppable choice.
