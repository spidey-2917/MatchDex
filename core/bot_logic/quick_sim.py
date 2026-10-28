"""
Quick Sim Match Engine — Ported from RFM27 Game Event Simulator
Simulates a full 90-minute football match using RFM27 state machine and mathematics:

1. Dynamic state-machine phase progression:
   KICK_OFF -> MIDDLE (Midfield Battle) -> ATTACKING (Att vs Def)
   -> DANGEROUS (Chance Creation / Clearance Counter / Penalty)
   -> SHOOTING (Position-weighted shooter vs Goalkeeper roulette wheel)
   -> GOAL / SAVE / MISS -> ASSIST calculation.
2. RFM27 Line-count formation multipliers (alterAverageBasedOnPlayersPerLine):
   Adjusts DEF, MID, ATT ratings dynamically based on numbers of players in each line.
3. RFM27 Superiority Boost Factor (5% superiority multiplier to dominant lines).
4. RFM27 Clearance to Attack counter-attacking mechanics.
5. RFM27 Disciplinary system: Yellow cards, double yellow expulsions, straight reds
   that actively remove players and weaken team line ratings in real time.
6. Dynamic minute calculator with natural match pacing (half-time at 45', full-time at 90').
7. Goalscorer and assist partner tracking with rich live commentary.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import List, Optional, Tuple


# ── Data Classes ──────────────────────────────────────────────────


@dataclass
class PlayerSnapshot:
    """Lightweight snapshot of a card for the sim — avoids DB access mid-loop."""
    name: str
    position: str  # e.g. "ST", "CB", "GK"
    attack: int
    defence: int
    ovr: int
    slot: str = ""  # e.g. "at1", "gk", "md3"
    yellow_cards: int = 0
    is_sent_off: bool = False
    is_injured: bool = False


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
    manager_boost_type: Optional[str] = None
    manager: Optional[Any] = None
    manager_clearance_bonus: float = 0.0
    formation: str = "433"
    captain: Optional[PlayerSnapshot] = None


@dataclass
class MatchEvent:
    """A single event in the match timeline with visual radar and statistics tracking."""
    minute: int
    text: str
    event_type: str  # "goal", "save", "miss", "possession", "card", "chance", "half_time", "full_time"
    scorer_name: Optional[str] = None
    assist_name: Optional[str] = None
    team_side: Optional[str] = None  # "home" or "away"
    phase_title: str = ""           # e.g. "⚙️ MIDFIELD BATTLE", "🔥 DANGEROUS CHANCE", "⚽ GOAL!"
    zone: int = 2                   # 0: Home Box, 1: Home Def, 2: Midfield, 3: Away Def, 4: Away Box
    home_momentum: int = 50         # 0-100 possession / momentum % for home team
    is_shot: bool = False
    is_shot_on_target: bool = False
    card_type: Optional[str] = None # "yellow" or "red"


@dataclass
class MatchResult:
    """Complete result of a simulated match."""
    home_score: int
    away_score: int
    events: List[MatchEvent] = field(default_factory=list)
    home_name: str = ""
    away_name: str = ""
    home_total_shots: int = 0
    away_total_shots: int = 0
    home_shots_on_target: int = 0
    away_shots_on_target: int = 0
    home_cards: List[str] = field(default_factory=list)
    away_cards: List[str] = field(default_factory=list)


# ── Visual Interface Helpers (RFM27 Pitch Radar & Momentum) ──────────

def render_pitch_radar(
    zone: int,
    event_type: str,
    home_name: str,
    away_name: str,
) -> tuple[str, str]:
    """
    Renders an RFM-style 5-zone pitch radar showing the ball/action puck
    moving across the pitch between Home and Away goal lines.

    Zones:
      0: Home Box (Threatened by Away)
      1: Home Defensive Third
      2: Center Midfield
      3: Away Defensive Third
      4: Away Box (Threatened by Home)

    Returns:
      (radar_line, zone_description)
    """
    if event_type == "goal":
        puck = "💥 GOAL!"
    elif event_type == "save":
        puck = "🧤 SAVE!"
    elif event_type == "miss":
        puck = "💨 MISS!"
    elif event_type == "card":
        puck = "🟨 FOUL"
    elif event_type == "half_time":
        puck = "⏱️ HT"
    elif event_type == "full_time":
        puck = "🏁 FT"
    else:
        puck = "⚽"

    icons = ["🥅", "🛡️", "⚙️", "⚔️", "🥅"]
    nodes = []
    clamped_zone = max(0, min(4, zone))
    for i in range(5):
        if i == clamped_zone:
            nodes.append(f"**[{puck}]**")
        else:
            nodes.append(icons[i])

    radar_line = f"`{home_name[:12]}` " + " ── ".join(nodes) + f" `{away_name[:12]}`"

    zone_descs = [
        f"🚨 **Danger!** {home_name}'s penalty box under heavy siege!",
        f"🛡️ **Defensive Battle:** {home_name} fighting off pressure in their defensive third.",
        f"⚙️ **Midfield Battle:** Both sides contesting control in the center circle.",
        f"⚔️ **Attack Building:** {home_name} pushing forward into {away_name}'s half.",
        f"🚨 **Danger!** {away_name}'s penalty box under heavy siege!"
    ]
    zone_desc = zone_descs[clamped_zone]

    return radar_line, zone_desc


def render_momentum_bar(home_pct: int, home_name: str, away_name: str) -> str:
    """
    Renders a visual tug-of-war momentum / possession bar.
    """
    pct = max(10, min(90, home_pct))
    filled = max(1, min(13, int(round(14 * (pct / 100.0)))))
    empty = 14 - filled
    bar = "█" * filled + "░" * empty

    h_short = home_name[:10]
    a_short = away_name[:10]
    return f"`[{bar}]`\n`{h_short:<10}` {pct}% ─ {100 - pct}% `{a_short:>10}`"


# ── Tactic Modifiers (RFM27 + MatchDex Tactics) ───────────────────

TACTIC_MODS = {
    #              ATT    MID    DEF   CLEARANCE_BOOST
    "balanced":   (1.00,  1.00,  1.00, 0.28),
    "attacking":  (1.10,  1.00,  0.90, 0.20),
    "defensive":  (0.85,  1.00,  1.10, 0.35),
    "possession": (0.95,  1.15,  1.00, 0.22),
    "counter":    (1.02,  0.95,  1.08, 0.45),
}


# ── RFM27 Rating & Formation Mathematics ──────────────────────────

def _avg(values: list[int | float]) -> float:
    return sum(values) / len(values) if values else 50.0


def alter_average_based_on_players_per_line(
    def_count: int, mid_count: int, att_count: int,
    def_val: float, mid_val: float, att_val: float
) -> Tuple[float, float, float]:
    """
    Direct port of RFM27 alterAverageBasedOnPlayersPerLine.
    Applies formation-density multipliers to defense, midfield, and attack lines.
    """
    # Defense multiplier
    if def_count <= 2:
        d_mult = 0.80
    elif def_count == 3:
        d_mult = 0.88
    elif def_count == 4:
        d_mult = 1.00
    elif def_count == 5:
        d_mult = 1.11
    else:
        d_mult = 1.15

    # Midfield multiplier
    if mid_count <= 1:
        m_mult = 0.75
    elif mid_count == 2:
        m_mult = 0.82
    elif mid_count == 3:
        m_mult = 0.91
    elif mid_count == 4:
        m_mult = 1.00
    else:
        m_mult = 1.14

    # Attack multiplier
    if att_count <= 1:
        a_mult = 0.91
    elif att_count == 2:
        a_mult = 1.00
    elif att_count == 3:
        a_mult = 1.09
    elif att_count == 4:
        a_mult = 1.14
    else:
        a_mult = 1.17

    return def_val * d_mult, mid_val * m_mult, att_val * a_mult


def normalize_formation_str(f: str) -> str:
    """Normalize formation string e.g. '4-3-3' -> '433'."""
    return (str(f) if f else "").replace("-", "").strip().lower()


def calculate_manager_boosts(manager, formation: str, tactic: str):
    """
    Computes dynamic manager boosts based on formation and tactic matching.
    Returns: (att_multiplier, def_multiplier, clearance_bonus, primary_boost_type)
    """
    att_mult = 1.0
    def_mult = 1.0
    clearance_bonus = 0.0
    primary_type = None

    if not manager:
        return att_mult, def_mult, clearance_bonus, primary_type

    norm_pref = normalize_formation_str(getattr(manager, "preferred_formation", "all"))
    norm_form = normalize_formation_str(formation)

    # 1. Formation Synergy
    if norm_pref in ("all", norm_form):
        b_type = getattr(manager, "formation_boost_type", None) or getattr(manager, "boost_type", "ATTACKING")
        pct = getattr(manager, "formation_boost_pct", 5) or 5
        rate = pct / 100.0
        primary_type = b_type

        if b_type == "ATTACKING":
            att_mult *= (1.0 + rate)
        elif b_type == "DEFENCE":
            def_mult *= (1.0 + rate)
        elif b_type == "BALANCED":
            att_mult *= (1.0 + rate)
            def_mult *= (1.0 + rate)
        elif b_type == "COUNTER_ATTACKING":
            clearance_bonus += rate

    # 2. Tactic Synergy
    pref_tactic = getattr(manager, "preferred_tactic", "ALL") or "ALL"
    if pref_tactic == "ALL" or pref_tactic.lower() == (tactic or "").lower():
        t_type = getattr(manager, "tactic_boost_type", "NONE")
        t_pct = getattr(manager, "tactic_boost_pct", 0) or 0
        if t_type and t_type != "NONE" and t_pct > 0:
            t_rate = t_pct / 100.0
            if not primary_type or primary_type == "NONE":
                primary_type = t_type

            if t_type == "ATTACKING":
                att_mult *= (1.0 + t_rate)
            elif t_type == "DEFENCE":
                def_mult *= (1.0 + t_rate)
            elif t_type == "BALANCED":
                att_mult *= (1.0 + t_rate)
                def_mult *= (1.0 + t_rate)
            elif t_type == "COUNTER_ATTACKING":
                clearance_bonus += t_rate

    return att_mult, def_mult, clearance_bonus, primary_type


def build_team_data(
    cards: list,          # list of UserCard ORM instances or PlayerSnapshot instances
    display_name: str,
    tactic: str = "balanced",
    logo_bonus: int = 0,
    manager=None,
    formation: str = "433",
) -> TeamData:
    """
    Converts a list of UserCard ORM objects into a TeamData snapshot.
    Applies RFM27 line-density scaling, captain boosts, and tactical modifiers.
    """
    players: list[PlayerSnapshot] = []
    gk: Optional[PlayerSnapshot] = None
    attackers: list[PlayerSnapshot] = []
    midfielders: list[PlayerSnapshot] = []
    defenders: list[PlayerSnapshot] = []

    for card in cards:
        if isinstance(card, PlayerSnapshot):
            snap = card
        else:
            t = getattr(card, "template", card)
            atk = getattr(t, "attack_stat", 50)
            df = getattr(t, "defence_stat", 50)
            atk_boost = getattr(t, "attack_boost_pct", 0)
            def_boost = getattr(t, "defence_boost_pct", 0)
            tier = getattr(card, "upgrade_tier", "NONE")
            tier_boost = 0
            if tier == "SILVER":
                tier_boost = 5
            elif tier == "GOLD":
                tier_boost = 10
            elif tier == "DIAMOND":
                tier_boost = 25
            total_atk_boost = atk_boost + tier_boost
            total_def_boost = def_boost + tier_boost
            if total_atk_boost > 0:
                atk = int(atk * (1 + total_atk_boost / 100.0))
            if total_def_boost > 0:
                df = int(df * (1 + total_def_boost / 100.0))
            pos = getattr(t, "position", "CM")
            snap = PlayerSnapshot(
                name=getattr(t, "name", "Player"),
                position=pos,
                attack=atk,
                defence=df,
                ovr=getattr(t, "ovr", None) or max(atk, df),
                slot="",
            )
        players.append(snap)

        if snap.position == "GK":
            gk = snap
        elif snap.position in ("LW", "ST", "RW"):
            attackers.append(snap)
        elif snap.position in ("CAM", "CM", "CDM"):
            midfielders.append(snap)
        elif snap.position in ("LB", "CB", "RB"):
            defenders.append(snap)
        else:
            # Fallback by stat dominance
            if snap.attack >= snap.defence:
                attackers.append(snap)
            else:
                defenders.append(snap)

    # Balance midfield if none present
    if not midfielders and (attackers or defenders):
        if attackers:
            midfielders.append(attackers[-1])
        if defenders:
            midfielders.append(defenders[0])

    # RFM27 Captain: highest OVR player provides captain influence boost
    captain = max(players, key=lambda p: p.ovr) if players else None
    captain_bonus = (captain.ovr / 200.0) if captain else 0.0

    att_mod, mid_mod, def_mod, _ = TACTIC_MODS.get(tactic, (1.0, 1.0, 1.0, 0.28))

    att_boost_m, def_boost_m, clearance_bonus, mgr_boost_type = calculate_manager_boosts(manager, formation, tactic)
    att_mod *= att_boost_m
    def_mod *= def_boost_m

    raw_att = _avg([p.attack for p in attackers]) * att_mod + logo_bonus + captain_bonus if attackers else 50.0
    raw_mid = _avg([(p.attack + p.defence) / 2 for p in midfielders]) * mid_mod + logo_bonus + captain_bonus if midfielders else 50.0
    raw_def = _avg([p.defence for p in defenders]) * def_mod + logo_bonus + captain_bonus if defenders else 50.0
    raw_gk = (gk.defence + logo_bonus) if gk else 50.0

    # Apply RFM27 formation density multipliers
    adj_def, adj_mid, adj_att = alter_average_based_on_players_per_line(
        len(defenders), len(midfielders), len(attackers),
        raw_def, raw_mid, raw_att
    )

    return TeamData(
        display_name=display_name,
        players=players,
        gk=gk,
        attackers=attackers,
        midfielders=midfielders,
        defenders=defenders,
        att_rating=adj_att,
        mid_rating=adj_mid,
        def_rating=adj_def,
        gk_rating=raw_gk,
        tactic=tactic,
        logo_bonus=logo_bonus,
        manager_boost_type=mgr_boost_type,
        manager=manager,
        manager_clearance_bonus=clearance_bonus,
        formation=formation,
        captain=captain,
    )


# ── Commentary Templates ─────────────────────────────────────────

_GOAL_TEMPLATES = [
    "⚽ **GOAL!** {shooter} strikes cleanly into the back of the net!",
    "⚽ **GOAL!** A clinical finish from {shooter}! The keeper stood no chance!",
    "⚽ **GOAL!** What a thunderbolt from {shooter}! Unbelievable strike!",
    "⚽ **GOAL!** {shooter} rises highest and heads it powerfully home!",
    "⚽ **GOAL!** {shooter} curls it gracefully into the top corner!",
    "⚽ **GOAL!** {shooter} pounces on the loose ball and buries it!",
    "⚽ **GOAL!** Superb footwork from {shooter} to beat the defense and score!",
]

_SAVE_TEMPLATES = [
    "🧤 Sensational reflex save! The keeper denies {shooter}'s drive!",
    "🧤 What a stop! The goalkeeper dives full-stretch to keep {shooter} out!",
    "🧤 Brilliant goalkeeping! {shooter}'s shot is tipped around the post!",
    "🧤 The keeper stands tall and parries away {shooter}'s effort!",
]

_MISS_TEMPLATES = [
    "💨 {shooter} strikes, but the ball rattles against the crossbar!",
    "💨 {shooter} fires wide of the near post! Golden opportunity missed!",
    "💨 Off the woodwork! {shooter} inches away from scoring!",
    "💨 {shooter} sends his shot flying over the bar!",
]

_PENALTY_GOAL_TEMPLATES = [
    "🎯 **PENALTY GOAL!** {shooter} calmly steps up and sends the keeper the wrong way!",
    "🎯 **PENALTY GOAL!** Struck with pinpoint power! {shooter} converts from the spot!",
]

_PENALTY_SAVE_TEMPLATES = [
    "🧤 **PENALTY SAVED!** The keeper guesses right and denies {shooter}!",
    "🧤 **PENALTY SAVED!** Heroic save from 12 yards to stop {shooter}!",
]

_PENALTY_MISS_TEMPLATES = [
    "💨 **PENALTY MISSED!** {shooter} blazes the spot-kick wide of the goal!",
    "💨 **PENALTY MISSED!** {shooter} strikes the post from the penalty spot!",
]

_COUNTER_TEMPLATES = [
    "⚡ Quick clearance! {team} break into a blistering counter-attack!",
    "⚡ Lightning transition! {team} launch forward against an exposed defense!",
]

_MID_BATTLE_TEMPLATES = [
    "⚙️ {team} dictate the tempo and seize control of the midfield battle.",
    "⚙️ Crisp passing and strong midfield press by {team}.",
]

_CHANCE_TEMPLATES = [
    "🔥 {team} carve through the defense into a dangerous goalscoring position!",
    "🔥 Overload in the final third! Dangerous chance for {team}!",
]

_CARD_YELLOW_TEMPLATES = [
    "🟨 Yellow card! {player} is cautioned for a tactical foul.",
    "🟨 The referee shows {player} a yellow card for a reckless challenge.",
]

_CARD_RED_TEMPLATES = [
    "🟥 **RED CARD!** {player} receives a second yellow card and is sent off!",
    "🟥 **RED CARD!** Straight red! {player} dismissed for violent conduct!",
]


# ── Simulation Helper Methods ─────────────────────────────────────

def _recalculate_ratings(team: TeamData) -> None:
    """Updates team line ratings dynamically when tactics change, players are sent off, or injured."""
    active_def = [p for p in team.defenders if not p.is_sent_off]
    active_mid = [p for p in team.midfielders if not p.is_sent_off]
    active_att = [p for p in team.attackers if not p.is_sent_off]

    att_mod, mid_mod, def_mod, _ = TACTIC_MODS.get(team.tactic, (1.0, 1.0, 1.0, 0.28))
    att_boost_m, def_boost_m, clearance_bonus, _ = calculate_manager_boosts(team.manager, team.formation, team.tactic)
    att_mod *= att_boost_m
    def_mod *= def_boost_m
    team.manager_clearance_bonus = clearance_bonus

    captain_bonus = (team.captain.ovr / 200.0) if team.captain and not team.captain.is_sent_off else 0.0

    raw_def = (_avg([p.defence for p in active_def]) * def_mod + team.logo_bonus + captain_bonus) if active_def else 30.0
    raw_mid = (_avg([(p.attack + p.defence) / 2 for p in active_mid]) * mid_mod + team.logo_bonus + captain_bonus) if active_mid else 30.0
    raw_att = (_avg([p.attack for p in active_att]) * att_mod + team.logo_bonus + captain_bonus) if active_att else 30.0

    adj_d, adj_m, adj_a = alter_average_based_on_players_per_line(
        len(active_def), len(active_mid), len(active_att),
        raw_def, raw_mid, raw_att
    )
    team.def_rating = adj_d
    team.mid_rating = adj_m
    team.att_rating = adj_a


def change_team_tactic(team: TeamData, new_tactic: str) -> bool:
    """
    Changes a team's tactic midgame and recalculates all line ratings.
    Returns True if changed, False if already on that tactic or invalid.
    """
    new_tactic = (new_tactic or "").lower()
    if new_tactic not in TACTIC_MODS:
        return False
    if team.tactic.lower() == new_tactic:
        return False
    team.tactic = new_tactic
    _recalculate_ratings(team)
    return True


def _pick_shooter(team: TeamData) -> PlayerSnapshot:
    """
    Port of RFM27 choosePlayerAndShoot shooter selection:
    60% Forward, 30% Midfielder, 10% Defender.
    Filters out sent off players with automatic line fallbacks.
    """
    roll = random.random() * 100.0
    active_att = [p for p in team.attackers if not p.is_sent_off]
    active_mid = [p for p in team.midfielders if not p.is_sent_off]
    active_def = [p for p in team.defenders if not p.is_sent_off]

    if roll <= 60 and active_att:
        return random.choice(active_att)
    elif roll <= 90 and active_mid:
        return random.choice(active_mid)
    elif active_def:
        return random.choice(active_def)
    elif active_att:
        return random.choice(active_att)
    elif active_mid:
        return random.choice(active_mid)

    avail = [p for p in team.players if not p.is_sent_off]
    return random.choice(avail) if avail else (team.gk or team.players[0])


def _calculate_assist(team: TeamData, scorer: PlayerSnapshot) -> Optional[PlayerSnapshot]:
    """
    Port of RFM27 calculateAssist logic:
    Roll 1-100:
    <= 35: another forward (if >1 FWD available) else midfielder
    >= 87: defender
    36-86: midfielder
    Candidate is chosen via roulette wheel weighted by player OVR/skill.
    """
    active_att = [p for p in team.attackers if not p.is_sent_off and p != scorer]
    active_mid = [p for p in team.midfielders if not p.is_sent_off and p != scorer]
    active_def = [p for p in team.defenders if not p.is_sent_off and p != scorer]

    roll = random.randint(1, 100)
    candidates: list[PlayerSnapshot] = []

    if roll <= 35:
        candidates = active_att if len(active_att) >= 1 else active_mid
    elif roll >= 87:
        candidates = active_def
    else:
        candidates = active_mid

    if not candidates:
        all_candidates = [p for p in team.players if not p.is_sent_off and p != scorer and p.position != "GK"]
        candidates = all_candidates

    if not candidates:
        return None

    # Weighted roulette selection by skill/ovr
    total_skill = sum(c.ovr for c in candidates)
    if total_skill <= 0:
        return random.choice(candidates)
    r = random.uniform(0, total_skill)
    cum = 0.0
    for c in candidates:
        cum += c.ovr
        if r <= cum:
            return c
    return candidates[-1]


# ── Stateful Live Simulation Session (RFM27 Port with Real-Time Tactics) ──

class MatchSimulationSession:
    """
    Interactive live match simulation engine ported from RFM27 GameEventSimulator.
    Allows managers to dynamically change team strategies midgame via Discord UI buttons,
    immediately recalculating ratings and shifting the tactical balance of the match.
    """

    def __init__(self, home: TeamData, away: TeamData):
        self.home = home
        self.away = away
        self.home_score = 0
        self.away_score = 0
        self.home_total_shots = 0
        self.away_total_shots = 0
        self.home_shots_on_target = 0
        self.away_shots_on_target = 0
        self.home_cards: list[str] = []
        self.away_cards: list[str] = []
        self.all_events: list[MatchEvent] = []

        # Total match phases (15 to 18 phases, each ~5-6 minutes)
        self.total_phases = random.randint(15, 18)
        self.half_phase = self.total_phases // 2
        self.current_step = 0
        self.current_minute = 0

        # RFM27 Superiority & Advantage Boosts
        self.boost_factor = 1.05
        self.home_boost = 1.04

        # State machine
        self.phase_state = "MIDDLE"
        self.possession_team = home if random.random() < 0.50 else away

        # Momentum
        total_mid = home.mid_rating + away.mid_rating
        if total_mid > 0:
            self.current_home_momentum = (home.mid_rating / total_mid) * 100.0
        else:
            self.current_home_momentum = 50.0
        self.current_home_momentum = max(35.0, min(65.0, self.current_home_momentum))

        self.is_half_time_done = False
        self.is_finished = False

    def change_tactic(self, team_side: str, new_tactic: str) -> bool:
        """
        Dynamically changes a team's tactic midgame.
        Recalculates line ratings immediately.
        """
        team = self.home if team_side == "home" else self.away
        return change_team_tactic(team, new_tactic)

    def step(self) -> list[MatchEvent]:
        """
        Simulates the next phase step and returns newly generated events.
        If the match is already finished, returns [].
        """
        if self.is_finished:
            return []

        step_events: list[MatchEvent] = []
        step = self.current_step
        self.current_step += 1

        # Calculate dynamic minute (RFM27 calculateMinute)
        is_first_half = (step < self.half_phase)
        base_min = 0 if is_first_half else 45
        half_steps = self.half_phase if is_first_half else (self.total_phases - self.half_phase)
        step_in_half = step if is_first_half else (step - self.half_phase)

        fraction = step_in_half / max(1, half_steps)
        minute = int(base_min + fraction * 43 + random.randint(0, 3))
        if is_first_half:
            minute = min(minute, 45)
        else:
            minute = min(max(minute, 46), 90)
        self.current_minute = minute

        # Half-Time Event
        if step == self.half_phase and not self.is_half_time_done:
            self.is_half_time_done = True
            self.current_home_momentum = self.current_home_momentum * 0.7 + 50.0 * 0.3
            step_events.append(MatchEvent(
                minute=45,
                text="⏱️ **Half Time!** Players head to the dressing rooms.",
                event_type="half_time",
                phase_title="⏱️ HALF TIME",
                zone=2,
                home_momentum=int(round(self.current_home_momentum)),
            ))
            self.phase_state = "MIDDLE"
            self.possession_team = self.away if self.possession_team == self.home else self.home
            self.all_events.extend(step_events)
            return step_events

        # ── Disciplinary Incidents (RFM27 calculateRedCardsAndInjuries) ──
        if random.random() < 0.07:  # ~7% chance of disciplinary check
            card_team = self.home if random.random() < 0.50 else self.away
            is_home_card = (card_team == self.home)
            card_side = "home" if is_home_card else "away"
            avail_players = [p for p in card_team.players if not p.is_sent_off and p.position != "GK"]
            if avail_players:
                fouler = random.choice(avail_players)
                fouler.yellow_cards += 1
                card_zone = 1 if is_home_card else 3

                if fouler.yellow_cards == 2:
                    fouler.is_sent_off = True
                    _recalculate_ratings(card_team)
                    if is_home_card:
                        self.home_cards.append("red")
                        self.current_home_momentum = max(15.0, self.current_home_momentum - 6.0)
                    else:
                        self.away_cards.append("red")
                        self.current_home_momentum = min(85.0, self.current_home_momentum + 6.0)

                    step_events.append(MatchEvent(
                        minute=minute,
                        text=_CARD_RED_TEMPLATES[0].format(player=fouler.name),
                        event_type="card",
                        team_side=card_side,
                        phase_title="🟥 RED CARD (2nd Yellow)",
                        zone=card_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        card_type="red",
                    ))
                elif random.random() < 0.02:  # direct red
                    fouler.is_sent_off = True
                    _recalculate_ratings(card_team)
                    if is_home_card:
                        self.home_cards.append("red")
                        self.current_home_momentum = max(15.0, self.current_home_momentum - 8.0)
                    else:
                        self.away_cards.append("red")
                        self.current_home_momentum = min(85.0, self.current_home_momentum + 8.0)

                    step_events.append(MatchEvent(
                        minute=minute,
                        text=_CARD_RED_TEMPLATES[1].format(player=fouler.name),
                        event_type="card",
                        team_side=card_side,
                        phase_title="🟥 STRAIGHT RED CARD",
                        zone=card_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        card_type="red",
                    ))
                else:
                    if is_home_card:
                        self.home_cards.append("yellow")
                    else:
                        self.away_cards.append("yellow")

                    step_events.append(MatchEvent(
                        minute=minute,
                        text=random.choice(_CARD_YELLOW_TEMPLATES).format(player=fouler.name),
                        event_type="card",
                        team_side=card_side,
                        phase_title="🟨 YELLOW CARD",
                        zone=card_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        card_type="yellow",
                    ))

        # ── Phase State Machine ──────────────────────────────────
        # Phase 1: MIDDLE (Midfield Battle)
        if self.phase_state == "MIDDLE":
            home_mid = self.home.mid_rating * self.home_boost
            away_mid = self.away.mid_rating
            if home_mid > away_mid:
                home_mid *= self.boost_factor
            else:
                away_mid *= self.boost_factor

            home_mid_prob = home_mid / (home_mid + away_mid)
            if random.random() < home_mid_prob:
                attacking_team, defending_team = self.home, self.away
                atk_side = "home"
                self.current_home_momentum = min(82.0, self.current_home_momentum + random.uniform(2.0, 3.5))
            else:
                attacking_team, defending_team = self.away, self.home
                atk_side = "away"
                self.current_home_momentum = max(18.0, self.current_home_momentum - random.uniform(2.0, 3.5))

            if random.random() < 0.40:
                step_events.append(MatchEvent(
                    minute=minute,
                    text=random.choice(_MID_BATTLE_TEMPLATES).format(team=attacking_team.display_name),
                    event_type="possession",
                    team_side=atk_side,
                    phase_title="⚙️ MIDFIELD BATTLE",
                    zone=2,
                    home_momentum=int(round(self.current_home_momentum)),
                ))

            self.phase_state = "ATTACKING"
            self.attacking_team = attacking_team
            self.defending_team = defending_team
            self.atk_side = atk_side

        # Phase 2: ATTACKING (Attack vs Defence Battle)
        if self.phase_state == "ATTACKING":
            attacking_team = getattr(self, "attacking_team", self.home)
            defending_team = getattr(self, "defending_team", self.away)
            atk_side = getattr(self, "atk_side", "home")

            atk_power = attacking_team.att_rating * (self.home_boost if atk_side == "home" else 1.0)
            def_power = defending_team.def_rating * (self.home_boost if atk_side == "away" else 1.0)
            if atk_power > def_power:
                atk_power *= self.boost_factor
            else:
                def_power *= self.boost_factor

            atk_prob = atk_power / (atk_power + def_power * 1.18)

            if random.random() < atk_prob:
                self.phase_state = "DANGEROUS"
            else:
                # Defense wins! Check clearance / counter-attack (RFM27 clereanceToAttack)
                _, _, _, clearance_chance = TACTIC_MODS.get(defending_team.tactic, (1.0, 1.0, 1.0, 0.28))
                clearance_chance += defending_team.manager_clearance_bonus
                if defending_team.manager_boost_type == "COUNTER_ATTACKING":
                    clearance_chance += 0.10

                if random.random() < clearance_chance:
                    counter_side = "away" if atk_side == "home" else "home"
                    if counter_side == "home":
                        self.current_home_momentum = min(84.0, self.current_home_momentum + random.uniform(3.0, 5.0))
                    else:
                        self.current_home_momentum = max(16.0, self.current_home_momentum - random.uniform(3.0, 5.0))

                    counter_zone = 3 if counter_side == "home" else 1
                    step_events.append(MatchEvent(
                        minute=minute,
                        text=random.choice(_COUNTER_TEMPLATES).format(team=defending_team.display_name),
                        event_type="chance",
                        team_side=counter_side,
                        phase_title="⚡ FAST COUNTER ATTACK!",
                        zone=counter_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                    ))
                    attacking_team, defending_team = defending_team, attacking_team
                    atk_side = counter_side
                    self.attacking_team = attacking_team
                    self.defending_team = defending_team
                    self.atk_side = atk_side
                    self.phase_state = "DANGEROUS"
                else:
                    self.phase_state = "MIDDLE"
                    if not step_events:
                        step_events.append(MatchEvent(
                            minute=minute,
                            text=f"🛡️ Strong defensive interception by **{defending_team.display_name}** to break up the attack.",
                            event_type="possession",
                            team_side=atk_side,
                            phase_title="🛡️ DEFENSIVE STOP",
                            zone=1 if atk_side == "away" else 3,
                            home_momentum=int(round(self.current_home_momentum)),
                        ))
                    self.all_events.extend(step_events)
                    if step >= self.total_phases - 1:
                        self._finish(step_events)
                    return step_events

        # Phase 3: DANGEROUS (Chance Creation & Penalty/Shot resolution)
        if self.phase_state == "DANGEROUS":
            attacking_team = getattr(self, "attacking_team", self.home)
            defending_team = getattr(self, "defending_team", self.away)
            atk_side = getattr(self, "atk_side", "home")

            threat_zone = 4 if atk_side == "home" else 0

            if atk_side == "home":
                self.current_home_momentum = min(85.0, self.current_home_momentum + random.uniform(3.0, 5.0))
            else:
                self.current_home_momentum = max(15.0, self.current_home_momentum - random.uniform(3.0, 5.0))

            step_events.append(MatchEvent(
                minute=minute,
                text=random.choice(_CHANCE_TEMPLATES).format(team=attacking_team.display_name),
                event_type="chance",
                team_side=atk_side,
                phase_title="🔥 DANGEROUS OPPORTUNITY",
                zone=threat_zone,
                home_momentum=int(round(self.current_home_momentum)),
            ))

            # Penalty chance (RFM27 canConcedePenalty & PENALTY_PROBABILITY)
            can_penalty = (10 <= minute <= 85)
            is_penalty = can_penalty and (random.random() < 0.06)

            if is_penalty:
                shooter = _pick_shooter(attacking_team)
                if atk_side == "home":
                    self.home_total_shots += 1
                else:
                    self.away_total_shots += 1

                p_roll = random.random()
                if p_roll < 0.75:
                    if atk_side == "home":
                        self.home_score += 1
                        self.home_shots_on_target += 1
                        self.current_home_momentum = min(88.0, self.current_home_momentum + 7.0)
                    else:
                        self.away_score += 1
                        self.away_shots_on_target += 1
                        self.current_home_momentum = max(12.0, self.current_home_momentum - 7.0)

                    step_events.append(MatchEvent(
                        minute=minute,
                        text=random.choice(_PENALTY_GOAL_TEMPLATES).format(shooter=shooter.name),
                        event_type="goal",
                        scorer_name=shooter.name,
                        team_side=atk_side,
                        phase_title="🎯 PENALTY GOAL!",
                        zone=threat_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        is_shot=True,
                        is_shot_on_target=True,
                    ))
                elif p_roll < 0.90:
                    if atk_side == "home":
                        self.home_shots_on_target += 1
                    else:
                        self.away_shots_on_target += 1

                    step_events.append(MatchEvent(
                        minute=minute,
                        text=random.choice(_PENALTY_SAVE_TEMPLATES).format(shooter=shooter.name),
                        event_type="save",
                        team_side=atk_side,
                        phase_title="🧤 PENALTY SAVED!",
                        zone=threat_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        is_shot=True,
                        is_shot_on_target=True,
                    ))
                else:
                    step_events.append(MatchEvent(
                        minute=minute,
                        text=random.choice(_PENALTY_MISS_TEMPLATES).format(shooter=shooter.name),
                        event_type="miss",
                        team_side=atk_side,
                        phase_title="💨 PENALTY MISSED!",
                        zone=threat_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        is_shot=True,
                        is_shot_on_target=False,
                    ))
                self.phase_state = "MIDDLE"

            else:
                # Open Play Shot
                shooter = _pick_shooter(attacking_team)
                gk = defending_team.gk or PlayerSnapshot("Goalkeeper", "GK", 50, 50, 50)

                if atk_side == "home":
                    self.home_total_shots += 1
                else:
                    self.away_total_shots += 1

                shooter_factor = shooter.attack * (self.home_boost if atk_side == "home" else 1.0)
                gk_factor = gk.defence * (self.home_boost if atk_side == "away" else 1.0) * 2.15

                goal_prob = shooter_factor / (shooter_factor + gk_factor)
                shot_roll = random.uniform(0.0, 1.0)

                if shot_roll < goal_prob:
                    if atk_side == "home":
                        self.home_score += 1
                        self.home_shots_on_target += 1
                        self.current_home_momentum = min(88.0, self.current_home_momentum + 6.0)
                    else:
                        self.away_score += 1
                        self.away_shots_on_target += 1
                        self.current_home_momentum = max(12.0, self.current_home_momentum - 6.0)

                    assister = _calculate_assist(attacking_team, shooter)
                    if assister:
                        goal_text = random.choice(_GOAL_TEMPLATES).format(shooter=shooter.name) + f" *(Assist: {assister.name})*"
                        assist_name = assister.name
                    else:
                        goal_text = random.choice(_GOAL_TEMPLATES).format(shooter=shooter.name)
                        assist_name = None

                    step_events.append(MatchEvent(
                        minute=minute,
                        text=goal_text,
                        event_type="goal",
                        scorer_name=shooter.name,
                        assist_name=assist_name,
                        team_side=atk_side,
                        phase_title="⚽ GOAL!",
                        zone=threat_zone,
                        home_momentum=int(round(self.current_home_momentum)),
                        is_shot=True,
                        is_shot_on_target=True,
                    ))
                else:
                    if shot_roll < (goal_prob + 0.28):
                        if atk_side == "home":
                            self.home_shots_on_target += 1
                        else:
                            self.away_shots_on_target += 1

                        step_events.append(MatchEvent(
                            minute=minute,
                            text=random.choice(_SAVE_TEMPLATES).format(shooter=shooter.name),
                            event_type="save",
                            team_side=atk_side,
                            phase_title="🧤 SENSATIONAL SAVE!",
                            zone=threat_zone,
                            home_momentum=int(round(self.current_home_momentum)),
                            is_shot=True,
                            is_shot_on_target=True,
                        ))
                    else:
                        step_events.append(MatchEvent(
                            minute=minute,
                            text=random.choice(_MISS_TEMPLATES).format(shooter=shooter.name),
                            event_type="miss",
                            team_side=atk_side,
                            phase_title="💨 SHOT OFF TARGET!",
                            zone=threat_zone,
                            home_momentum=int(round(self.current_home_momentum)),
                            is_shot=True,
                            is_shot_on_target=False,
                        ))

                self.phase_state = "MIDDLE"

        # Check if final step
        if step >= self.total_phases - 1:
            self._finish(step_events)

        self.all_events.extend(step_events)
        return step_events

    def _finish(self, step_events: list[MatchEvent]):
        self.is_finished = True
        step_events.append(MatchEvent(
            minute=90,
            text="🏁 **Full Time!** The referee blows the final whistle.",
            event_type="full_time",
            phase_title="🏁 FULL TIME",
            zone=2,
            home_momentum=int(round(self.current_home_momentum)),
        ))

    def get_result(self) -> MatchResult:
        order = {"possession": 1, "card": 2, "chance": 3, "save": 4, "miss": 5, "goal": 6, "half_time": 7, "full_time": 8}
        sorted_events = sorted(self.all_events, key=lambda e: (e.minute, order.get(e.event_type, 99)))
        return MatchResult(
            home_score=self.home_score,
            away_score=self.away_score,
            events=sorted_events,
            home_name=self.home.display_name,
            away_name=self.away.display_name,
            home_total_shots=self.home_total_shots,
            away_total_shots=self.away_total_shots,
            home_shots_on_target=self.home_shots_on_target,
            away_shots_on_target=self.away_shots_on_target,
            home_cards=self.home_cards,
            away_cards=self.away_cards,
        )

    def run_all(self) -> MatchResult:
        while not self.is_finished:
            self.step()
        return self.get_result()


# ── Standalone Wrapper (100% Backwards Compatible) ───────────────

def simulate_match(home: TeamData, away: TeamData) -> MatchResult:
    """
    Runs the full 90-minute simulation using the RFM27 state machine and mathematics.
    Returns a MatchResult with chronological events compatible with MatchCog live feed.
    """
    session = MatchSimulationSession(home, away)
    return session.run_all()

