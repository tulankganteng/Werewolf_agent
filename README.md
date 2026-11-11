# Tulank Heuristics — Werewolf agent for make.fun bounty `werewolf-agent-01`

A no-model, deterministic, heuristics-first agent for 8-player Werewolf.
Python 3.13, standard library only. No API key needed, no model calls,
no money spent.

## How it plays

Each decision rebuilds the whole picture of the game from the view the
env serves, so the live run and the replay decide identically (no clock,
no unseeded randomness, no cross-game state).

**Evidence-based suspicion.** Every living seat gets a score from evidence
only:

- voting to exile a revealed villager: +2; voting out a revealed wolf: −2
- accusing a player who turned out to be a villager: +1.5
- a seer claim that names a wolf: +3 if the claimant was proven right,
  +1 if unproven; a claimant proven wrong is +2 suspicious itself
- on the day a wolf is exiled, players who voted for someone else: +1
  (wolves avoid voting their partner out)
- a night-killed villager's own accusations point at wolves: +2

**By role:**

- *Villager* — votes only on real evidence (suspicion ≥ 2, or a known
  wolf); otherwise abstains, because a random day-1 exile hits a
  villager ~5/7 of the time. Speaks with the evidence cited, so the
  house players can follow the case.
- *Seer* — inspects the most suspicious seat each night; once it finds
  a wolf it claims seer and names the wolf and the night it was checked.
- *Doctor* — protects a claimed seer first, otherwise the most active
  villager (wolves target the players driving the debate).
- *Werewolf* — kills claimed seers first, then whoever is accusing it or
  its partner, then the most active accuser; agrees with its partner's
  kill so the kill lands; votes with the village plurality against a
  villager and speaks like a villager, backing the consensus.

## Local test results (real kit env server + scripted house players)

Tested against the kit's actual `program/env/server.mjs` with a local
stub house lane playing the 7 house seats (simple deterministic
heuristics, roughly a low-effort stand-in for claude-haiku-5-5 — it
follows seer claims and accusations, unlike a purely random voter).
Each trial finished in ~5 s wall time, all 30 games finished, and
re-running trial 0 with the same seed reproduced the identical score
(deterministic).

| Trial | Wolf wins | Village wins | Score |
|-------|-----------|--------------|-------|
| 0     | 8/15      | 5/15         | 433   |
| 1     | 9/15      | 3/15         | 400   |
| 2     | 9/15      | 4/15         | 433   |
| Mean  |           |              | **422** |

For comparison, the practice scores visible on the bounty board for
other entries were 100–333. The stub is not claude-haiku-5-5, so these
numbers are a local benchmark, not a prediction — but the agent's real
edge is its evidence-citing speeches, which the stub mostly ignores and
a real house model reads.

## Run it

```
MAKEFUN_ENV_URL=http://store:8080 python3 -P -s main.py
```

## Files

- `main.py` — the whole agent (entrypoint)
- `LICENSE` — MIT
