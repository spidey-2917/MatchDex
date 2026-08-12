"""
Quick Sim Match Engine
======================
Simulates a full 90-minute football match between two lineups.

The engine:
1. Calculates zone-based team ratings (ATT/MID/DEF/GK) from lineup cards.
2. Applies tactic modifiers.
3. Runs 15 time-phases (each ~6 game-minutes) resolving possession,
   chance creation, and shots with a randomised luck multiplier.
4. Returns a chronological list of MatchEvent objects for a live feed.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional


# ── Data Classes ──────────────────────────────────────────────────


@dataclass
class PlayerSnapshot:
    """Lightweight snapshot of a card for the sim — avoids DB access mid-loop."""
    name: str
    position: str  # e.g. "ST", "CB", "GK"
    attack: int
    defence: int
    ovr: int
    slot: str  # e.g. "at1", "gk", "md3"


@dataclass
class TeamData:
    """Pre-computed team ratings + player list for one side."""
    display_name: str
    players: List[PlayerSnapshot]
    gk: Optional[PlayerSnapshot]
    attackers: List[PlayerSnapshot]
    midfielders: List[PlayerSnapshot]
    defenders: List[PlayerSnapshot]
    att_rating: float = 0.0
    mid_rating: float = 0.0
    def_rating: float = 0.0
    gk_rating: float = 0.0
    tactic: str = "balanced"
    logo_bonus: int = 0


@dataclass
class MatchEvent:
    """A single event in the match timeline."""
    minute: int
    text: str
    event_type: str  # "goal", "save", "miss", "possession", "card", "chance", "half_time", "full_time"
    scorer_name: Optional[str] = None
    team_side: Optional[str] = None  # "home" or "away"


@dataclass
class MatchResult:
    """Complete result of a simulated match."""
    home_score: int
    away_score: int
    events: List[MatchEvent] = field(default_factory=list)
    home_name: str = ""
    away_name: str = ""


# ── Tactic Modifiers ─────────────────────────────────────────────

TACTIC_MODS = {
    #              ATT    MID    DEF
    "balanced":   (1.00,  1.00,  1.00),
    "attacking":  (1.10,  1.00,  0.90),
    "defensive":  (0.85,  1.00,  1.10),
    "possession": (0.95,  1.15,  1.00),
    "counter":    (1.00,  0.95,  1.05),  # special: counter-attack bonus handled separately
}


# ── Rating Calculator ────────────────────────────────────────────

def _avg(values: list[int | float]) -> float:
    return sum(values) / len(values) if values else 0.0


def build_team_data(
    cards: list,          # list of UserCard ORM instances
    display_name: str,
    tactic: str = "balanced",
    logo_bonus: int = 0,
) -> TeamData:
    """
    Converts a list of UserCard ORM objects into a TeamData snapshot.
    Cards are bucketed by their slot prefix (gk/df/md/at).
    """
    players: list[PlayerSnapshot] = []
    gk = None
    attackers: list[PlayerSnapshot] = []
    midfielders: list[PlayerSnapshot] = []
    defenders: list[PlayerSnapshot] = []

    for card in cards:
        t = card.template
        # Determine the slot from the related_name or by iterating the lineup
        # For now we use position-based grouping
        snap = PlayerSnapshot(
            name=t.name,
            position=t.position,
            attack=t.attack_stat,
            defence=t.defence_stat,
            ovr=t.ovr or max(t.attack_stat, t.defence_stat),
            slot="",
        )
        players.append(snap)

        if t.position == "GK":
            gk = snap
        elif t.position in ("LW", "ST", "RW"):
            attackers.append(snap)
        elif t.position in ("CAM", "CM", "CDM"):
            midfielders.append(snap)
        elif t.position in ("LB", "CB", "RB"):
            defenders.append(snap)
        else:
            # Fallback: bucket by stat dominance
            if t.attack_stat >= t.defence_stat:
                attackers.append(snap)
            else:
                defenders.append(snap)

    # If there happen to be no midfielders (e.g. 4-2-4 with only ATT-position
    # players in md slots), spread some defenders/attackers into mid bucket
    if not midfielders and (attackers or defenders):
        # Grab the weakest attacker and strongest defender as midfield proxies
        if attackers:
            midfielders.append(attackers[-1])
        if defenders:
            midfielders.append(defenders[0])

    att_mod, mid_mod, def_mod = TACTIC_MODS.get(tactic, (1.0, 1.0, 1.0))

    team = TeamData(
        display_name=display_name,
        players=players,
        gk=gk,
        attackers=attackers,
        midfielders=midfielders,
        defenders=defenders,
        att_rating=_avg([p.attack for p in attackers]) * att_mod + logo_bonus if attackers else 50.0,
        mid_rating=_avg([(p.attack + p.defence) / 2 for p in midfielders]) * mid_mod + logo_bonus if midfielders else 50.0,
        def_rating=_avg([p.defence for p in defenders]) * def_mod + logo_bonus if defenders else 50.0,
        gk_rating=(gk.defence + logo_bonus) if gk else 50.0,
        tactic=tactic,
        logo_bonus=logo_bonus,
    )
    return team


# ── Simulation Helpers ───────────────────────────────────────────

def _luck() -> float:
    """Random luck multiplier: 0.80 – 1.30, slightly biased towards average."""
    return random.triangular(0.80, 1.30, 1.02)


def _pick_shooter(team: TeamData) -> PlayerSnapshot:
    """
    Randomly selects a player to take the shot.
    Attackers have ~60% chance, midfielders ~35%, defenders ~5%.
    """
    pool: list[tuple[PlayerSnapshot, float]] = []
    for p in team.attackers:
        pool.append((p, 6.0))
    for p in team.midfielders:
        pool.append((p, 3.5))
    for p in team.defenders:
        pool.append((p, 0.5))

    if not pool:
        # Absolute fallback
        return team.players[0] if team.players else PlayerSnapshot("Unknown", "ST", 50, 50, 50, "")

    total = sum(w for _, w in pool)
    r = random.uniform(0, total)
    cumulative = 0.0
    for player, weight in pool:
        cumulative += weight
        if r <= cumulative:
            return player
    return pool[-1][0]


# ── Goal commentary templates ────────────────────────────────────

_GOAL_TEMPLATES = [
    "⚽ **GOAL!** {shooter} fires an unstoppable shot into the net!",
    "⚽ **GOAL!** {shooter} finishes with clinical precision!",
    "⚽ **GOAL!** A brilliant strike from {shooter}! What a hit!",
    "⚽ **GOAL!** {shooter} slots it home coolly past the keeper!",
    "⚽ **GOAL!** {shooter} rises highest and heads it in!",
    "⚽ **GOAL!** {shooter} weaves past the defence and scores!",
    "⚽ **GOAL!** A thunderbolt from {shooter}! Keeper had no chance!",
    "⚽ **GOAL!** {shooter} pounces on the rebound and buries it!",
]

_SAVE_TEMPLATES = [
    "🧤 Great save by the keeper! {shooter}'s shot is denied!",
    "🧤 The goalkeeper pulls off a magnificent stop from {shooter}!",
    "🧤 What a save! {shooter}'s fierce drive is kept out!",
    "🧤 Brilliant reflexes! The keeper tips {shooter}'s shot away!",
]

_MISS_TEMPLATES = [
    "💨 {shooter} fires wide! A golden chance wasted!",
    "💨 {shooter} blazes it over the bar from close range!",
    "💨 Off the post! {shooter} so close!",
    "💨 {shooter}'s shot flies just wide of the target!",
]

_CHANCE_TEMPLATES = [
    "🔥 {team} building a dangerous attack...",
    "🔥 {team} surging forward with purpose!",
    "🔥 Great build-up play from {team}!",
]

_COUNTER_TEMPLATES = [
    "⚡ {team} break on the counter-attack!",
    "⚡ Rapid transition by {team}! They're through on goal!",
]

_POSSESSION_TEMPLATES = [
    "⚙️ {team} controlling the midfield battle.",
    "⚙️ {team} winning the possession game.",
    "⚙️ Strong midfield presence from {team}.",
]

_CARD_TEMPLATES = [
    "🟨 Yellow card! {player} goes into the book for a reckless challenge.",
    "🟨 {player} is cautioned by the referee.",
]


# ── Main Simulation ──────────────────────────────────────────────

def simulate_match(home: TeamData, away: TeamData) -> MatchResult:
    """
    Run the full 90-minute simulation.

    Returns a MatchResult with all events in chronological order.
    """
    events: list[MatchEvent] = []
    home_score = 0
    away_score = 0

    # We simulate 15 phases — each covers ~6 minutes of game time
    # Phase minutes are slightly randomised for variety
    phase_minutes: list[int] = []
    minute = 1
    for _ in range(15):
        phase_minutes.append(minute)
        minute += random.randint(5, 7)

    # Ensure we don't exceed 90+stoppage
    phase_minutes = [min(m, 90) for m in phase_minutes]

    for phase_idx, base_minute in enumerate(phase_minutes):
        # Half-time event
        if phase_idx == 7:
            events.append(MatchEvent(
                minute=45,
                text="⏱️ **Half Time!**",
                event_type="half_time",
            ))

        # Slight minute jitter for realism
        minute = base_minute + random.randint(0, 3)
        minute = min(minute, 93)  # cap at 90+3

        # ── Phase 1: Midfield Battle ──────────────────────────
        home_mid_roll = home.mid_rating * _luck()
        away_mid_roll = away.mid_rating * _luck()

        if home_mid_roll >= away_mid_roll:
            attacking_team, defending_team = home, away
            atk_side = "home"
        else:
            attacking_team, defending_team = away, home
            atk_side = "away"

        # Occasional possession commentary (~30% of phases)
        if random.random() < 0.30:
            events.append(MatchEvent(
                minute=minute,
                text=random.choice(_POSSESSION_TEMPLATES).format(team=attacking_team.display_name),
                event_type="possession",
                team_side=atk_side,
            ))

        # ── Phase 2: Chance Creation ──────────────────────────
        # Compare ATT of attacker vs DEF of defender
        att_roll = attacking_team.att_rating * _luck()
        def_roll = defending_team.def_rating * _luck()

        # Counter-attack bonus: if the defending team has "counter" tactic
        # and the attack fails, they get a bonus chance
        counter_chance = False

        chance_threshold = def_roll * 1.05  # slight defender advantage
        if att_roll > chance_threshold:
            # Chance created!
            is_counter = attacking_team.tactic == "counter" and random.random() < 0.25
            if is_counter:
                events.append(MatchEvent(
                    minute=minute,
                    text=random.choice(_COUNTER_TEMPLATES).format(team=attacking_team.display_name),
                    event_type="chance",
                    team_side=atk_side,
                ))
            else:
                events.append(MatchEvent(
                    minute=minute,
                    text=random.choice(_CHANCE_TEMPLATES).format(team=attacking_team.display_name),
                    event_type="chance",
                    team_side=atk_side,
                ))

            # ── Phase 3: The Shot ─────────────────────────────
            shooter = _pick_shooter(attacking_team)
            shot_power = shooter.attack * _luck()
            gk_power = defending_team.gk_rating * _luck()

            # Determine outcome
            if shot_power > gk_power * 1.05:
                # GOAL!
                if atk_side == "home":
                    home_score += 1
                else:
                    away_score += 1

                events.append(MatchEvent(
                    minute=minute,
                    text=random.choice(_GOAL_TEMPLATES).format(shooter=shooter.name),
                    event_type="goal",
                    scorer_name=shooter.name,
                    team_side=atk_side,
                ))
            elif shot_power > gk_power * 0.90:
                # Great save
                events.append(MatchEvent(
                    minute=minute,
                    text=random.choice(_SAVE_TEMPLATES).format(shooter=shooter.name),
                    event_type="save",
                    team_side=atk_side,
                ))
            else:
                # Miss
                events.append(MatchEvent(
                    minute=minute,
                    text=random.choice(_MISS_TEMPLATES).format(shooter=shooter.name),
                    event_type="miss",
                    team_side=atk_side,
                ))
        else:
            # Attack neutralised — counter-attack chance for defending team
            if defending_team.tactic == "counter" and random.random() < 0.35:
                counter_chance = True

        # ── Counter-Attack Resolution ─────────────────────────
        if counter_chance:
            counter_side = "away" if atk_side == "home" else "home"
            events.append(MatchEvent(
                minute=minute + 1,
                text=random.choice(_COUNTER_TEMPLATES).format(team=defending_team.display_name),
                event_type="chance",
                team_side=counter_side,
            ))
            shooter = _pick_shooter(defending_team)
            shot_power = shooter.attack * _luck()
            gk_power = attacking_team.gk_rating * _luck()

            if shot_power > gk_power * 1.10:  # harder to score on counter
                if counter_side == "home":
                    home_score += 1
                else:
                    away_score += 1
                events.append(MatchEvent(
                    minute=minute + 1,
                    text=random.choice(_GOAL_TEMPLATES).format(shooter=shooter.name),
                    event_type="goal",
                    scorer_name=shooter.name,
                    team_side=counter_side,
                ))
            elif shot_power > gk_power * 0.92:
                events.append(MatchEvent(
                    minute=minute + 1,
                    text=random.choice(_SAVE_TEMPLATES).format(shooter=shooter.name),
                    event_type="save",
                    team_side=counter_side,
                ))
            else:
                events.append(MatchEvent(
                    minute=minute + 1,
                    text=random.choice(_MISS_TEMPLATES).format(shooter=shooter.name),
                    event_type="miss",
                    team_side=counter_side,
                ))

        # ── Random Yellow Card (~10% chance per phase) ────────
        if random.random() < 0.10:
            card_team = random.choice([home, away])
            card_player = random.choice(card_team.players) if card_team.players else None
            if card_player:
                events.append(MatchEvent(
                    minute=minute + random.randint(0, 2),
                    text=random.choice(_CARD_TEMPLATES).format(player=card_player.name),
                    event_type="card",
                ))

    # Full time
    events.append(MatchEvent(
        minute=90,
        text="🏁 **Full Time!**",
        event_type="full_time",
    ))

    # Sort events by minute for clean timeline
    events.sort(key=lambda e: (e.minute, ["possession", "chance", "goal", "save", "miss", "card", "half_time", "full_time"].index(e.event_type) if e.event_type in ["possession", "chance", "goal", "save", "miss", "card", "half_time", "full_time"] else 99))

    return MatchResult(
        home_score=home_score,
        away_score=away_score,
        events=events,
        home_name=home.display_name,
        away_name=away.display_name,
    )
