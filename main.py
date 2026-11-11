# werewolf-agent-01 entry: "Tulank Heuristics" — a no-model, deterministic,
# heuristics-first Werewolf agent (Python 3.13, standard library only).
#
# Strategy in one paragraph:
#   * The agent rebuilds its whole picture of a game from the view the env
#     serves on every decision, so nothing depends on timing, clocks or
#     unseeded randomness: the live run and the replay decide identically.
#   * It keeps a suspicion score for every living seat from evidence only:
#     votes that exiled revealed villagers/wolves, seer claims and whether
#     those claims were later proven right or wrong, accusations aimed at
#     players who turned out to be villagers, and night-kill patterns.
#   * As a villager it votes the most suspicious seat and says why, citing
#     the evidence. As the seer it inspects the most suspicious seat and,
#     once it has found a wolf, claims seer and names the wolf with the
#     night it was checked. As the doctor it protects a claimed seer first,
#     otherwise the most trusted villager. As a werewolf it kills claimed
#     seers first, agrees with its partner's kill, votes with the village
#     plurality against a villager, and speaks like a villager: agreeing
#     with the consensus and nudging suspicion onto a villager.
#
# No model calls are made, so no API key is needed and no $15 budget is
# spent. Entry point: python3 -P -s main.py (MAKEFUN_ENV_URL must be set).

import hashlib
import json
import os
import re
import urllib.error
import urllib.request

ENV_URL = os.environ.get("MAKEFUN_ENV_URL", "").rstrip("/")
SEED = os.environ.get("MAKEFUN_TRIAL_SEED", "werewolf-agent-01")

MAX_CHARS = 500


# --------------------------------------------------------------------------
# small helpers
# --------------------------------------------------------------------------

def post(path, body):
    """POST one JSON object to the env; returns (status, parsed json)."""
    req = urllib.request.Request(
        ENV_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}
    except Exception:
        return 0, {}


def fold(text):
    """Fold a statement the env's way and cut it to MAX_CHARS code points."""
    text = re.sub(r"[\t\n\v\f\r\x85\u2028\u2029]+", " ", text)
    text = "".join(ch for ch in text if ch.isprintable() or ch == " ")
    text = text.strip()
    chars = list(text)
    if len(chars) > MAX_CHARS:
        text = "".join(chars[:MAX_CHARS]).strip()
    return text


# --------------------------------------------------------------------------
# per-game analysis, rebuilt from the view on every decision
# --------------------------------------------------------------------------

