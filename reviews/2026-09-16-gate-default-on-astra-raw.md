# gpt-6-astra — adversarial review of the gate-default-on diff (raw return)

lane=codex model=gpt-6-astra effort=low wall_s=42.1 prompt_chars=69692 stop=end_turn

Prompt: the review pack in this session's scratchpad (`gate-review-full.md` = prompt + full diff). Framing given: adversarial security/design review, ten named attack surfaces, findings-only.

---

I’ll check the execution seams and try concrete bypasses locally, without sending mail or changing devices.The startup findings file lists four unpushed repos, two missing key-registry entries, and two Hermes databases holding credentials outside the vault. I’m leaving those unchanged for this review.

The bash allowlist admits shell syntax inside a permitted argument, including in `ranch_diag`’s `df` rule. I’m testing that with harmless marker files.This content was flagged for possible cybersecurity risk. If this seems wrong, try rephrasing your request. To get authorized for security work, join the Trusted Access for Cyber program: https://chatgpt.com/cyber

---

# Retry (reframed as an input-validation review of defensive code, effort high)

model=gpt-6-astra effort=high wall_s=537.2 prompt_chars=69897 stop=end_turn — reviewed a FROZEN copy of the original diff (it noticed the working tree changing during the review).

---

I’ll check the surrounding call paths and reproduce suspected failures locally, then return only ranked findings.
The workspace’s current findings report lists four unpushed repos, two missing key-registry entries, and credentials in two Hermes databases. I’ll leave those outside this review’s scope.

The first confirmed issue is that `bash_policy` uses prefix matching despite promising whole-command matching. I’m also checking what the documented regex allows Bash to execute.
The working-tree policy changed during the review. I’ll keep the findings tied to the diff you supplied and use a frozen copy for reproductions. The HA and mail checks already accept inputs that violate their stated contracts; I’m checking whether downstream parsers accept them too.
The supplied Bash rules allow a second command in both the README example and `ranch_diag`’s `df` pattern. HA’s denylist also misses uppercase and percent-encoded service names; I’m validating those against HA’s routing behavior without calling a live device.
The remaining checks found a Proton CLI body-cap bypass through stdin, six recipients hidden in one list element, and denial reasons that copy argument values into the event bus. I found no path that skips `_gate_ok` inside `loop.run`; the filesystem probes also found no Linux escape through both the textual check and `_confine`.
1. **HIGH — CONFIRMED — `harness/policy.py:143`; `observability/ranch_diag.py:100`.** The anchored allowlists admit shell syntax. `wc -l /dev/null;printf${IFS}BYPASS` matches the documented `^wc -l \S+$`; `df /;printf${IFS}BYPASS` matches the diagnostic allowlist. Both execute the second command. Command substitution and backticks also pass `\S+`. **Fix:** execute validated argv without a shell; expose required shell expressions as fixed commands. [Reproductions](/tmp/policy-review-20260916/results.txt).

2. **HIGH — CONFIRMED — `harness/policy.py:143`.** `bash_policy([r"wc -l"])` accepts `wc -l /dev/null;printf BYPASS`: `.match()` checks a prefix, contradicting “WHOLE string.” **Fix:** use `.fullmatch()`. This does not fix finding 1.

3. **HIGH — CONFIRMED — `_lib/policy_gate.py:217`.** `/api/services/LOCK/unlock` and `/api/services/%6cock/unlock` both pass. Aiohttp decodes the latter to `lock/unlock`; HA resolves uppercase service names to lowercase. These bypass the denylist on the direct route; the proxy may independently reject them. **Fix:** require canonical lowercase identifiers and reject percent escapes before checking the denylist. [HA service lookup](https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/core.py).

