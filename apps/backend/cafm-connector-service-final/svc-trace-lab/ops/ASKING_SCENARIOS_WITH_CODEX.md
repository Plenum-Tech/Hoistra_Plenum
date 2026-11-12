# Asking Hoistra scenario questions and reading them in Trace Lab

Instructions for a coding agent (Codex). Give this whole file to the agent. It explains how to ask Hoistra's orchestrator a
batch of scenario questions so that every conversation lands in Trace Lab's **Sessions** page with its expected outcome,
ready to be scored and annotated. Tested end to end on 12 Nov 2026: 10 single-turn and 5 multi-turn scenarios (23 turns).

## What you are doing

```
scenarios.jsonl (questions + expected outcome per turn, written from SPEC.md)
   │  1. import into Trace Lab
   ▼
Hoistra orchestrator  ◄── 2. ask each turn, session id scn__<run tag>__<scenario id>__<attempt>
   │  the chat records every turn and its trace
   ▼
Trace Lab  ◄── 3. file those conversations as sessions   4. score them   5. read the results
```

1. Read the specification into Trace Lab and import the scenario file.
2. Ask every turn of every scenario to the orchestrator, in order, with the scenario's session id.
3. Tell Trace Lab to file exactly those conversations as sessions under a run tag.
4. Run the logic evaluation (free). The model judges are optional and cost money.
5. Read the verdicts back, then open Trace Lab → **Sessions** → filter by the run tag.

## Before you start

You need:

- `HOISTRA_URL`: the Hoistra address, e.g. `https://<hoistra host>` (deployed) or `http://localhost:3001` (local gateway).
- `HOISTRA_EMAIL` and `HOISTRA_PASSWORD`: a Hoistra account on that environment. Read them from environment variables.
  **Never write them to a file, a log, a commit or a message.**
- That account needs a **Trace Lab curator role** (company admin, platform team or consultant) to import scenarios,
  file sessions and evaluate. The user-experience and domain judges need the platform team or consultant role.
- Trace Lab deployed with migrations 0014 and 0015 (sessions, scenarios, labels). If `/eval/spec` returns 404, it is not.

Asking writes to that environment's database: every turn is recorded as a chat turn with its spans and usage, and the
model calls are billed. Ask only what you were asked to ask.

## The rules that matter

| Rule | Why |
|---|---|
| Session id is exactly `scn__<run tag>__<scenario id>__<attempt>`, e.g. `scn__baseline_v0__SD-001__1` (at most 120 characters) | Trace Lab reads the run tag, scenario and attempt out of it. Any other id is never filed as a session. |
| Run tag: 1 to 40 letters, digits, dots, dashes or **single** underscores, starting with a letter or digit (`baseline_v0`, `postfix_v1`) | `__` separates the parts of the session id. |
| Scenario id: 1 to 60 of the same characters, e.g. `SD-001`, `MT-014` | Same reason. |
| Send each turn's `message` **exactly** as written in the scenario file | A trace whose question differs from its turn is not scored ("question differs"). Case, spaces and a closing `?` do not matter. |
| A multi-turn scenario uses **one** session id for all its turns, asked in order, one after the other | The orchestrator keeps the conversation's context by session id. |
| Asking a scenario again: increase the attempt (`__2`) | The newest attempt is the one scored and compared. |
| Import the scenarios **before** filing the sessions | A conversation whose scenario is not stored yet waits ("waiting") and is filed on a later read. |
| Ask one turn at a time; allow up to 600 s per turn | Turns take 3 to 50 s; the gateway drops a response idle for 300 s. |

## The scenario file (JSONL, one scenario per line)

