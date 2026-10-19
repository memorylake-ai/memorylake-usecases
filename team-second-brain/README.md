# A team second brain with role-gated access

A runnable version of the MemoryLake use case
[*A Second Brain for Your Team, Not Just for You*](https://www.memorylake.ai/en/usecase/ai-second-brain-for-teams),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *Institutional knowledge scattered across documents, chat threads, and individual memories — invisible to AI,
inaccessible when people leave.* The page promises *a shared AI memory layer with role-gated access, so the knowledge that makes
your team effective is always available, to everyone who should have it.* This demo builds that for a five-person research lab
and checks both halves: everyone who should have it, has it; and it stays when the person who wrote it leaves.

**What this demo shows.** Thornfield Field Ecology Lab (fictional) keeps one shared memory with three projects. Mira, its most
senior researcher, leaves at the end of the month; Theo joined this week; Asha writes the grant reports.

| | Methods and decisions | Field notes | Funder reports |
|---|---|---|---|
| **Mira Okonkwo-Hale** — senior researcher, leaving | **write** | **write** | **write** |
| **Theo Lindqvist** — joined this week | read | **write** | — |
| **Asha Varga** — researcher, writes the grant reports | read | **write** | **write** |

Nobody is given that table directly. It comes out of one **custom role** and three **access groups**, written down in
[`data/team.json`](data/team.json):

- **Role “Lab researcher”** (everyone): reads the methods and the field notes. Built from nothing with `role create`,
  `permission rule create` and `role scope set`.
- **Access group “Field crew”** (everyone): adds write on the field notes.
- **Access group “Method stewards”** (Mira): adds write on the methods.
- **Access group “Grant team”** (Mira, Asha): adds the funder reports, read and write. Nobody else reaches them.

The demo builds it in a sandbox org team on your account and checks it:

1. **A custom role from permission rules.** `role create` starts with no permissions. `permission rule create` adds read and
   search on projects, plus `workspace:memory_search`. Search is refused without that workspace permission, even with every
   project action. `role scope set` limits the role to two projects, each with a read-only action list.
2. **Access groups for the rest.** `access-group create` with write permissions, `access-group scope set` with one project,
   `access-group member add`. `access-group explain` and `member explain` say where each permission comes from (`role`,
   `Grant team`, …) and through what each project is reached.
3. **The second brain fills up.** Each researcher writes their part with their own key. Mira writes the eight methods rules
   she carries in her head, Theo the week's field notes, Asha the funder report terms.
4. **27 calls, each answered by the server.** Every researcher runs `fact list`, `search` and `fact add` on every project with
   their own key. **27 of 27** answers match the model, in every run. Theo writing the methods: *You don't have the "Add Memory"
   permission on project "Methods and decisions".* Theo reading the funder reports: *… "Read Memory" permission on project
   "Funder reports".*
5. **A tidy-up of the role leaks a group's write.** A ticket drops the per-project action lists from the role's scope (the help
   calls them optional). The same two projects stay in scope. Theo can now **write the methods**. Nobody gave him that:
   `member explain` shows `project:mem_add` from the **Field crew**, whose scope is the field notes. With the action lists
   back, the write is refused again (6 of 6 runs).
6. **Mira leaves.** `member remove`: her key is refused **within a second** (`401 service account is disabled`) and she is in
   none of the three groups any more. Her 9 facts stay, and Theo's three questions about the lab's methods are each answered by
   one of them, at rank 1.
7. **The audit log.** `audit-log list` shows the role (`custom_role:create`), each permission rule (`policy:create`), the role
   scope changes, the groups, the members, the keys and the removal: 28 events per run.

**What MemoryLake enforces and what the demo adds.** Every allow and every refusal is the server's answer to a call made with
that researcher's key. The demo writes the model down, applies it, and compares. It adds one thing itself: a fact has no record
of who wrote it, so the demo keeps the ids each researcher's `fact add` returned (`.memorylake-demo/sandbox.json`). That is how
it can say an answer was *written by Mira*.

**Watch it run** (real recordings, unedited): [CLI demo, 2:20](https://github.com/memorylake-ai/memorylake-usecases/releases/download/team-second-brain-v1/team-second-brain-cli-demo.mp4) · [web demo, 2:45](https://github.com/memorylake-ai/memorylake-usecases/releases/download/team-second-brain-v1/team-second-brain-web-demo.mp4)
(GitHub serves release videos as downloads.)

## Prerequisites

1. **A MemoryLake account and API key of your personal team.** Sign up at [app.memorylake.ai](https://app.memorylake.ai),
   open **API Keys**, create a key and copy it. Details: [Authentication and API Keys](https://docs.memorylake.ai/authentication).
   An account may own **one org team** besides its personal team. The demo creates one and deletes it at the end. If your
   account already owns an org team, the run stops at step 2 and says so.
2. **The `memorylake` CLI** (v20261013 or later):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.sh | sh
   ```

   On Windows (PowerShell):

   ```powershell
   irm https://raw.githubusercontent.com/memorylake-ai/memorylake-cli/main/scripts/install.ps1 | iex
   ```
3. **Python 3.9+** (standard library only).

## Run it

```bash
git clone https://github.com/memorylake-ai/memorylake-usecases.git
cd memorylake-usecases/team-second-brain

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
python3 demo.py cleanup
```

A run takes about 90–105 seconds; nothing in it waits for an LLM. With `MEMORYLAKE_API_KEY` set, the demo logs into its own
CLI profile under `./.memorylake-demo/` (git-ignored) and leaves `~/.memorylake` alone. The sandbox owner key and the three
researchers' keys each live in their own profile there (`org/`, `members/<name>/`) and are only ever printed as `sk-` + 4
characters.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `python3 demo.py --reset` | Delete a sandbox team left by an earlier run first, then run (a run does this on its own) |
| `python3 demo.py audit` | Read the sandbox team's audit log again |
| `python3 demo.py report` | Print the last review (`out/second-brain-review.md`, `.json`) |
| `python3 demo.py cleanup` | Delete the sandbox team (its projects, role, groups, members and keys go with it) and the local profiles |
| `--show-json` | Print the raw JSON the CLI returns (keys masked) |

**Every run starts from nothing.** Teams, roles, groups and keys are one-shot, so a run that finds a sandbox team from an
earlier run deletes it first and says so. The run leaves the sandbox team in place so you can look around; `cleanup` deletes it.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — 27 calls, each researcher with their own key](https://github.com/memorylake-ai/memorylake-usecases/raw/main/team-second-brain/web/screenshot-matrix.png)

![Web companion — without action lists the Field crew's write lands on the methods; with them it is refused](https://github.com/memorylake-ai/memorylake-usecases/raw/main/team-second-brain/web/screenshot-leak.png)

![Web companion — Mira's key is refused, and what she wrote answers Theo's questions](https://github.com/memorylake-ai/memorylake-usecases/raw/main/team-second-brain/web/screenshot-departure.png)

Paste the key, press **Run demo**, and follow the nine steps: the sandbox team, the custom role, the three groups with
`member explain`, what each researcher wrote, the 27-call matrix filling in as each call returns, the tidy-up that leaks and
its fix, Mira's departure with Theo's three answers, and the audit log (**Read the audit log again** re-reads it). **Review**
shows the report. The terminal drawer shows every `memorylake` command as it runs, with the key it ran under.

## What happens, step by step

### 1. Connect

```
$ memorylake auth login --api-key sk-… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases
$ memorylake team get
  · connected to team “you@example.com” (personal) as tenant_owner
```

### 2. A sandbox org team

```
$ memorylake team create --name 'Thornfield Lab - second brain sandbox' --issue-api-key --api-key-name mlu-tsb-owner --idempotency-key mlu-tsb-20261013072622
$ memorylake auth login --api-key sk-8453… --base-url … --profile memorylake-usecases   # as sandbox owner
  · org team 6b40a795… “Thornfield Lab - second brain sandbox” — owner key mlu-tsb-owner (id 16013) is printed once; it now lives only in .memorylake-demo/org/
$ memorylake ws list --page-size 100   # as sandbox owner
  · sandbox team type “org”, your role there “tenant_owner”, workspace “Default Workspace” (ws-c4c27fb5…)
```

The owner key is in the `team create` reply only, so the demo writes the team id to `.memorylake-demo/sandbox.json` and logs
the key into its profile before doing anything else.

### 3. A custom role from permission rules

```
$ memorylake proj create --name 'Methods and decisions' --custom-id mlu-tsb-methods --description '…' --workspace ws-…   # as sandbox owner
$ memorylake proj create --name 'Field notes' --custom-id mlu-tsb-field-notes …
$ memorylake proj create --name 'Funder reports' --custom-id mlu-tsb-funder-reports …
$ memorylake role create --label 'Lab researcher' --description '…' --idempotency-key mlu-tsb-20261013072622-role   # as sandbox owner
  · custom role “Lab researcher” → key tenant_custom_fe91169c, permissions 0 (a custom role starts with nothing)
$ memorylake permission rule create --role tenant_custom_fe91169c --resource memorylake:project --action project:read,project:mem_read,project:mem_search,project:doc_read,project:doc_search,project:search --reason 'LAB-12: every researcher reads the methods and the field notes'
$ memorylake permission rule create --role tenant_custom_fe91169c --resource memorylake:workspace --action workspace:memory_search --reason 'LAB-12: …'
  · search needs a workspace permission as well as the project ones (workspace:memory_search)
$ memorylake role scope set tenant_custom_fe91169c --reason 'LAB-12: …' --scopes '[{"resource_type": "memorylake:project", "mode": "instances", "instances": [{"id": "proj-cd73…", "workspace_id": "ws-…", "actions": ["project:read", "project:mem_read", "project:mem_search", "project:doc_read", "project:doc_search", "project:search"]}, {"id": "proj-8837…", …}]}]'

  role tenant_custom_fe91169c “Lab researcher”
  permissions: project:doc_read, project:doc_search, project:mem_read, project:mem_search, project:read, project:search, workspace:memory_search
  project scope: instances — field-notes (6 actions, read-only); methods (6 actions, read-only)
  not in scope: funder-reports
```

The role key (`tenant_custom_<id>`) is what `member create --role` takes. `permission rule create` makes one rule per action.
There are only allow rules: to take a permission away, delete the rule.

### 4. Access groups add the rest

```
$ memorylake member create --name 'Mira Okonkwo-Hale' --role tenant_custom_fe91169c   # as sandbox owner
  · Mira Okonkwo-Hale (senior researcher, leaving at the end of the month): virtual member 0c74c317…, role tenant_custom_fe91169c
  …
$ memorylake access-group create --name 'Field crew' --description '…' --permission memorylake:project:project:read --permission memorylake:project:project:mem_read … --permission memorylake:project:project:mem_add
$ memorylake access-group scope set 4a4170ef4ef3 --reason 'LAB-15: the field crew adds field notes' --scopes '[{"resource_type": "memorylake:project", "mode": "instances", "instances": [{"id": "proj-8837…", "workspace_id": "ws-…"}]}]'
$ memorylake access-group member add 4a4170ef4ef3 0c74c317… e782f28d… 8e455484…
  … (Method stewards → methods, Grant team → funder reports)
$ memorylake access-group explain 0ca899617f63

  access-group explain “Grant team” — 2 member(s)
  permissions: project:doc_read, project:doc_search, project:mem_add, project:mem_read, project:mem_search, project:read, project:search
  projects it opens: funder-reports
$ memorylake api-key create --name mlu-tsb-mira --member 0c74c317…   # as sandbox owner
  · Mira Okonkwo-Hale: key mlu-tsb-mira (id 16014, sk-20e2…) → its own profile .memorylake-demo/members/mira/
  …
$ memorylake member explain 0c74c317…

  member explain Mira — where each project permission comes from
    project:mem_add        field-crew, method-stewards, grant-team
    project:mem_read       role, field-crew, method-stewards, grant-team
    …
  projects she reaches
    field-notes            via role, field-crew
    funder-reports         via grant-team
    methods                via role, method-stewards
```

An access group's scope does two things: it opens the project to its members (the funder reports are not in the role's scope at
all), and it is where the group's permissions apply.

### 5. The second brain fills up

```
$ memorylake fact add --project proj-2252… 'Re-run the power analysis whenever the sampling frame changes by more than 10 percent; …' …   # as mira

  Mira Okonkwo-Hale → Methods and decisions: 8 facts
    Re-run the power analysis whenever the sampling frame changes by more than 10 percent; the 2024 wetland…
    Pitfall traps are checked every 48 hours, never longer: after 72 hours the beetle counts drop by about …
    …
    We stopped using the Hollis creek transect in 2025: the landowner withdrew access, and the data from 20…
```

Theo writes four field notes, Asha four funder report terms, each with their own key.

### 6. 27 calls — every researcher × every project × read / search / write

```
$ memorylake fact list --projects proj-cd73… --page-size 50 --workspace ws-…   # as theo
$ memorylake search 'when do we redo the power analysis' --projects proj-cd73… --top-k 3 --workspace ws-…   # as theo
$ memorylake fact add --project proj-cd73… '2026-10-13: N3 water level up another 4 cm after the weekend rain.' --workspace ws-…   # as theo
…

  what the server allowed (R read · S search · W write; ✓ allowed, ✗ refused, ! differs from the model)
          methods         field-notes     funder-reports
  mira    R✓ S✓ W✓        R✓ S✓ W✓        R✓ S✓ W✓
  theo    R✓ S✓ W✗        R✓ S✓ W✓        R✗ S✗ W✗
  asha    R✓ S✓ W✗        R✓ S✓ W✓        R✓ S✓ W✓
  · 27/27 calls answered as the model says; refusals: HTTP 403 ACCESS_DENIED
  · e.g. theo → write methods: “You don't have the "Add Memory" permission on project "Methods and decisions". Ask a team owner or admin to grant it.”
  · e.g. theo → read funder-reports: “You don't have the "Read Memory" permission on project "Funder reports". Ask a team owner or admin to grant it.”
```

### 7. A tidy-up of the role scope lets a group's write land on the methods

```
  · ticket LAB-19: tidy up the role scope - the help says per-project actions are optional
$ memorylake role scope set tenant_custom_fe91169c --reason 'LAB-19: …' --scopes '[{"resource_type": "memorylake:project", "mode": "instances", "instances": [{"id": "proj-cd73…", "workspace_id": "ws-…"}, {"id": "proj-8837…", "workspace_id": "ws-…"}]}]'
$ memorylake fact add --project proj-cd73… '2026-10-13: N3 water level up another 4 cm after the weekend rain.' --workspace ws-…   # as theo
  · Theo Lindqvist writes to Methods and decisions: rc 0 — added fact-4f3f0565…
$ memorylake member explain e782f28d…   # as sandbox owner
  · member explain: project:mem_add comes from field-crew; methods is reached through role
  · the field crew's write was meant for the field notes: without action lists on the role's projects, it lands on every project the role reaches
  · put the action lists back
$ memorylake role scope set tenant_custom_fe91169c --reason 'LAB-19: … (reverted)' --scopes '[… "actions": ["project:read", …] …]'
$ memorylake fact add --project proj-cd73… '…' --workspace ws-…   # as theo
  · Theo Lindqvist writes to Methods and decisions now: HTTP 403 “You don't have the "Add Memory" permission on project "Methods and decisions". …” · field notes: rc 0
$ memorylake fact delete --project proj-cd73… fact-4f3f0565… --workspace ws-…   # as sandbox owner
```

`role scope set --help` says `actions` is optional and defaults to *every action the role allows on it*. The role allows only
reads. In practice an instance without `actions` gets every action the member holds **from anywhere**, access groups included:
the Field crew's `project:mem_add`, scoped to the field notes, applies to the methods as soon as the role's methods instance
carries no action list. `member explain` names the group as the source but not the project it lands on. Keep an action list on
every instance.

### 8. Mira leaves: her key stops, her knowledge stays

```
  · ticket LAB-21: Mira leaves the lab on 2026-10-31
$ memorylake fact list --projects proj-cd73… --page-size 50 --workspace ws-…   # as mira
  · Mira Okonkwo-Hale reads Methods and decisions: rc 0, 9 facts
$ memorylake member remove 0c74c317…   # as sandbox owner
$ memorylake fact list --projects proj-cd73… --page-size 50 --workspace ws-…   # as mira
  · the same call 0.5s later: HTTP 401 “API key was rejected by the server (authentication_error): service account is disabled”
$ memorylake access-group member list 4a4170ef4ef3   # as sandbox owner
  …
  · her groups after `member remove`: field-crew 2 member(s), method-stewards 0 member(s), grant-team 1 member(s) — she is in none of them any more
  · Methods and decisions still holds 9 facts, 9 of them written by Mira Okonkwo-Hale
$ memorylake search 'when do we redo the power analysis' --projects proj-cd73… --top-k 3 --workspace ws-…   # as theo
…

  Theo Lindqvist asks the second brain (search Methods and decisions, rank 1)
    ✓ when do we redo the power analysis
      → Re-run the power analysis whenever the sampling frame changes by more than 10 percent; the 2024 wet…
        written by Mira Okonkwo-Hale
    ✓ how long can pitfall traps stay out
      → Pitfall traps are checked every 48 hours, never longer: after 72 hours the beetle counts drop by ab…
        written by Mira Okonkwo-Hale
    ✓ why don't we use the Hollis creek transect any more
      → We stopped using the Hollis creek transect in 2025: the landowner withdrew access, and the data fro…
        written by Mira Okonkwo-Hale
  · 3/3 answered from memory; 3 of the answers were written by Mira Okonkwo-Hale, whose key no longer works
```

Facts belong to the project, not to the member who wrote them. `member remove` disables the member's keys and takes them out
of every access group; it does not touch what they wrote.

### 9. The audit log

```
$ memorylake audit-log list --page-size 100   # as sandbox owner

  time (UTC)           action                        by          target
  2026-10-13 07:26:24Z tenant:create                 niuniu      Thornfield Lab - second brain sandbox
  2026-10-13 07:26:29Z custom_role:create            niuniu      Lab researcher
  2026-10-13 07:26:30Z policy:create                 niuniu      tenant_custom_fe91169c|memorylake:project|project:search|50
  …
  2026-10-13 07:26:31Z member:set_scope              niuniu      Lab researcher
  2026-10-13 07:26:36Z access_group:create           niuniu      Field crew
  2026-10-13 07:26:37Z access_group:scope_update     niuniu      Field crew
  2026-10-13 07:26:38Z access_group:member_add       niuniu      Field crew
  …
  2026-10-13 07:27:30Z member:set_scope              niuniu      Lab researcher
  2026-10-13 07:27:34Z member:set_scope              niuniu      Lab researcher
  2026-10-13 07:27:39Z member:remove                 niuniu      Mira Okonkwo-Hale
  · 28 event(s) since the sandbox was created: tenant:create ×1, custom_role:create ×1, policy:create ×7, member:set_scope ×3, member:manage_virtual ×3, access_group:create ×3, access_group:scope_update ×3, access_group:member_add ×3, service_account:create ×3, member:remove ×1
  · the custom role is `custom_role:create`, each permission rule a `policy:create` (one per action), and a role scope change is logged as `member:set_scope` with the role as its target
  · group membership events name the group, not who joined it (3 event(s))
```

The tidy-up and its revert are the two `member:set_scope` events on *Lab researcher* at 07:27:30 and 07:27:34. Mira leaving
the groups through `member remove` has no `access_group:member_remove` event of its own.

### Cleanup

```
$ memorylake team delete --confirm 6b40a795…   # as sandbox owner
  · deleted the sandbox team 6b40a795… — its projects, role, groups, members and keys go with it
$ memorylake team get   # as sandbox owner
  · its owner key now: HTTP 401 “service account is disabled”
```

Deleting the team frees the account's org-team slot at once; the next run can create a new one.

## Measured

Six full runs on a free account (2026-10-13, CLI v20261013), each in a fresh sandbox team: three from the terminal (one of
them with `--reset`), one from the web companion, and the two recordings (CLI and web).

| | every run |
|---|---|
| matrix | **27/27** as the model says (6 of 6) |
| refusals | `403 ACCESS_DENIED`, the permission named (*Read Memory*, *Search Memories*, *Add Memory*) |
| role scope without action lists | Theo writes the methods, `mem_add` from **Field crew** (6 of 6) |
| action lists back | Theo's write to the methods `403`, field notes still writable (6 of 6) |
| Mira's key refused after `member remove` | 0.5–0.8 s (`401 service account is disabled`) |
| Mira still in a group afterwards | 0 of 3 groups (6 of 6) |
| Theo's questions answered at rank 1 | 3/3, all three by facts Mira wrote (6 of 6) |
| audit events per run | 28 |
| whole run | 90–104 s (the five that were timed) |

## Wiring it into your own team

- **Define roles from rules, not from the built-ins.** `role create` + `permission rule create` gives a role exactly what you
  list. A built-in `tenant_member` may add memory anywhere.
- **Give search its workspace permission.** `search` needs `workspace:memory_search` on top of the project actions; without it,
  every search is `403 Search Workspace Memories`.
- **Use access groups for "who sees what", and keep action lists on every scope instance.** A role instance without `actions`
  picks up permissions from every group the member is in. After any scope change, run your matrix (or at least `member explain`
  and `access show project <id>`) again.
- **Write knowledge to projects, not to people.** What a member wrote stays when the member is removed; their keys do not.
  Record the writer yourself if you need it — the fact does not.
- **Org teams are limited per account** (one besides the personal team on a free account). Use one org team per environment.

## If something goes wrong

- **`tenant creation limit reached: limit=1 current=1`** at step 2: your account already owns an org team. Delete it with its
  owner key (`memorylake team delete --confirm <team-id>`) or use another account.
- **`You don't have the "Search Workspace Memories" permission`**: the role lacks `workspace:memory_search`. The demo adds it
  with a second `permission rule create`.
- **A researcher at `read` can write**: an instance in the role's scope has no `actions`, and one of their groups has
  `project:mem_add`. Step 7 shows it; `member explain <id>` names the group.
- **`project instances need their workspace_id`**: a scope entry for a project without `workspace_id`. The demo always sends it.
- **`.memorylake-demo/org/` was deleted but the sandbox team still exists**: the owner key is gone with it, and it is only
  printed once. Delete the team in the MemoryLake console.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Calls whose refusal is the expected answer, and read-only
  commands, retry four times. Otherwise run again; it starts over.

## Files

```
demo.py                  the runner; prints every CLI command it runs and the key it ran under (and emits events for the web app)
data/team.json           the lab, its three projects, what each researcher knows, the role, the three groups, the tidy-up ticket and the departure
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     second-brain-review.md / .json (role, groups, matrix, tidy-up, departure, audit log), runs.jsonl (git-ignored)
```

The lab, its researchers and everyone in it are fictional.