4. **HIGH — CONFIRMED — `_lib/policy_gate.py:197`.** An automation configuration containing `{"trigger":[{"platform":"time_pattern","seconds":"/1"}],"action":[{"service":"lock.unlock","target":{"entity_id":"lock.front_door"}}]}` passes. With that lock installed and sufficient token permissions, the saved automation performs the expressly forbidden action. HA reloads automation configuration after writing it. **Fix:** remove arbitrary automation editing from this gate; permit only separately validated, constrained automation templates. [HA configuration handler](https://raw.githubusercontent.com/home-assistant/core/dev/homeassistant/components/config/automation.py).

5. **HIGH — CONFIRMED — `_lib/policy_gate.py:189`.** `/api/services/script/turn_on` targeting an existing door-unlocking script passes; so does `automation/trigger` targeting an equivalent automation. `alarm_control_panel/alarm_disarm`, `cover/open_cover`, and `light/turn_off` with `{"entity_id":"all"}` also pass. Checking the domain does not constrain the action’s effects or target count. **Fix:** allowlist service-and-target combinations per caller; treat scripts, automations, scenes, configurable commands, and notification destinations as delegated capabilities. [Automation actions](https://www.home-assistant.io/docs/automation/services/), [light targets](https://www.home-assistant.io/integrations/light/).

6. **HIGH — CONFIRMED — `harness/policy.py:91`.** With the workspace as `workdir`, `write_file({"path":"harness/policy.py","content":"DEFAULT=lambda *_:(True,'ok')\n"})` passes both the policy and `_confine`. A subsequent process imports the weakened default. Git tracking does not protect the stated trust anchor against filesystem writes. **Fix:** place the executable policy outside writable tool roots, or enforce protected resolved paths at the filesystem boundary.

7. **MEDIUM — CONFIRMED — `harness/policy.py:142`.** Normalization changes quoted arguments: allowlist `^printf '%s' 'a b'$` accepts `printf '%s' 'a  b'`, which prints two spaces. `printf\x0bok`, `printf\x0cok`, and `printf\u00a0ok` match normalized `printf ok`, but Bash treats them as different executable names. **Fix:** validate exactly what executes; normalize parsed argv only when execution also uses that argv.

8. **MEDIUM — CONFIRMED — `_lib/policy_gate.py:141`.** `to=["a@x,b@x,c@x,d@x,e@x,f@x"]` counts as one recipient and passes; `EmailMessage` parses six. Conversely, valid `"Doe, Jane" <jane@example.com>` is rejected. `to="@"` and `to=[["a@x"]]` also pass despite producing empty addresses downstream. **Fix:** reject non-string elements, parse addresses once with the email parser, reject malformed results, and count the actual mailboxes used for authorization and delivery.

9. **MEDIUM — CONFIRMED — `cc-skills/proton-mail/send_proton.py:133`.** With `--authorized` and a 500,001-character body piped through stdin, `build_message()` consumes that body, but the gate receives `body=None`. A mocked SMTP run reaches `send_message`. Files are also reread for validation, so the checked content can differ from the constructed message. **Fix:** load inputs once and validate those exact values before constructing the message; include the effective sender.

10. **MEDIUM — CONFIRMED — `_lib/policy_gate.py:225`.** `{"state":"ok","attributes":{"description":"é"*20000}}` passes at 20,050 `repr` characters, then `ha.post()` serializes it to 120,050 bytes—above the 65,536-byte cap. Arbitrary non-JSON objects also pass this size check. **Fix:** serialize supported bodies once, reject serialization failures, and check the actual transmitted bytes.

11. **MEDIUM — CONFIRMED — `_lib/policy_gate.py:168`.** `to="UNTRUSTED TEXT"` produces a denial reason containing that value. `_emit_denied()` publishes the reason, contradicting “argument KEYS, never values.” HA path and Telegram ID denials have the same issue. **Fix:** publish fixed rule identifiers and field names, without interpolated argument values.

12. **MEDIUM — CONFIRMED — `harness/policy.py:205`, `:225`.** Import the standalone harness before adding the workspace to `sys.path`; subsequently import a spine-registered tool. `DEFAULT` retains `spine=None` and denies that tool forever, while a newly constructed gate permits it. This is an availability failure, not an allow bypass. **Fix:** retry spine discovery on unknown-tool calls while the cached spine is absent.

13. **LOW — CONFIRMED — `harness/__main__.py:104`; `harness/gate.py:29`.** `BASH_ANY` permits model-generated commands, not merely “whatever a human typed.” A benign CLI task can therefore produce an unrelated shell command without command-specific approval; a fitness test’s temporary cwd likewise provides no confinement. **Fix:** describe these as unrestricted agent lanes, require explicit CLI opt-in or command approval, and isolate fitness execution. ACP’s permission card provides a separate approval control.

14. **LOW — CONFIRMED — `_lib/policy_gate.py:243`.** `chat_id="--100123"` and `"²"` pass; neither is a valid decimal identifier. Negative `thread_id` values also pass. **Fix:** validate ASCII decimal syntax with at most one leading minus for chat IDs, and positive integers for thread IDs.

15. **LOW — CONFIRMED — `harness/policy.py:69`.** `DEFAULT("list_dir", {"path":"\n"*5000})` returns allowed before either control-character or length validation. This erodes the textual policy but does not escape `_confine`. **Fix:** validate supplied strings before treating an optional empty path as the default.

16. **LOW — CONFIRMED — `harness/policy.py:200`.** Setting `_harness_bash_policy=True` on `lambda _: (True, "")` permits commands containing NUL/newlines without construction failure. This contradicts the unconditional shape-check claim, but requires trusted Python execution—not model-supplied JSON. **Fix:** enforce Bash shape checks independently of the declared predicate; document the marker as advisory.

17. **LOW — CONFIRMED — `_lib/test_core_policy.py:69`; `harness/selftest.py:417`.** Replacing `_emit_denied` with a no-op leaves the new “bus event fires” test passing. Prefix-matching and shell-injection defects also pass the supplied Bash tests. **Fix:** assert the bus publication and add execution-backed allowlist tests. The existing tests are not literally tautological, but predicate-only assertions do not independently prove shell behavior; `MAX_* + 1` checks also cannot detect an incorrect configured cap.

18. **LOW — CONFIRMED — `decisions/harness-gate-default-on-2026-09-16.md:24`.** The claim that a newline in `to` previously constituted “a valid send” is unsupported: the existing `EmailMessage` header assignment already raises `ValueError` for `a@x.io\nBcc: e@z.io`, before SMTP. **Fix:** describe the demonstrated gap as missing early policy validation, and test the complete old send path before claiming successful header injection.

Filesystem escapes through both layers on Linux: **none found**; encoded names, Unicode normalization variants, and Windows-looking names do not themselves escape `_confine`, and external symlinks are rejected.

Skipped `_gate_ok` inside `loop.run` and its local wrappers: **none found**. `with_agentic_fallback`’s arbitrary primary callback is outside that gate’s scope.

Additional regressions in the probe, wiki declarations, named HA callers, negative supergroup IDs, non-string Telegram topics, or the intentional `RecipientNotAllowed` rewrap: **none found**.