class Game:
    def __init__(self, view, game_no):
        self.view = view
        self.game_no = game_no
        self.day = view.get("day", 1)
        self.phase = view.get("phase", "")
        you = view.get("you") or {}
        self.me = you.get("seat")
        self.my_name = you.get("name", "")
        self.role = you.get("role", "")
        self.alive = bool(you.get("alive", True))
        self.partner = (you.get("partner") or {}).get("seat")
        self.players = {}
        for p in view.get("players") or []:
            self.players[p["seat"]] = {
                "name": p.get("name", "?"),
                "alive": bool(p.get("alive", True)),
                "role": p.get("role"),  # revealed only after death
            }
        self.name_to_seat = {
            p["name"].lower(): s for s, p in self.players.items()
        }
        self.log = view.get("log") or []
        self.seer_results = view.get("seerResults") or []
        self.wolf_votes = view.get("wolfVotes") or []
        self.last_protect = view.get("lastProtect") or {}
        self._claims = None
        self._suspicion = None

    # -- basic sets ------------------------------------------------------
    def living(self):
        return [s for s in sorted(self.players) if self.players[s]["alive"]]

    def living_others(self):
        return [s for s in self.living() if s != self.me]

    def wolves_known(self):
        """Seats I know are werewolves: my partner, my seer results, dead wolves."""
        out = set()
        if self.partner:
            out.add(self.partner)
        for r in self.seer_results:
            if r.get("werewolf"):
                out.add(r["seat"])
        for s, p in self.players.items():
            if p["role"] == "werewolf":
                out.add(s)
        return out

    def villagers_known(self):
        out = set()
        for r in self.seer_results:
            if r.get("werewolf") is False:
                out.add(r["seat"])
        for s, p in self.players.items():
            if p["role"] in ("seer", "doctor", "villager"):
                out.add(s)
        return out

    def inspected(self):
        return {r["seat"] for r in self.seer_results}

    def name(self, seat):
        return self.players.get(seat, {}).get("name", "seat %d" % seat)

    def label(self, seat):
        return "seat %d (%s)" % (seat, self.name(seat))

    # -- claims parsed from speeches ------------------------------------
    def claims(self):
        """Parse speeches for seer claims and wolf/innocent accusations.

        Returns list of dicts: {claimer, kind: 'seer'|'wolf'|'innocent',
        target, day}. A claim is trusted only when the claimant's earlier
        claims were proven right, or when the claimant is me.
        """
        if self._claims is not None:
            return self._claims
        claims = []
        for e in self.log:
            if e.get("kind") != "speech":
                continue
            text = e.get("text", "") or ""
            seat = e.get("seat")
            low = text.lower()
            target = None
            m = re.search(r"(?:seat\s*)?(\d+)", low)
            # find a named target: prefer a name mentioned in the sentence
            for nm, s in self.name_to_seat.items():
                if re.search(r"\b" + re.escape(nm) + r"\b", low):
                    target = s
                    break
            if target is None and m:
                target = int(m.group(1))
            if re.search(r"\bi am the seer\b|\bi'?m the seer\b", low):
                claims.append({"claimer": seat, "kind": "seer", "target": None,
                               "day": e.get("day", 0)})
            if target is not None and target != seat:
                if re.search(r"werewolf|wolf\b", low) and re.search(
                        r"\b(is|was|checked|inspected|found|saw)\b", low):
                    claims.append({"claimer": seat, "kind": "wolf",
                                   "target": target, "day": e.get("day", 0)})
                elif re.search(r"innocent|not a wolf|villager|not the wolf",
                               low):
                    claims.append({"claimer": seat, "kind": "innocent",
                                   "target": target, "day": e.get("day", 0)})
        self._claims = claims
        return claims

    def claimer_trust(self, claimer):
        """+1 if the claimant's wolf-accusations were proven right,
        -1 if proven wrong, 0 otherwise. My own claims are trusted."""
        if claimer == self.me:
            return 1
        score = 0
        for c in self.claims():
            if c["claimer"] != claimer or c["kind"] != "wolf":
                continue
            role = self.players.get(c["target"], {}).get("role")
            if role == "werewolf":
                score += 1
            elif role in ("seer", "doctor", "villager"):
                score -= 1
        return 1 if score > 0 else (-1 if score < 0 else 0)

    def claimed_seers(self):
        out = []
        for c in self.claims():
            if c["kind"] == "seer" and c["claimer"] not in out:
                out.append(c["claimer"])
        return out

    # -- suspicion --------------------------------------------------------
    def suspicion(self):
        """Suspicion score per living seat (higher = more likely a wolf),
        from evidence only. Computed for the village's point of view."""
        if self._suspicion is not None:
            return self._suspicion
        s = {seat: 0.0 for seat in self.players}
        # votes in exiles: voting a revealed villager out is damning,
        # voting a revealed wolf out is credit
        for e in self.log:
            if e.get("kind") == "exile":
                role = e.get("role")
                tally = e.get("tally") or []
                voters = self._voters_for(e)
                for v in voters:
                    if role in ("seer", "doctor", "villager"):
                        s[v] = s.get(v, 0) + 2.0
                    elif role == "werewolf":
                        s[v] = s.get(v, 0) - 2.0
                if role == "werewolf":
                    # on the day a wolf was exiled, the other wolf
                    # voted for someone else to save its partner
                    day_voters = {x.get("seat") for x in self.log
                                  if x.get("kind") == "vote"
                                  and x.get("day") == e.get("day")}
                    for v in day_voters:
                        if v not in voters:
                            s[v] = s.get(v, 0) + 1.0
        # accusations aimed at players later revealed as villagers
        for c in self.claims():
            if c["kind"] != "wolf":
                continue
            role = self.players.get(c["target"], {}).get("role")
            if role in ("seer", "doctor", "villager"):
                s[c["claimer"]] = s.get(c["claimer"], 0) + 1.5
            elif role == "werewolf":
                s[c["claimer"]] = s.get(c["claimer"], 0) - 1.5
        # seer claims: a trusted claim of wolf is strong, an unproven one
        # is a nudge; a proven liar is very suspicious
        for c in self.claims():
            if c["kind"] != "wolf":
                continue
            trust = self.claimer_trust(c["claimer"])
            if trust > 0:
                s[c["target"]] = s.get(c["target"], 0) + 3.0
            elif trust < 0:
                s[c["claimer"]] = s.get(c["claimer"], 0) + 2.0
            else:
                s[c["target"]] = s.get(c["target"], 0) + 1.0
        for c in self.claims():
            if c["kind"] == "innocent":
                trust = self.claimer_trust(c["claimer"])
                if trust > 0:
                    s[c["target"]] = s.get(c["target"], 0) - 1.5
        # night kills: whoever pushed suspicion on a night-killed villager
        # gains a little suspicion (wolves steer the village onto victims)
        for e in self.log:
            if e.get("kind") == "dawn" and e.get("role") in (
                    "seer", "doctor", "villager"):
                dead = e.get("died")
                for c in self.claims():
                    if c["kind"] == "wolf" and c["target"] == dead:
                        s[c["claimer"]] = s.get(c["claimer"], 0) + 0.5
                # but the dead player's own accusations point at wolves:
                # wolves kill the villagers who are onto them
                for c in self.claims():
                    if (c["kind"] == "wolf" and c["claimer"] == dead
                            and c["target"] is not None):
                        s[c["target"]] = s.get(c["target"], 0) + 2.0
        # my own seer results override everything
        for r in self.seer_results:
            if r.get("werewolf"):
                s[r["seat"]] = 100.0
            elif r.get("werewolf") is False:
                s[r["seat"]] = -100.0
        # dead players and myself score nothing; partner is a known wolf
        for seat in list(s):
            if not self.players.get(seat, {}).get("alive", False):
                s[seat] = 0.0
        if self.role == "werewolf" and self.partner:
            s[self.partner] = -100.0
        self._suspicion = s
        return s

    def _voters_for(self, exile_entry):
        """Seats whose vote in that day's voting named the exiled seat."""
        target = exile_entry.get("seat")
        day = exile_entry.get("day")
        voters = []
        for e in self.log:
            if (e.get("kind") == "vote" and e.get("day") == day
                    and e.get("target") == target):
                voters.append(e.get("seat"))
        return voters

    def votes_this_round(self):
        """Current voting tally from the log (votes already cast today)."""
        tally = {}
        for e in self.log:
            if (e.get("kind") == "vote" and e.get("day") == self.day):
                t = e.get("target")
                if t:
                    tally[t] = tally.get(t, 0) + 1
        return tally

    def top_suspect(self, candidates=None):
        """Most suspicious living seat; ties break by lowest seat."""
        s = self.suspicion()
        cands = candidates if candidates is not None else self.living_others()
        cands = [c for c in cands if self.players[c]["alive"]]
        if not cands:
            return None
        return sorted(cands, key=lambda c: (-s.get(c, 0), c))[0]

    def most_trusted(self, candidates=None):
        s = self.suspicion()
        cands = candidates if candidates is not None else self.living_others()
        cands = [c for c in cands if self.players[c]["alive"]]
        if not cands:
            return None
        return sorted(cands, key=lambda c: (s.get(c, 0), c))[0]

    # -- evidence phrasing -------------------------------------------------
    def reason_for(self, seat):
        """One short evidence phrase for suspecting a seat, or ''."""
        for e in self.log:
            if e.get("kind") == "exile" and e.get("role") in (
                    "seer", "doctor", "villager"):
                if seat in self._voters_for(e):
                    return ("voted to exile %s, who was a %s"
                            % (self.name(e.get("seat")), e.get("role")))
        for c in self.claims():
            if c["kind"] == "wolf" and c["target"] == seat:
                if self.claimer_trust(c["claimer"]) > 0:
                    return ("%s checked them and found a wolf"
                            % self.name(c["claimer"]))
                return ("%s accused them of being a wolf"
                        % self.name(c["claimer"]))
        for c in self.claims():
            if (c["kind"] == "wolf" and c["claimer"] == seat
                    and self.players.get(c["target"], {}).get("role")
                    in ("seer", "doctor", "villager")):
                return ("accused %s, who turned out to be a %s"
                        % (self.name(c["target"]),
                           self.players[c["target"]]["role"]))
        return ""


