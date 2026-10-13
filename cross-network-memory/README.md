# Shared memory for agents on different networks

A runnable version of the MemoryLake use case
[*Give Agents That Communicate Across Networks Shared Memory With Auth and Audit*](https://www.memorylake.ai/en/usecase/memory-for-agents-that-talk-to-each-other-across-networks),
built entirely on the [`memorylake` CLI](https://github.com/memorylake-ai/memorylake-cli).

**The problem.** *Agent A from your service needs to talk to Agent B from a partner service. They exchange messages but have no
shared memory store.* The page promises **federated memory access** (each agent authenticates with scoped permissions),
**cross-organization memory contracts** (what one agent can read or write in another's memory) and a **per-network audit
trail**. This demo builds that for two companies' agents and then changes the contract under them.

**What this demo shows.** Harborline Freight (a fictional freight forwarder) runs a dispatch agent. Kestrel Customs Brokerage
(a fictional customs broker) runs a broker agent on its own network. They share one memory, the shipment contract; Harborline
keeps a second memory to itself:

| | Shipment contract | Harborline internal | allowed to call from |
|---|---|---|---|
| **harborline-dispatch** — books carriers, tracks shipments | **write** | **write** | this machine (Harborline's network) |
| **kestrel-broker** — files customs entries, records holds | **write** | — | Kestrel's egress, `198.51.100.0/24` |

That table is [`data/contract.json`](data/contract.json). `198.51.100.0/24` is a documentation range (RFC 5737): no real machine
calls from it, so wherever you run the demo, you are outside Kestrel's network until the contract says otherwise.

1. **A sandbox org team.** A personal team cannot have members; an org team can. `team create --issue-api-key` makes one on
   your account. Each agent is a virtual member (`member create`) limited to the memory the contract names (`member scope set`).
2. **Each key pinned to its agent's network.** `api-key create --member … --allowed-ip 198.51.100.0/24`. Used from your
   machine, Kestrel's key is refused at `auth login`, before it touches any memory: `403` *IP 103.151.•.• is not allowed to
   access*. The refusal names the address the server saw, so the demo pins Harborline's key to exactly that address. No
   IP-echo service is involved.
3. **Dispatch writes, the partner joins.** Harborline's agent writes four shipments to the contract and three notes to its
   internal memory. The contract then adds your machine to Kestrel's allowlist (`api-key update --allowed-ip`), and **the
   same key that was refused now logs in**. The broker reads the shipments, records a customs hold, and is refused on
   Harborline's internal memory (`403 ACCESS_DENIED`, *You don't have the "Read Memory" permission*). Harborline asks
   *"Is anything holding HX-4471 at customs?"*, and Kestrel's hold comes back **#1** in every run.
4. **A key may have leaked: suspend it, don't revoke it.** `api-key update --status disabled`. The next call with the key
   is refused (`401 service account is disabled`, within 1.1 s). What the broker wrote stays readable to Harborline.
   `--status enabled` brings back **the same key**, so Kestrel has nothing to re-deploy. Rotating or revoking would mean a
   new key.
5. **The partner's network changes.** The allowlist goes back to `198.51.100.0/24` only. The next call from your machine is
   refused and names it. Harborline's key works from the same machine. `api-key list` shows the contract as the server
   holds it: each key, its agent, its status and its networks.
6. **Audit log, and the calls it doesn't show.** `audit-log list` has every contract change and the key that made it:
   `service_account:create` ×2, `service_account:update` ×4, `member:set_scope` ×2. An update event says *that* a key
   changed, not *what* changed (`api-key get` has that). Refused calls are not in the log at all (`--result failed`: 0). So
   the demo keeps an access ledger of every agent call, with the server's answer and the IP it reported:
   `out/access-ledger.jsonl`, 14 calls, 5 refused (network 2, scope 2, key 1).

**What MemoryLake enforces and what the demo adds.** Every refusal and every allowed call here is the server answering a
call made with that agent's own key, and the network check happens before the server looks at the memory. The demo adds three
things, and says so:

- **The access ledger.** The audit log records changes, not refused calls.
- **`metadata.written_by` on each fact.** Facts do not record which key wrote them, so each agent stamps its own name
  (self-reported).
- **This machine standing in for Kestrel's network at step 3.** One machine cannot call from two networks.

**Watch it run** (real recordings, unedited, made with `MLU_MASK_IP=1`): [CLI demo, 1:24](https://github.com/memorylake-ai/memorylake-usecases/releases/download/cross-network-memory-v1/cross-network-memory-cli-demo.mp4) · [web demo, 1:55](https://github.com/memorylake-ai/memorylake-usecases/releases/download/cross-network-memory-v1/cross-network-memory-web-demo.mp4)
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
cd memorylake-usecases/cross-network-memory

export MEMORYLAKE_API_KEY=sk-…
python3 demo.py
python3 demo.py cleanup
```

A run takes about 40 seconds; nothing in it waits for an LLM. With `MEMORYLAKE_API_KEY` set, the demo logs into its own CLI
profile under `./.memorylake-demo/` (git-ignored) and leaves `~/.memorylake` alone. The sandbox owner key and the two agent
keys each live in their own profile there (`org/`, `members/<name>/`) and are only ever printed as `sk-` + 4 characters.
Kestrel's key can't log in until step 3, so until then it is kept only in the running process.

| | |
|---|---|
| `MEMORYLAKE_BASE_URL` | API endpoint; default global, China is `https://app.memorylake.cn/openapi/memorylake` |
| `MLU_MASK_IP=1` | Print every public IPv4 address as its first two octets (`103.151.•.•`), on screen and in the web app. The recordings and this README use it. The keys are still pinned to the real address |
| `python3 demo.py --reset` | Delete a sandbox team left by an earlier run first, then run (a run does this on its own) |
| `python3 demo.py audit` | Read the sandbox team's audit log and the access ledger again |
| `python3 demo.py report` | Print the last contract review (`out/contract-review.md`, `.json`) |
| `python3 demo.py cleanup` | Delete the sandbox team (its projects, members and keys go with it) and the local profiles |
| `--show-json` | Print the raw JSON the CLI returns (keys masked) |

**Every run starts from nothing.** Teams, members and keys are one-shot, so a run that finds a sandbox team from an earlier
run deletes it first and says so. The run leaves the sandbox team in place so you can look around; `cleanup` deletes it.

## Or run it in the browser

```bash
python3 web/server.py        # http://127.0.0.1:8765 — standard library only
```

![Web companion — the partner's agent joins: 5/5 as the contract says, its customs hold #1 in the forwarder's answer](https://github.com/memorylake-ai/memorylake-usecases/raw/main/cross-network-memory/web/screenshot-partner.png)

![Web companion — the audit log next to the refused calls from the demo's access ledger](https://github.com/memorylake-ai/memorylake-usecases/raw/main/cross-network-memory/web/screenshot-audit.png)

Paste the key, press **Run demo**, and follow the nine steps: the sandbox team, the two memories and agents, the pinned keys
(with the refusal that names your IP), the shipments, the partner joining (both agents' questions and the contract check),
suspend and resume, the network change with the contract as the server holds it, and the audit log next to the refused calls
from the ledger. **Contract review** shows the report. The terminal drawer shows every `memorylake` command as it runs, with
the key it ran under.

## What happens, step by step

Output below is from a real run with `MLU_MASK_IP=1`; ids are shortened.

### 1–3. Connect, sandbox team, two memories and two agents

```
$ memorylake team create --name 'Harborline Freight - agent network sandbox' --issue-api-key --api-key-name mlu-xnm-owner --idempotency-key mlu-xnm-20261013102552
  · org team b357877b… “Harborline Freight - agent network sandbox” — owner key mlu-xnm-owner (id 16067) is printed once; it now lives only in .memorylake-demo/org/
$ memorylake proj create --name 'Shipment contract' --custom-id mlu-xnm-contract …   # as sandbox owner
$ memorylake proj create --name 'Harborline internal' --custom-id mlu-xnm-internal …   # as sandbox owner
$ memorylake member create --name kestrel-broker --role tenant_member   # as sandbox owner
$ memorylake member scope set 8b8a4728… --reason 'contract: Kestrel Customs Brokerage — files customs entries; …' --scopes '[{"resource_type": "memorylake:project", "mode": "instances", "instances": [{"id": "proj-99c5…", "workspace_id": "ws-c30f…", "actions": ["project:read", "project:mem_read", "project:mem_search", …, "project:mem_add", …]}]}]'   # as sandbox owner
  · kestrel-broker (Kestrel Customs Brokerage): virtual member 8b8a4728…, reaches Shipment contract (write)
```

Each scope entry carries its actions. An entry without `actions` gets every action the role allows (see
[enterprise-rbac-memory](../enterprise-rbac-memory)).

### 4. Each key pinned to its agent's network

```
  · Kestrel's agent calls from 198.51.100.0/24 — its key is pinned there
$ memorylake api-key create --name mlu-xnm-kestrel-broker --member 8b8a4728… --allowed-ip 198.51.100.0/24   # as sandbox owner
  · kestrel-broker: key mlu-xnm-kestrel-broker (id 16068, sk-e4ce…) — allowed from 198.51.100.0/24
$ memorylake auth login --api-key sk-e4ce… --base-url https://app.memorylake.ai/openapi/memorylake --profile memorylake-usecases   # as kestrel-broker
  · refused before it touched memory: HTTP 403 permission_error “API key was rejected by the server (permission_error): IP 103.151.•.• is not allowed to access”
  · the server just told us this machine's IP: 103.151.•.•. Harborline's dispatch agent runs here, so its key is pinned to exactly that address
$ memorylake api-key create --name mlu-xnm-harborline-dispatch --member 18855691… --allowed-ip 103.151.•.•   # as sandbox owner
$ memorylake auth login --api-key sk-d4e5… … --profile memorylake-usecases   # as harborline-dispatch
  · harborline-dispatch logged in from 103.151.•.• — its key lives in .memorylake-demo/members/harborline-dispatch/
```

`auth login` checks the key against the server, so a key used outside its network fails right there. `api-key create` does not
echo the allowlist; `api-key get` / `list` return it as `allowed_ips`.

### 5. Harborline's dispatch agent writes the shipments

```
$ memorylake fact add --project proj-99c5… 'Shipment HX-4471: 18 pallets of bicycle frames, Busan to Rotterdam, vessel ETA 2026-10-20, consignee Velora Cycles BV.' … --workspace ws-c30f…   # as harborline-dispatch
  Shipment contract — 4 facts by harborline-dispatch
$ memorylake fact add --project proj-87bc… 'Margin on HX-4471 is 11 percent after the Busan surcharge; do not quote it to anyone outside Harborline.' …   # as harborline-dispatch
  Harborline internal — 3 facts by harborline-dispatch
```

### 6. Kestrel's agent joins the shared memory, and only the shared memory

```
  · for this demo, this machine also plays Kestrel's network: the contract adds 103.151.•.• to the broker's allowlist
$ memorylake api-key update 16068 --allowed-ip 198.51.100.0/24,103.151.•.•   # as sandbox owner
$ memorylake auth login --api-key sk-e4ce… … --profile memorylake-usecases   # as kestrel-broker
  · the same key that was refused at step 4 now logs in — nothing about the key changed, only the contract
$ memorylake search 'Which shipments need a customs document this week?' --projects proj-99c5… --types fact --top-k 4 …   # as kestrel-broker
    #1 Shipment HX-4480: 2 containers of solar inverters, Ningbo to Hamburg, ETA 2026-10-24, needs a C…  [harborline-dispatch]
    …
$ memorylake fact add --project proj-99c5… 'Customs hold on HX-4471: the commercial invoice lists 18 pallets but the packing list lists 16; …' …   # as kestrel-broker
$ memorylake fact list --projects proj-87bc… --page-size 20 …   # as kestrel-broker
  · kestrel-broker on Harborline internal: HTTP 403 ACCESS_DENIED “You don't have the "Read Memory" permission on project "Harborline internal". Ask a team owner or admin to grant it.”
$ memorylake search 'Is anything holding shipment HX-4471 at customs?' --projects proj-99c5… --types fact --top-k 3 …   # as harborline-dispatch
    #1 Customs hold on HX-4471: the commercial invoice lists 18 pallets but the packing list lists 16;…  [kestrel-broker]
    #2 Shipment HX-4471: 18 pallets of bicycle frames, Busan to Rotterdam, vessel ETA 2026-10-20, cons…  [harborline-dispatch]
    #3 HX-4480 entry is ready to file as soon as the CE declaration for the solar inverters arrives; K…  [kestrel-broker]

  contract check (every call made with the agent's own key)
    kestrel-broker       search  Shipment contract    ✓ allowed              4 hits
    kestrel-broker       write   Shipment contract    ✓ allowed              added 2
    kestrel-broker       read    Harborline internal  ✗ 403 ACCESS_DENIED
    kestrel-broker       search  Harborline internal  ✗ 403 ACCESS_DENIED
    harborline-dispatch  search  Shipment contract    ✓ allowed              partner's answer at #1
  · 5/5 calls answered as the contract says
```

### 7. A key may have leaked: suspend it, keep its memory, resume the same key

```
  · ticket SEC-2207: Kestrel reports its broker key was in a CI log for 40 minutes; suspend until they confirm
$ memorylake api-key update 16068 --status disabled   # as sandbox owner
$ memorylake fact list --projects proj-99c5… --page-size 20 …   # as kestrel-broker
  · kestrel-broker, same key, 0.7s later: HTTP 401 authentication_error “API key was rejected by the server (authentication_error): service account is disabled”
  · what kestrel-broker wrote stays in the contract: 2/2 of its facts, read by harborline-dispatch while the key is suspended
$ memorylake api-key get 16068   # as sandbox owner
  · api-key get 16068: status “disabled”, allowed_ips ['198.51.100.0/24', '103.151.•.•']
$ memorylake api-key update 16068 --status enabled   # as sandbox owner
  · the same key, never re-issued, 1.5s later: rc 0, 6 facts
```

### 8. Kestrel's network changes: the contract drops this machine

```
  · ticket NET-318: Kestrel moves its agent back behind its own egress; the contract drops this machine
$ memorylake api-key update 16068 --allowed-ip 198.51.100.0/24   # as sandbox owner
  · kestrel-broker from 103.151.•.•: HTTP 403 permission_error “API key was rejected by the server (permission_error): IP 103.151.•.• is not allowed to access”
  · harborline-dispatch from the same machine, its own key: rc 0, 6 facts
$ memorylake api-key list   # as sandbox owner

  the contract as the server holds it (api-key list)
  key                           agent                 status    allowed from
  mlu-xnm-harborline-dispatch   harborline-dispatch   enabled   103.151.•.•
  mlu-xnm-kestrel-broker        kestrel-broker        enabled   198.51.100.0/24
  mlu-xnm-owner                 niuniu                enabled   anywhere
```

### 9. Audit log for contract changes, access ledger for every call

```
$ memorylake audit-log list --page-size 100   # as sandbox owner
  time (UTC)           action                    by          key     target
  2026-10-13 10:25:53Z tenant:create             niuniu      11511   Harborline Freight - agent network sandbox
  2026-10-13 10:25:58Z member:manage_virtual     niuniu      16067   harborline-dispatch
  2026-10-13 10:25:59Z member:set_scope          niuniu      16067   harborline-dispatch
  2026-10-13 10:26:00Z member:manage_virtual     niuniu      16067   kestrel-broker
  2026-10-13 10:26:00Z member:set_scope          niuniu      16067   kestrel-broker
  2026-10-13 10:26:01Z service_account:create    niuniu      16067   mlu-xnm-kestrel-broker
  2026-10-13 10:26:02Z service_account:create    niuniu      16067   mlu-xnm-harborline-dispatch
  2026-10-13 10:26:10Z service_account:update    niuniu      16067   mlu-xnm-kestrel-broker
  2026-10-13 10:26:21Z service_account:update    niuniu      16067   mlu-xnm-kestrel-broker
  2026-10-13 10:26:23Z service_account:update    niuniu      16067   mlu-xnm-kestrel-broker
  2026-10-13 10:26:26Z service_account:update    niuniu      16067   mlu-xnm-kestrel-broker
  · 11 event(s) since the sandbox was created: tenant:create ×1, member:manage_virtual ×2, member:set_scope ×2, service_account:create ×2, service_account:update ×4
  · service_account:update ×4: the log says the key changed and who changed it, not what changed — the new allowlist / status is read with `api-key get`
$ memorylake audit-log list --result failed --page-size 100   # as sandbox owner
  · refused calls in the audit log: 0 · in the demo's access ledger: 5 of 14 agent calls (key 1, network 2, scope 2)

  access ledger — refused calls (out/access-ledger.jsonl, kept by the demo)
    2026-10-13 10:26:02Z  kestrel-broker       login   —                    HTTP 403 network  from 103.151.•.•
    2026-10-13 10:26:16Z  kestrel-broker       read    Harborline internal  HTTP 403 scope
    2026-10-13 10:26:16Z  kestrel-broker       search  Harborline internal  HTTP 403 scope
    2026-10-13 10:26:21Z  kestrel-broker       read    Shipment contract    HTTP 401 key
    2026-10-13 10:26:26Z  kestrel-broker       read    Shipment contract    HTTP 403 network  from 103.151.•.•
```

The three kinds of refusal look different on the wire, and the ledger keeps them apart. The network check answers
`403 permission_error` with the caller's IP. A suspended key gets `401 authentication_error`. A call outside the scope gets
`403 ACCESS_DENIED` with the permission it lacks.

### Cleanup

```
$ memorylake team delete --confirm b357877b…   # as sandbox owner
  · deleted the sandbox team b357877b… — its projects, members and keys go with it
$ memorylake team get   # as sandbox owner
  · its owner key now: HTTP 401 “service account is disabled”
```

## Measured

Six full runs on a free account (2026-10-13, CLI v20261013), each in a fresh sandbox team: three from the terminal (one with
`--reset`, one with `MLU_MASK_IP=1`), one from the web companion, and the two recordings (CLI and web):

| | every run |
|---|---|
| pinned key used from outside its network | `403 permission_error`, *IP … is not allowed to access*, at `auth login` (6 of 6) |
| contract check | **5/5** as the contract says (6 of 6) |
| Kestrel's hold in Harborline's answer | **#1** (6 of 6) |
| suspended key refused after | 0.5–1.1 s (`401 service account is disabled`) |
| re-enabled key accepted after | 0.9–1.5 s, same key |
| partner's facts readable while suspended | 2/2 (6 of 6) |
| allowlist change → refused on the next call | 6 of 6 |
| audit events per run / refused calls in the audit log | 11 / 0 |
| access ledger | 14 agent calls, 5 refused (network 2, scope 2, key 1) |
| whole run | 36–42 s |

Before writing the demo, a probe on the personal team checked how fast each change takes effect: the first call after every
`api-key update` (disable, enable, a /32 allowlist, a /24 allowlist, `--no-ip-restriction`) already got the new answer, six
calls in a row each time.

## Wiring it into your own deployment

- **One member and one key per agent, pinned to where it runs.** `--allowed-ip` takes IPs and CIDR ranges, comma-separated;
  `api-key update --allowed-ip` replaces the list, and `--no-ip-restriction` removes it. A key used from anywhere else is
  refused before it reaches memory, so a leaked key alone isn't enough to read it.
- **Suspend first, then decide.** `--status disabled` is reversible and takes effect on the next call. Use `rotate` (new
  value, old one dead) when the key is known to be compromised, and `revoke` when the agent is gone.
- **Scope the partner to the shared memory, with actions.** The network says *where from*. The scope says *what*.
- **Keep your own access log.** The audit log tells you which key changed the contract. It does not tell you what changed, and
  it does not record refused calls. Log every agent call's outcome on your side, as this demo's ledger does. The refusal
  text includes the caller's IP.
- **Org teams are limited per account** (one besides the personal team on a free account). Use one org team per environment,
  not one per partner.

## If something goes wrong

- **`the key pinned to 198.51.100.0/24 was accepted from this machine`**: you are calling from inside that documentation
  range (unlikely), or the restriction was not applied. The demo stops, because nothing after it would mean anything.
- **`this machine's IP changed during the run`**: a VPN or network switch between steps. Keys are pinned to the address the
  server saw at step 4. Run again.
- **`tenant creation limit reached: limit=1 current=1`** at step 2: your account already owns an org team. Delete it with its
  owner key (`memorylake team delete --confirm <team-id>`) or use another account.
- **Your egress is IPv6**: the refusal names whatever address the server saw, and the demo pins that. `MLU_MASK_IP` only
  masks IPv4.
- **`.memorylake-demo/org/` was deleted but the sandbox team still exists**: the owner key is gone with it, and it is only
  printed once. Delete the team in the MemoryLake console.
- **`tls handshake eof` / `could not connect`**: a dropped connection. Calls whose refusal is the expected answer, and
  read-only commands, retry four times. Otherwise run again; it starts over.

## Files

```
demo.py                  the runner; prints every CLI command it runs and the key it ran under (and emits events for the web app)
data/contract.json       the two companies, the two memories, the two agents and their networks, the facts, the questions, the incident and the network change
web/server.py            local web companion (stdlib HTTP server + Server-Sent Events over demo.py)
web/static/              the page — no build step
out/                     contract-review.md / .json, access-ledger.jsonl (every agent call), runs.jsonl (git-ignored)
```

The companies, their agents and their shipments are fictional.