```json
{"scenario_id": "SPEC-01", "type": "single_domain", "role": "compliance_officer", "space": "comp",
 "spec_sections": ["s_1_overdue_reported_as_none"], "scope": {}, "faults": [], "grounded_on": [],
 "domain_rules": [{"id": "S-1", "text": "When a certificate or inspection is past its due date, the answer states the due date and how many days overdue, and names any work order already raised for it. Never \"nothing is due\"."}],
 "turns": [{"message": "Which certificates are overdue, and how many days overdue is each?",
   "expected": {"plan": [{"call_id": "c1", "domain": "compliance", "tool": null, "params": {}, "depends_on": []},
                         {"call_id": "c2", "domain": "wo_engine", "tool": null, "params": {}, "depends_on": ["c1"]}],
                "failure_handling": null, "answer_format": "success",
                "must_include": [], "must_not_include": ["nothing is due"],
                "answer": "A short list, one line per overdue certificate: certificate, building, due date, days overdue, and the work order already raised for it (or that none has been)."}}]}
```

(Shown over several lines for reading; in the file each scenario is **one** line.)

| Field | What to put |
|---|---|
| `type` | `single_domain`, `multi_domain`, `multi_turn` or `failure` (a failure scenario needs `failure_handling` or `faults`) |
| `role` | `general_user`, `portfolio_manager`, `property_manager` or `compliance_officer`. Stored as a label only: the question is asked as whoever signs in. |
| `space` | `ops`, `comp`, `assets` or `full` |
| `spec_sections` | Section ids of the loaded specification: `GET /eval/spec` lists them (`s_1_overdue_reported_as_none` … `s_11_incorrect_date_range_filtering` for today's SPEC.md). The scenario is flagged out of date when one of them changes. |
| `domain_rules` | The rule text the domain judge checks the answer against. Copy it from SPEC.md. |
| `turns` | 1 to 6 turns, each `{message, expected}` |
| `expected.plan` | The calls the turn should make, in order: `domain` is `compliance`, `wo_engine`, `contract_performance` or `energy_intelligence`; `depends_on` names calls that must finish first. For a follow-up turn that should answer from the previous turn without a new call, use `"plan": []`. |
| `expected.failure_handling` | `null`, or `{"state": "<partial_failure, total_failure, authorization_failure, evidence_conflict, zero_results, not_recorded, unavailable or tool_failed>", "failed": ["c2"]}` |
| `expected.answer_format` | `success`, `partial_success`, `denial` or `failure` |
| `expected.must_include` / `must_not_include` | Phrases the answer must / must not contain |
| `expected.answer` | The expected answer **in words**: its shape and what it must say. Write it from SPEC.md, never from an answer the model gave. Do not invent record values; only put values you read from the company's records. |

How to write the expected outcome from SPEC.md: for each rule the scenario tests, say what a correct answer must do
(states the due date and days overdue; groups by building and lists every record; never says the data is unavailable),
which engine must be called, and what must never appear. Multi-turn: say what must carry over from the turn before
(same building, same vendors, same count).

## The calls

All under `HOISTRA_URL`. `LAB = /backend/trace-lab/api/trace-lab`. Every call after sign-in sends
`Authorization: Bearer <access token>`.

| Step | Call | Notes |
|---|---|---|
| Sign in | `POST /backend/ops-intelligence/api/auth/login` `{"email", "password"}` | Token is at `tokens.access_token`. HTTP 202 = the account must enter an emailed code first (sign in once in the browser). 401 = wrong email or password; repeated 401s lock the account, so stop and ask. |
| Read the specification | `POST LAB/eval/spec/reload` | Then `GET LAB/eval/spec` lists the section ids. |
| Import scenarios | `POST LAB/eval/scenarios/import` (multipart, field `file`; `?dry_run=true` checks without storing) | A bad line refuses the whole file and names every bad line (422). |
| Ask one turn | `POST /backend/deep-agents/api/workflow/run-stateful` `{"message": "<turn text>", "session_id": "scn__..."}` | Reply: `answer`, `success`, `error`, `tool_calls`, `route_metadata`. |
| File the sessions | `POST LAB/eval/ingest` `{"run_tag": "<tag>", "sessions": {"<session id>": "<scenario id>", ...}}` | Files exactly those conversations (up to 500). Reply counts: `sessions`, `traces`, `waiting` (scenario not imported), `other_company`, `failed`. |
| Score | `POST LAB/eval/evaluate` `{"run_tag": "<tag>", "levels": ["logic"], "skip": []}` | Repeat until `remaining - left_for_later` is 0. Add each reply's `failed_traces` to `skip`. `levels` may add `"ux"`, `"domain"` (model judges; budget per run tag; platform team / consultant only). |
| Read results | `GET LAB/eval/sessions?run_tag=<tag>&limit=200`, `GET LAB/eval/sessions/<session id>` | Each trace carries its scores and the scenario's expected outcome. |
| Compare two runs | `GET LAB/eval/compare?base=<tag>&head=<tag>` | Per scenario: fixed, regressed, same. |

## Reference script (Python 3, standard library only)

```python
"""Ask a scenario file to Hoistra and file it in Trace Lab.
Env: HOISTRA_URL, HOISTRA_EMAIL, HOISTRA_PASSWORD. Usage: python ask_scenarios.py <scenarios.jsonl> <run_tag> [attempt]"""
import json, os, sys, time, urllib.error, urllib.request, uuid

BASE = os.environ["HOISTRA_URL"].rstrip("/")
LAB = BASE + "/backend/trace-lab/api/trace-lab"
FILE, RUN_TAG = sys.argv[1], sys.argv[2]
ATTEMPT = int(sys.argv[3]) if len(sys.argv) > 3 else 1


def http(method, url, token=None, body=None, raw=None, ctype="application/json", timeout=600):
    headers = {"Content-Type": ctype}
    if token:
        headers["Authorization"] = "Bearer " + token
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method, headers=headers), timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        text = (e.read() or b"")[:1500].decode("utf-8", "replace")
        try:
            return e.code, json.loads(text)
        except ValueError:
            return e.code, {"text": text}


def must(step, st, out):
    if st >= 400:
        raise SystemExit(f"{step} failed: HTTP {st} {json.dumps(out)[:400]}")
    return out


st, out = http("POST", BASE + "/backend/ops-intelligence/api/auth/login",
               body={"email": os.environ["HOISTRA_EMAIL"], "password": os.environ["HOISTRA_PASSWORD"]})
token = (out.get("tokens") or {}).get("access_token")
if st != 200 or not token:
    raise SystemExit(f"sign-in failed: HTTP {st} (202 = verification code needed; 401 = wrong login, do not retry)")
print("signed in")

spec = must("spec", *http("POST", LAB + "/eval/spec/reload", token))
print("specification", spec.get("spec_version"), "-", spec.get("sections"), "sections")

data = open(FILE, "rb").read()
b = "----" + uuid.uuid4().hex
form = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"scenarios.jsonl\"\r\n"
        f"Content-Type: application/x-ndjson\r\n\r\n").encode() + data + f"\r\n--{b}--\r\n".encode()
imp = must("import", *http("POST", LAB + "/eval/scenarios/import", token, raw=form, ctype=f"multipart/form-data; boundary={b}"))
print("imported", imp.get("scenarios"), "scenarios: new", imp.get("added"), "updated", imp.get("new_versions"))

scenarios = [json.loads(line) for line in data.decode().splitlines() if line.strip()]
named, replies = {}, []
for s in scenarios:
    sid = f"scn__{RUN_TAG}__{s['scenario_id']}__{ATTEMPT}"
    named[sid] = s["scenario_id"]
    for n, turn in enumerate(s["turns"], start=1):
        t0 = time.time()
        st, out = http("POST", BASE + "/backend/deep-agents/api/workflow/run-stateful", token,
                       body={"message": turn["message"], "session_id": sid})
        replies.append({"scenario_id": s["scenario_id"], "turn": n, "session_id": sid, "question": turn["message"],
                        "http": st, "seconds": round(time.time() - t0, 1), "success": out.get("success"),
                        "answer": out.get("answer"), "error": out.get("error") or (out if st >= 400 else None)})
        print(f"{s['scenario_id']} turn {n}: HTTP {st} in {replies[-1]['seconds']}s")
        if st >= 400:
            break                       # a broken turn: do not ask the follow-ups without it

ing = must("ingest", *http("POST", LAB + "/eval/ingest", token, body={"run_tag": RUN_TAG, "sessions": named}, timeout=300))
print("filed:", ing.get("sessions"), "sessions,", ing.get("traces"), "traces; waiting", ing.get("waiting"), "failed", ing.get("failed"))

skip = []
for _ in range(100):
    ev = must("evaluate", *http("POST", LAB + "/eval/evaluate", token,
                                body={"run_tag": RUN_TAG, "levels": ["logic"], "skip": skip}, timeout=300))
    skip += [t for t in ev.get("failed_traces") or [] if t not in skip]
    left = (ev.get("remaining") or 0) - (ev.get("left_for_later") or 0)
    if left <= 0 or ev.get("capped") or (not ev.get("traces") and not ev.get("failed_traces")):
        break

listed = must("sessions", *http("GET", LAB + f"/eval/sessions?run_tag={RUN_TAG}&limit=200", token))
for sess in listed.get("sessions") or []:
    print(sess.get("scenario_id"), "logic:", ((sess.get("scores") or {}).get("logic") or {}).get("verdict"))
json.dump(replies, open(f"replies_{RUN_TAG}.json", "w"), indent=2)
print(f"saved replies_{RUN_TAG}.json. Open Trace Lab > Sessions and filter run tag {RUN_TAG}.")
```

Run it:

```sh
export HOISTRA_URL=https://<hoistra host>
export HOISTRA_EMAIL=<account email>
read -rs 'HOISTRA_PASSWORD?Password: '; export HOISTRA_PASSWORD; echo    # zsh; in bash: read -rsp 'Password: ' HOISTRA_PASSWORD
python3 ask_scenarios.py scenarios.jsonl baseline_v0
```

## Reading the results

- Trace Lab → **Sessions** → filter the run tag. Each session shows the scenario's expected outcome on top and a tab
  per turn: the question, the answer, how it ended, and the whole trace (spans and generations). People add notes on any
  span, a comment on the turn, and mark each failure mode present or absent.
- **Evaluate** on the Sessions page runs the logic checks and, for the platform team and consultant, the user-experience
  and domain judges. **Compare** puts two run tags side by side (baseline vs. after a fix).
- The logic check compares the calls made with the expected plan (coverage, order, scope, retries, context passed). It
  cannot see a contradiction between turns, an answer that ignores the previous turn, or wrong figures: the judges and
  the people reading the session catch those.

## When something goes wrong

| You see | It means | Do |
|---|---|---|
| Sign-in 401 | Wrong email or password | Stop; do not retry (the account locks). Ask for the right login. |
| Sign-in 202 | The account must enter an emailed code | Sign in once in the browser, then run again. |
| 403 `curators_only` / `platform_only` | The account lacks the Trace Lab role | Ask a company admin or the platform team for the role. |
| 404 on `/eval/...` | Trace Lab on that environment is older than migration 0014 | Ask for Trace Lab to be updated. |
| 422 on import | A scenario line is invalid | Fix the lines named in `errors`. |
| ingest `waiting` > 0 | Those scenarios are not imported | Import them, then call ingest again. |
| A score "not evaluated: question differs" | The text asked is not the turn's `message` | Ask the exact text (new attempt). |
| A score "not evaluated: fault not injected" | A failure scenario whose failure never happened | Fault injection is not built yet; leave it. |
| Plan coverage fails on a follow-up that answered from the previous turn | The expected plan asked for a call the turn did not need | Use `"plan": []` for such turns. |
