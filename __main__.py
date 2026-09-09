"""CLI:  cd ~/Github/CC && /usr/bin/python3 -m harness [options] "task"

Modes:
  "task" --workdir DIR      run the agentic floor on a task (default mode)
  --probe MODEL             measure which dialect a model actually passes
  --selftest                offline selftest (no network)

The probe is how a registry entry earns ``status: proven`` — it runs one tiny
tool task per dialect against the live node and reports PASS/FAIL + latency.
"""
import argparse
import json
import sys

from . import dialects, loop, registry, tools_local


def probe(model):
    """One tiny tool task per dialect; report what the model actually does."""
    results = {}
    for dia in dialects.DIALECTS:
        got = {}
        mark = loop.Tool(
            "mark", "record a token",
            {"type": "object", "properties": {"token": {"type": "string"}},
             "required": ["token"]},
            lambda a: got.update(a) or "recorded")
        try:
            answer, meta = loop.run(
                'Call the tool `mark` with args {"token": "7A3"}. After it '
                "returns, reply exactly: done.",
                [mark], model=model, dialect=dia, max_turns=3, call_timeout=180)
            passed = got.get("token") == "7A3"
            results[dia] = {"pass": passed, "turns": meta["turns"],
                            "wall_s": round(meta["wall_s"], 1),
                            "answer": answer[:60]}
        except loop.HarnessError as e:
            results[dia] = {"pass": False, "error": str(e)[:120]}
    prof = registry.profile_for(model)
    print("probe %s  (registry says: %s, status=%s)"
          % (model, prof["dialect"], prof.get("status")))
    for dia, r in results.items():
        flag = "PASS" if r.get("pass") else "FAIL"
        print("  %-9s %s  %s" % (dia, flag,
                                 json.dumps({k: v for k, v in r.items()
                                             if k != "pass"})))
    best = [d for d, r in results.items() if r.get("pass")]
    if best and prof["dialect"] not in best:
        print("  ⚠ registry assigns %r but only %s passed — update registry.json"
              % (prof["dialect"], best))
    return 0 if best else 1


def main(argv=None):
    ap = argparse.ArgumentParser(prog="harness", description=__doc__)
    ap.add_argument("task", nargs="?", help="agentic task to run")
    ap.add_argument("--workdir", default=".", help="jail for the floor toolset")
    ap.add_argument("--model", default=None, help="Ollama tag (default: node resident)")
    ap.add_argument("--dialect", default=None, choices=dialects.DIALECTS)
    ap.add_argument("--max-turns", type=int, default=loop.DEFAULT_MAX_TURNS)
    ap.add_argument("--timeout", type=int, default=loop.DEFAULT_WALL_BUDGET,
                    help="wall budget seconds")
    ap.add_argument("--no-bash", action="store_true", help="drop run_bash from the set")
    ap.add_argument("--probe", metavar="MODEL", help="dialect-probe a model")
    ap.add_argument("--gate", metavar="MODEL",
                    help="multi-step agentic gate (mechanical fs scoring)")
    ap.add_argument("--trials", type=int, default=3, help="gate trials")
    ap.add_argument("--provider", default="local", choices=("local", "fireworks"),
                    help="with --gate: which transport serves the model "
                         "(fireworks = the hosted open-weight lane, spends cents)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="print meta as JSON")
    args = ap.parse_args(argv)

    if args.selftest:
        from . import selftest
        return selftest.main()
    if args.probe:
        return probe(args.probe)
    if args.gate:
        from . import gate as gate_mod
        transport = None
        if args.provider == "fireworks":
            from . import fireworks_transport
            transport = fireworks_transport.transport
        verdict = gate_mod.gate(args.gate, trials=args.trials, transport=transport)
        verdict["provider"] = args.provider
        print(json.dumps(verdict, indent=2))
        return 0 if verdict["verdict"] == "PASS" else 1
    if not args.task:
        ap.error("a task (or --probe/--selftest) is required")

    tools = tools_local.standard_tools(args.workdir, bash=not args.no_bash)
    system = ("You are a capable agent working inside the directory %s. "
              "Use the tools to complete the task, then give a short final "
              "answer stating what you did." % args.workdir)

    def on_event(kind, detail):
        print("  [%s] %s" % (kind, detail), file=sys.stderr)

    try:
        answer, meta = loop.run(args.task, tools, model=args.model,
                                system=system, dialect=args.dialect,
                                max_turns=args.max_turns, deadline=args.timeout,
                                on_event=on_event)
    except loop.HarnessError as e:
        print("HARNESS REFUSED: %s" % e, file=sys.stderr)
        if args.json:
            print(json.dumps(e.meta, default=str))
        return 1
    print(answer)
    if args.json:
        print(json.dumps(meta, default=str))
    else:
        print("\n-- %s @%s dialect=%s turns=%d calls=%s wall=%.1fs" % (
            meta["model"], meta["host"], meta["dialect"], meta["turns"],
            [c for c, _ in meta["tool_calls"]], meta["wall_s"]),
            file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