# --------------------------------------------------------------------------
# decisions
# --------------------------------------------------------------------------

def decide_speak(g):
    """One statement for the debate, or None to pass."""
    if not g.alive:
        return None
    sus = g.suspicion()
    if g.role == "werewolf":
        # Blend in: back the village's leading suspect (never the partner),
        # or push the most suspicious villager with a reason.
        tally = g.votes_this_round()
        band = None
        if tally:
            band = sorted(tally.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        if band and band != g.partner and band != g.me:
            return fold("I agree with the votes on %s, they have looked "
                        "off all day. Voting %s."
                        % (g.label(band), g.label(band)))
        target = g.top_suspect([c for c in g.living_others()
                                if c != g.partner])
        if target is None:
            return None
        reason = g.reason_for(target)
        if reason:
            return fold("I am suspicious of %s: they %s."
                        % (g.label(target), reason))
        return fold("I am suspicious of %s, they have been too quiet "
                    "and their story does not add up."
                    % g.label(target))
    # village side
    wolves = [s for s in g.living_others() if s in g.wolves_known()
              and s != g.partner]
    if g.role == "seer":
        wolves = [s for s in g.living_others()
                  if any(r.get("seat") == s and r.get("werewolf")
                         for r in g.seer_results)]
    if wolves:
        w = wolves[0]
        if g.role == "seer":
            night = next((r.get("night") for r in g.seer_results
                          if r.get("seat") == w), g.day)
            return fold("I am the seer. I checked %s on night %d and "
                        "they are a werewolf. Vote %s."
                        % (g.label(w), night, g.label(w)))
        return fold("%s is a werewolf, vote %s out."
                    % (g.label(w), g.label(w)))
    target = g.top_suspect()
    if target is None:
        return None
    reason = g.reason_for(target)
    if sus.get(target, 0) < 1.0:
        # no evidence yet: stay quiet rather than invent a case
        return None
    if reason:
        return fold("I am suspicious of %s: they %s."
                    % (g.label(target), reason))
    return fold("I am suspicious of %s, their votes do not line up "
                "with a villager's." % g.label(target))


def decide_vote(g, options):
    if not g.alive or not options:
        return None
    if g.role == "werewolf":
        # vote with the plurality (never the partner, never myself)
        tally = g.votes_this_round()
        cands = [c for c in options if c != g.partner]
        if tally:
            ranked = sorted(
                ((c, tally.get(c, 0)) for c in cands),
                key=lambda kv: (-kv[1], kv[0]))
            if ranked[0][1] > 0:
                return ranked[0][0]
        target = g.top_suspect(cands)
        return target if target in options else (cands[0] if cands else None)
    wolves = [s for s in options if s in g.wolves_known()]
    if wolves:
        return wolves[0]
    # Village side: only vote on real evidence. A random day-1 exile
    # hits a villager ~5/7 of the time and hands the wolves the game,
    # so abstain until suspicion is backed by evidence — unless the
    # table is small, when inaction loses by parity anyway.
    top = g.top_suspect(options)
    if top is None:
        return None
    if g.suspicion().get(top, 0) >= 2.0 or len(g.living()) <= 5:
        return top
    return None


def decide_kill(g, options):
    """Werewolf night kill. First wolf picks; second agrees with partner."""
    if not options:
        return None
    # partner already voted tonight: agree, so the kill lands
    for v in g.wolf_votes:
        if (v.get("night") == g.day and v.get("seat") == g.partner
                and v.get("target") in options):
            return v["target"]
    # claimed seers are the danger: kill them first
    for s in g.claimed_seers():
        if s in options and s != g.partner:
            return s
    # then whoever is accusing me or my partner
    for c in g.claims():
        if (c["kind"] == "wolf" and c["claimer"] in options
                and c["target"] in (g.me, g.partner)):
            return c["claimer"]
    # then the most active accuser in the debate
    activity = {}
    for e in g.log:
        if e.get("kind") == "speech":
            activity[e.get("seat")] = activity.get(e.get("seat"), 0) + 1
    cands = [c for c in options if c != g.partner]
    if cands:
        return sorted(cands, key=lambda c: (-activity.get(c, 0), c))[0]
    return options[0]


def decide_protect(g, options):
    if not options:
        return None
    last = (g.last_protect or {}).get("seat")
    cands = [c for c in options if c != last]
    if not cands:
        return None
    # protect a claimed seer first
    for s in g.claimed_seers():
        if s in cands:
            return s
    # otherwise protect the most active villager: wolves target the
    # players who are driving the debate; ties go to the most trusted
    activity = {}
    for e in g.log:
        if e.get("kind") == "speech":
            activity[e.get("seat")] = activity.get(e.get("seat"), 0) + 1
    sus = g.suspicion()
    return sorted(cands, key=lambda c: (-activity.get(c, 0),
                                        sus.get(c, 0), c))[0]


def decide_inspect(g, options):
    if not options:
        return None
    done = g.inspected()
    cands = [c for c in options if c not in done]
    if not cands:
        return None
    return g.top_suspect(cands)


def decide(pending):
    """Answer one pending decision; returns the /act body."""
    view = pending.get("view") or {}
    g = Game(view, pending.get("game", 0))
    kind = pending.get("decision")
    options = pending.get("options") or []
    if kind == "speak":
        text = decide_speak(g)
        if text:
            return {"game": pending["game"], "decision": "speak",
                    "text": text}
        return {"game": pending["game"], "decision": "speak", "pass": True}
    if kind == "vote":
        return {"game": pending["game"], "decision": "vote",
                "target": decide_vote(g, options)}
    if kind == "kill":
        return {"game": pending["game"], "decision": "kill",
                "target": decide_kill(g, options)}
    if kind == "protect":
        return {"game": pending["game"], "decision": "protect",
                "target": decide_protect(g, options)}
    if kind == "inspect":
        return {"game": pending["game"], "decision": "inspect",
                "target": decide_inspect(g, options)}
    return {"game": pending["game"], "decision": kind, "target": None}


# --------------------------------------------------------------------------
# main loop
# --------------------------------------------------------------------------

def fallback(pending):
    if pending.get("decision") == "speak":
        return {"game": pending["game"], "decision": "speak", "pass": True}
    return {"game": pending["game"], "decision": pending.get("decision"),
            "target": None}


def main():
    if not ENV_URL:
        raise SystemExit("MAKEFUN_ENV_URL is not set")
    decisions = 0
    while True:
        status, obs = post("/observe", {})
        if status != 200 or not isinstance(obs, dict):
            # env gone or refusing: wait briefly by polling again
            continue
        if obs.get("done"):
            break
        pending = obs.get("pending") or []
        if not pending:
            continue
        for p in pending:
            try:
                act = decide(p)
            except Exception:
                act = fallback(p)
            status, _ = post("/act", act)
            if status == 400:
                # our answer was refused; send the fallback once
                post("/act", fallback(p))
            decisions += 1
    print("werewolf-agent: done, %d decisions answered" % decisions)


if __name__ == "__main__":
    main()
