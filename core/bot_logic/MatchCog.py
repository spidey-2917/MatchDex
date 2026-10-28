import asyncio

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, UserCard, UserLogo
from core.objectives import update_objective_progress
from core.bot_logic.quick_sim import (
    build_team_data,
    render_momentum_bar,
    render_pitch_radar,
    simulate_match,
    MatchSimulationSession,
)

# Formation -> how many of each slot type are needed
FORMATION_SLOTS = {
    "442": {"gk": 1, "df": 4, "md": 4, "at": 2},
    "433": {"gk": 1, "df": 4, "md": 3, "at": 3},
    "352": {"gk": 1, "df": 3, "md": 5, "at": 2},
    "4231": {"gk": 1, "df": 4, "md": 5, "at": 1},
    "541": {"gk": 1, "df": 5, "md": 4, "at": 1},
    "4141": {"gk": 1, "df": 4, "md": 5, "at": 1},
    "343": {"gk": 1, "df": 3, "md": 4, "at": 3},
    "424": {"gk": 1, "df": 4, "md": 2, "at": 4},
}

ACTIVE_MATCHES = {}


# ── DB helpers (all wrapped for async) ──────────────────────────


@sync_to_async
def load_lineup_cards(uid):
    lineup = Lineup.objects.select_related(
        "gk__template",
        "df1__template",
        "df2__template",
        "df3__template",
        "df4__template",
        "df5__template",
        "md1__template",
        "md2__template",
        "md3__template",
        "md4__template",
        "md5__template",
        "at1__template",
        "at2__template",
        "at3__template",
        "at4__template",
        "sub1__template",
        "sub2__template",
        "sub3__template",
    ).get(owner__discord_id=uid, is_active=True)
    cards = [
        getattr(lineup, s)
        for s in [
            "gk",
            "df1",
            "df2",
            "df3",
            "df4",
            "df5",
            "md1",
            "md2",
            "md3",
            "md4",
            "md5",
            "at1",
            "at2",
            "at3",
            "at4",
        ]
        if getattr(lineup, s)
    ]
    subs = [getattr(lineup, s) for s in ["sub1", "sub2", "sub3"] if getattr(lineup, s)]
    return cards, subs


@sync_to_async
def get_logo_bonus(uid):
    try:
        ul = UserLogo.objects.select_related("logo").get(owner__discord_id=uid)
        return ul.logo.bonus
    except UserLogo.DoesNotExist:
        return 0


# ════════════════════════════════════════════════════════════════
#  STEP 1 — Accept challenge
# ════════════════════════════════════════════════════════════════


class AcceptView(discord.ui.View):
    def __init__(self, p1, p2, cog):
        super().__init__(timeout=60)
        self.p1, self.p2, self.cog = p1, p2, cog
        self._processing = False

    @discord.ui.button(label="Accept Match", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            return await interaction.response.send_message(
                "Only the challenged player can accept!", ephemeral=True
            )
        if self._processing:
            return await interaction.response.send_message(
                "Already processing…", ephemeral=True
            )
        self._processing = True
        self.stop()
        await interaction.response.defer()
        await self.cog.start_match(interaction, self.p1, self.p2)


# ════════════════════════════════════════════════════════════════
#  STEP 2 — Chooser picks Attack/Defence  (public buttons, private result)
# ════════════════════════════════════════════════════════════════


class RoleView(discord.ui.View):
    """Public message with Attack / Defence / Sub buttons. Only the chooser can interact."""

    def __init__(self, match_id, cog, chooser_id):
        super().__init__(timeout=120)
        self.match_id = match_id
        self.cog = cog
        self.chooser_id = chooser_id
        self.used = False

    @discord.ui.button(label="⚔️ Attack", style=discord.ButtonStyle.danger)
    async def attack_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.chooser_id:
            return await interaction.response.send_message(
                "It's not your turn to choose!", ephemeral=True
            )
        if self.used:
            return await interaction.response.send_message(
                "You already chose!", ephemeral=True
            )
        self.used = True
        self.stop()
        # Disable buttons on the public message so nobody else can click
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        await self._pick_card(interaction, is_attack=True)

    @discord.ui.button(label="🛡️ Defence", style=discord.ButtonStyle.primary)
    async def defence_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.chooser_id:
            return await interaction.response.send_message(
                "It's not your turn to choose!", ephemeral=True
            )
        if self.used:
            return await interaction.response.send_message(
                "You already chose!", ephemeral=True
            )
        self.used = True
        self.stop()
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        await self._pick_card(interaction, is_attack=False)

    @discord.ui.button(label="🔄 Sub", style=discord.ButtonStyle.secondary)
    async def sub_btn(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.chooser_id:
            return await interaction.response.send_message(
                "It's not your turn!", ephemeral=True
            )
        m = ACTIVE_MATCHES.get(self.match_id)
        if not m:
            return await interaction.response.send_message(
                "Match not found.", ephemeral=True
            )
        p = "p1" if self.chooser_id == m["p1_id"] else "p2"
        if m[f"{p}_subs_used"] >= 3 or not m[f"{p}_subs"]:
            return await interaction.response.send_message(
                "No substitutions left!", ephemeral=True
            )
        opts = [
            discord.SelectOption(
                label=f"{c.template.name} ({c.template.position})",
                value=str(c.id),
                description=f"ATT {c.template.attack_stat} | DEF {c.template.defence_stat}",
            )
            for c in m[f"{p}_subs"]
        ]
        v = discord.ui.View()
        v.add_item(SubSelect(self.match_id, self.chooser_id, opts, self.cog))
        await interaction.response.send_message(
            "Pick a sub to bring on:", view=v, ephemeral=True
        )

    async def _pick_card(self, interaction, is_attack):
        m = ACTIVE_MATCHES.get(self.match_id)
        if not m:
            return
        p = "p1" if self.chooser_id == m["p1_id"] else "p2"
        cards = m[f"{p}_cards"]
        if not cards:
            await interaction.followup.send("No cards left!", ephemeral=True)
            return
        opts = [
            discord.SelectOption(
                label=f"{c.template.name} ({c.template.position})",
                value=str(c.id),
                description=f"ATT {c.template.attack_stat} | DEF {c.template.defence_stat}",
            )
            for c in cards[:25]
        ]
        v = discord.ui.View()
        v.add_item(
            CardSelect(self.match_id, self.chooser_id, is_attack, opts, self.cog)
        )
        # EPHEMERAL followup — only the chooser sees their card list
        await interaction.followup.send(
            f"Select your card to **{'ATTACK' if is_attack else 'DEFEND'}**:",
            view=v,
            ephemeral=True,
        )


# ════════════════════════════════════════════════════════════════
#  Card select dropdown (used for BOTH chooser and responder)
# ════════════════════════════════════════════════════════════════


class CardSelect(discord.ui.Select):
    def __init__(self, match_id, player_id, is_attack, options, cog):
        super().__init__(placeholder="Pick your card...", options=options)
        self.match_id = match_id
        self.player_id = player_id
        self.is_attack = is_attack
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message(
                "Not your pick!", ephemeral=True
            )
        m = ACTIVE_MATCHES.get(self.match_id)
        if not m:
            return await interaction.response.send_message(
                "Match expired.", ephemeral=True
            )

        card_id = int(self.values[0])
        p = "p1" if self.player_id == m["p1_id"] else "p2"
        card = next((c for c in m[f"{p}_cards"] if c.id == card_id), None)
        if not card:
            return await interaction.response.send_message(
                "Card not in your hand!", ephemeral=True
            )

        role = "ATTACK" if self.is_attack else "DEFENCE"
        m[f"{p}_choice"] = {"card": card, "stat_type": role}
        m[f"{p}_cards"] = [c for c in m[f"{p}_cards"] if c.id != card_id]

        # Private confirmation
        await interaction.response.edit_message(
            content=f"✅ You picked **{card.template.name}** as **{role}**!", view=None
        )

        # Check if both have chosen
        if m["p1_choice"] and m["p2_choice"]:
            await self.cog.resolve_round(self.match_id)
        else:
            # The other player still needs to pick — prompt them
            await self.cog.prompt_responder(self.match_id)


class SubSelect(discord.ui.Select):
    def __init__(self, match_id, player_id, options, cog):
        super().__init__(placeholder="Pick a substitute...", options=options)
        self.match_id = match_id
        self.player_id = player_id
        self.cog = cog

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.player_id:
            return await interaction.response.send_message("Not yours!", ephemeral=True)
        m = ACTIVE_MATCHES.get(self.match_id)
        if not m:
            return
        p = "p1" if self.player_id == m["p1_id"] else "p2"
        sub_id = int(self.values[0])
        sub = next((c for c in m[f"{p}_subs"] if c.id == sub_id), None)
        if not sub:
            return await interaction.response.send_message(
                "Sub unavailable!", ephemeral=True
            )
        m[f"{p}_cards"].append(sub)
        m[f"{p}_subs"] = [c for c in m[f"{p}_subs"] if c.id != sub_id]
        m[f"{p}_subs_used"] += 1
        await interaction.response.edit_message(
            content=f"🔄 **{sub.template.name}** subbed on! ({3 - m[f'{p}_subs_used']} left)",
            view=None,
        )


# ════════════════════════════════════════════════════════════════
#  STEP 3 — Responder picks (button → ephemeral dropdown)
# ════════════════════════════════════════════════════════════════


class ResponderView(discord.ui.View):
    """Public message telling the responder to pick. Only they can click."""

    def __init__(self, match_id, responder_id, forced_role, cog):
        super().__init__(timeout=120)
        self.match_id = match_id
        self.responder_id = responder_id
        self.forced_role = forced_role
        self.cog = cog
        self.used = False

    @discord.ui.button(label="🃏 Pick Your Card", style=discord.ButtonStyle.success)
    async def pick(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.responder_id:
            return await interaction.response.send_message(
                "This isn't your turn!", ephemeral=True
            )
        if self.used:
            return await interaction.response.send_message(
                "You already picked!", ephemeral=True
            )
        self.used = True
        self.stop()
        # Disable buttons so nobody else can click
        for item in self.children:
            if isinstance(item, (discord.ui.Button, discord.ui.Select)):
                item.disabled = True
        await interaction.response.edit_message(view=self)

        m = ACTIVE_MATCHES.get(self.match_id)
        if not m:
            return
        p = "p1" if self.responder_id == m["p1_id"] else "p2"
        cards = m[f"{p}_cards"]
        if not cards:
            await interaction.followup.send("No cards left!", ephemeral=True)
            return
        is_att = self.forced_role == "ATTACK"
        opts = [
            discord.SelectOption(
                label=f"{c.template.name} ({c.template.position})",
                value=str(c.id),
                description=f"ATT {c.template.attack_stat} | DEF {c.template.defence_stat}",
            )
            for c in cards[:25]
        ]
        v = discord.ui.View()
        v.add_item(CardSelect(self.match_id, self.responder_id, is_att, opts, self.cog))
        # EPHEMERAL followup — only the responder sees their cards
        await interaction.followup.send(
            f"Select your card for **{self.forced_role}**:", view=v, ephemeral=True
        )

    @discord.ui.button(label="🔄 Sub First", style=discord.ButtonStyle.secondary)
    async def sub_first(self, interaction: discord.Interaction, btn):
        if interaction.user.id != self.responder_id:
            return await interaction.response.send_message(
                "Not your turn!", ephemeral=True
            )
        m = ACTIVE_MATCHES.get(self.match_id)
        if not m:
            return await interaction.response.send_message(
                "Match expired.", ephemeral=True
            )
        p = "p1" if self.responder_id == m["p1_id"] else "p2"
        if m[f"{p}_subs_used"] >= 3 or not m[f"{p}_subs"]:
            return await interaction.response.send_message(
                "No subs left!", ephemeral=True
            )
        opts = [
            discord.SelectOption(
                label=f"{c.template.name} ({c.template.position})",
                value=str(c.id),
                description=f"ATT {c.template.attack_stat} | DEF {c.template.defence_stat}",
            )
            for c in m[f"{p}_subs"]
        ]
        v = discord.ui.View()
        v.add_item(SubSelect(self.match_id, self.responder_id, opts, self.cog))
        await interaction.response.send_message("Pick a sub:", view=v, ephemeral=True)


# ════════════════════════════════════════════════════════════════
#  THE COG
# ════════════════════════════════════════════════════════════════


class MatchCog(commands.Cog, name="Matches"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(
        name="match_start", description="Challenge another user to a Matchdex game"
    )
    async def match_start(
        self, interaction: discord.Interaction, opponent: discord.Member
    ):
        await interaction.response.defer()

        if opponent.id == interaction.user.id:
            return await interaction.followup.send(
                "You can't challenge yourself!", ephemeral=True
            )
        if opponent.bot:
            return await interaction.followup.send(
                "You can't challenge a bot!", ephemeral=True
            )

        # Already in a match (normal or quick sim)?
        for m in ACTIVE_MATCHES.values():
            if interaction.user.id in (m["p1_id"], m["p2_id"]):
                return await interaction.followup.send(
                    "You're already in a match!", ephemeral=True
                )
            if opponent.id in (m["p1_id"], m["p2_id"]):
                return await interaction.followup.send(
                    f"**{opponent.name}** is already in a match!", ephemeral=True
                )

        if interaction.user.id in ACTIVE_QUICKSIMS:
            return await interaction.followup.send(
                "You're already in a Quick Sim!", ephemeral=True
            )
        if opponent.id in ACTIVE_QUICKSIMS:
            return await interaction.followup.send(
                f"**{opponent.name}** is already in a Quick Sim!", ephemeral=True
            )

        # Lineup check
        async def has_full_lineup(uid):
            user, _ = await DiscordUser.objects.aget_or_create(discord_id=uid)
            lineup = await Lineup.objects.filter(owner=user, is_active=True).afirst()
            if not lineup or not lineup.gk_id:
                return False
            req = FORMATION_SLOTS.get(
                lineup.formation, {"gk": 1, "df": 4, "md": 3, "at": 3}
            )
            if (
                sum(1 for i in range(1, 6) if getattr(lineup, f"df{i}_id", None))
                < req["df"]
            ):
                return False
            if (
                sum(1 for i in range(1, 6) if getattr(lineup, f"md{i}_id", None))
                < req["md"]
            ):
                return False
            if (
                sum(1 for i in range(1, 4) if getattr(lineup, f"at{i}_id", None))
                < req["at"]
            ):
                return False
            return True

        if not await has_full_lineup(interaction.user.id):
            return await interaction.followup.send(
                "You don't have a full lineup!", ephemeral=True
            )
        if not await has_full_lineup(opponent.id):
            return await interaction.followup.send(
                f"**{opponent.name}** doesn't have a full lineup.", ephemeral=True
            )

        await interaction.followup.send(
            f"⚔️ {opponent.mention}, you have been challenged by {interaction.user.mention}!",
            view=AcceptView(interaction.user, opponent, self),
        )

    @app_commands.command(name="match_cancel", description="Cancel your active match or Quick Sim")
    async def match_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Check normal matches first
        match_id_to_cancel = None
        for mid, m in ACTIVE_MATCHES.items():
            if interaction.user.id in (m["p1_id"], m["p2_id"]):
                match_id_to_cancel = mid
                break

        if match_id_to_cancel:
            m = ACTIVE_MATCHES[match_id_to_cancel]
            await interaction.followup.send(
                f"❌ The match between **{m['p1_name']}** and **{m['p2_name']}** has been canceled by {interaction.user.mention}."
            )
            del ACTIVE_MATCHES[match_id_to_cancel]
            return

        # Check Quick Sims
        if interaction.user.id in ACTIVE_QUICKSIMS:
            ACTIVE_QUICKSIMS[interaction.user.id] = "cancelled"
            await interaction.followup.send(
                f"❌ Quick Sim cancelled by {interaction.user.mention}."
            )
            return

        await interaction.followup.send(
            "You do not have an active match to cancel.", ephemeral=True
        )

    # ── Start the match ─────────────────────────────────────────
    async def start_match(self, interaction, p1, p2):
        mid = f"match_{p1.id}_{p2.id}"
        p1_cards, p1_subs = await load_lineup_cards(p1.id)
        p2_cards, p2_subs = await load_lineup_cards(p2.id)
        channel = interaction.channel

        ACTIVE_MATCHES[mid] = {
            "p1_id": p1.id,
            "p2_id": p2.id,
            "p1_name": p1.display_name,
            "p2_name": p2.display_name,
            "p1_cards": p1_cards,
            "p2_cards": p2_cards,
            "p1_subs": p1_subs,
            "p2_subs": p2_subs,
            "p1_subs_used": 0,
            "p2_subs_used": 0,
            "round": 1,
            "p1_score": 0,
            "p2_score": 0,
            "p1_choice": None,
            "p2_choice": None,
            "chooser_id": p1.id,
            "channel": channel,
            "highlights": [],
            "main_message": None,
        }
        await self.send_round(mid)

    # ── Send round prompt ───────────────────────────────────────
    async def send_round(self, mid):
        m = ACTIVE_MATCHES[mid]
        ch = m["channel"]
        chooser_id = m["chooser_id"]

        embed = discord.Embed(
            title="🏟️ MatchDex Match Simulation",
            description=(
                f"**{m['p1_name']}** {m['p1_score']} - {m['p2_score']} **{m['p2_name']}**\n\n"
                f"**Round {m['round']}**\n"
                f"<@{chooser_id}> — choose ⚔️ Attack or 🛡️ Defence!"
            ),
            color=discord.Color.gold(),
        )
        if m["highlights"]:
            # Keep last 15 highlights
            hl_text = "\n".join(m["highlights"][-15:])
            embed.add_field(name="Match Highlights:", value=hl_text, inline=False)

        view = RoleView(mid, self, chooser_id)
        if m.get("main_message"):
            await m["main_message"].edit(embed=embed, view=view)
        else:
            m["main_message"] = await ch.send(embed=embed, view=view)

    # ── Prompt the responder (after chooser has picked) ─────────
    async def prompt_responder(self, mid):
        m = ACTIVE_MATCHES.get(mid)
        if not m:
            return
        # Figure out who hasn't chosen yet
        if m["p1_choice"] and not m["p2_choice"]:
            responder_id = m["p2_id"]
            chooser_role = m["p1_choice"]["stat_type"]
        elif m["p2_choice"] and not m["p1_choice"]:
            responder_id = m["p1_id"]
            chooser_role = m["p2_choice"]["stat_type"]
        else:
            return  # shouldn't happen

        # Forced opposite role
        forced = "DEFENCE" if chooser_role == "ATTACK" else "ATTACK"

        embed = discord.Embed(
            title="🏟️ MatchDex Match Simulation",
            description=(
                f"**{m['p1_name']}** {m['p1_score']} - {m['p2_score']} **{m['p2_name']}**\n\n"
                f"**Round {m['round']}**\n"
                f"<@{responder_id}>, your opponent has chosen! Pick your card for **{forced}**:"
            ),
            color=discord.Color.gold(),
        )
        if m["highlights"]:
            hl_text = "\n".join(m["highlights"][-15:])
            embed.add_field(name="Match Highlights:", value=hl_text, inline=False)

        view = ResponderView(mid, responder_id, forced, self)
        if m.get("main_message"):
            await m["main_message"].edit(embed=embed, view=view)
        else:
            m["main_message"] = await m["channel"].send(embed=embed, view=view)

    # ── Resolve round ───────────────────────────────────────────
    async def resolve_round(self, mid):
        m = ACTIVE_MATCHES.get(mid)
        if not m:
            return
        ch = m["channel"]
        p1c, p2c = m["p1_choice"], m["p2_choice"]

        b1 = await get_logo_bonus(m["p1_id"])
        b2 = await get_logo_bonus(m["p2_id"])

        def stat_val(choice, boost):
            if not choice or not choice["card"]:
                return 0
            c = choice["card"]
            return (
                c.template.attack_stat
                if choice["stat_type"] == "ATTACK"
                else c.template.defence_stat
            ) + boost

        v1, v2 = stat_val(p1c, b1), stat_val(p2c, b2)
        n1 = p1c["card"].template.name if p1c and p1c["card"] else "—"
        n2 = p2c["card"].template.name if p2c and p2c["card"] else "—"
        r1 = p1c["stat_type"] if p1c else "?"
        r2 = p2c["stat_type"] if p2c else "?"

        if v1 > v2:
            m["p1_score"] += 1
            line = f"**{m['p1_name']}** wins"
            m["chooser_id"] = m["p1_id"]
        elif v2 > v1:
            m["p2_score"] += 1
            line = f"**{m['p2_name']}** wins"
            m["chooser_id"] = m["p2_id"]
        else:
            line = "Draw"

        emoji1 = "⚔️" if r1 == "ATTACK" else "🛡️"
        emoji2 = "⚔️" if r2 == "ATTACK" else "🛡️"
        
        highlight = f"⏱️ **Round {m['round']}**: {n1} ({v1} {emoji1}) vs {n2} ({v2} {emoji2}) ➔ {line}!"
        m["highlights"].append(highlight)

        m["p1_choice"] = None
        m["p2_choice"] = None
        m["round"] += 1

        if m["round"] > 11:
            await self.end_match(mid)
        else:
            await self.send_round(mid)

    # ── End match ───────────────────────────────────────────────
    async def end_match(self, mid):
        m = ACTIVE_MATCHES.get(mid)
        if not m:
            return
        ch = m["channel"]
        s1, s2 = m["p1_score"], m["p2_score"]

        async def award(uid, pts, win, draw):
            u = await DiscordUser.objects.aget(discord_id=uid)
            u.points += pts
            if win:
                u.wins += 1
            elif draw:
                u.draws += 1
            else:
                u.losses += 1
            await u.asave()
            await update_objective_progress(u, "play_match")

        if s1 > s2:
            txt = f"🏆 **{m['p1_name']}** wins! (+3 pts)"
            await award(m["p1_id"], 3, True, False)
            await award(m["p2_id"], 0, False, False)
        elif s2 > s1:
            txt = f"🏆 **{m['p2_name']}** wins! (+3 pts)"
            await award(m["p2_id"], 3, True, False)
            await award(m["p1_id"], 0, False, False)
        else:
            txt = "🤝 **Draw!** Both get 1 point."
            await award(m["p1_id"], 1, False, True)
            await award(m["p2_id"], 1, False, True)

        embed = discord.Embed(title="🏟️ MATCH OVER!", color=discord.Color.red())
        embed.description = f"**{m['p1_name']}** {s1} - {s2} **{m['p2_name']}**\n\n{txt}"
        
        if m["highlights"]:
            hl_text = ""
            for h in reversed(m["highlights"][-15:]):
                # +1 for newline, +3 for "..." if we want to add it, but just fitting the lines is fine.
                if len(hl_text) + len(h) + 1 > 1020:
                    hl_text = "...\n" + hl_text
                    break
                hl_text = h + "\n" + hl_text if hl_text else h
                
            embed.add_field(name="Match Highlights:", value=hl_text, inline=False)

        if m.get("main_message"):
            await m["main_message"].edit(embed=embed, view=None)
        else:
            await ch.send(embed=embed)
        del ACTIVE_MATCHES[mid]

    # ════════════════════════════════════════════════════════════
    #  QUICK SIM — /match_sim command
    # ════════════════════════════════════════════════════════════

    @app_commands.command(
        name="match_sim",
        description="Challenge another user to a Quick Sim match — simulates a full 90-minute game!"
    )
    async def match_sim(
        self, interaction: discord.Interaction, opponent: discord.Member
    ):
        await interaction.response.defer()

        if opponent.id == interaction.user.id:
            return await interaction.followup.send(
                "You can't challenge yourself!", ephemeral=True
            )
        if opponent.bot:
            return await interaction.followup.send(
                "You can't challenge a bot!", ephemeral=True
            )

        # Check not already in a quicksim or regular match
        for m in ACTIVE_MATCHES.values():
            if interaction.user.id in (m["p1_id"], m["p2_id"]):
                return await interaction.followup.send(
                    "You're already in a match!", ephemeral=True
                )
            if opponent.id in (m["p1_id"], m["p2_id"]):
                return await interaction.followup.send(
                    f"**{opponent.name}** is already in a match!", ephemeral=True
                )

        for uid in ACTIVE_QUICKSIMS:
            if interaction.user.id == uid or opponent.id == uid:
                return await interaction.followup.send(
                    "One of you is already in a Quick Sim!", ephemeral=True
                )

        # Lineup check
        async def has_full_lineup(uid):
            user, _ = await DiscordUser.objects.aget_or_create(discord_id=uid)
            lineup = await Lineup.objects.filter(
                owner=user, is_active=True
            ).afirst()
            if not lineup or not lineup.gk_id:
                return False
            req = FORMATION_SLOTS.get(
                lineup.formation, {"gk": 1, "df": 4, "md": 3, "at": 3}
            )
            if (
                sum(1 for i in range(1, 6) if getattr(lineup, f"df{i}_id", None))
                < req["df"]
            ):
                return False
            if (
                sum(1 for i in range(1, 6) if getattr(lineup, f"md{i}_id", None))
                < req["md"]
            ):
                return False
            if (
                sum(1 for i in range(1, 5) if getattr(lineup, f"at{i}_id", None))
                < req["at"]
            ):
                return False
            return True

        if not await has_full_lineup(interaction.user.id):
            return await interaction.followup.send(
                "You don't have a full lineup!", ephemeral=True
            )
        if not await has_full_lineup(opponent.id):
            return await interaction.followup.send(
                f"**{opponent.name}** doesn't have a full lineup.", ephemeral=True
            )

        await interaction.followup.send(
            f"⚡ {opponent.mention}, you have been challenged to a "
            f"**Quick Sim** by {interaction.user.mention}!",
            view=QuickSimAcceptView(interaction.user, opponent, self),
        )

    @app_commands.command(
        name="sim_trophies",
        description="View your or another user's Quick Sim trophies"
    )
    async def sim_trophies(self, interaction: discord.Interaction, member: discord.Member | None = None):
        target = member or interaction.user
        
        from core.models import SimSeason, SimSeasonPlayer
        active_season = await SimSeason.objects.filter(is_active=True).afirst()
        if not active_season:
            return await interaction.response.send_message("There is no active Quick Sim season.", ephemeral=True)
            
        sp, _ = await SimSeasonPlayer.objects.aget_or_create(user_id=target.id, season=active_season, defaults={'trophies': 1000})
        
        embed = discord.Embed(
            title=f"🏆 {target.display_name}'s Trophies",
            description=f"**{sp.trophies}** Trophies\nWins: {sp.wins} | Losses: {sp.losses} | Draws: {sp.draws}",
            color=discord.Color.blue()
        )
        await interaction.response.send_message(embed=embed)

def format_tactic_badge(tactic: str) -> str:
    """Returns a friendly badge with emoji for the active strategy."""
    t = (tactic or "balanced").lower()
    badges = {
        "attacking": "⚔️ Attack",
        "defensive": "🛡️ Defend",
        "balanced": "⚖️ Balanced",
        "counter": "⚡ Counter",
        "possession": "🔄 Possession",
    }
    return badges.get(t, f"⚖️ {t.title()}")


class MatchTacticsView(discord.ui.View):
    """
    Real-time interactive tactical switchboard for MatchDex Quick Sim matches.
    Allows both managers (p1 = home, p2 = away) to adjust their team strategy midgame.
    Buttons dynamically update line ratings in MatchSimulationSession and announce changes.
    """

    def __init__(self, session: MatchSimulationSession, p1: discord.Member, p2: discord.Member):
        super().__init__(timeout=360)
        self.session = session
        self.p1 = p1
        self.p2 = p2
        self.tactical_events: list[str] = []

    async def _change_strategy(
        self,
        interaction: discord.Interaction,
        tactic_key: str,
        tactic_name: str,
        emoji: str,
        details: str,
    ):
        if interaction.user.id == self.p1.id:
            team_side = "home"
            team = self.session.home
        elif interaction.user.id == self.p2.id:
            team_side = "away"
            team = self.session.away
        else:
            return await interaction.response.send_message(
                "❌ Only the managers playing this match can adjust team tactics!",
                ephemeral=True,
            )

        if self.session.is_finished:
            return await interaction.response.send_message(
                "🏁 The match is already over!",
                ephemeral=True,
            )

        if team.tactic.lower() == tactic_key.lower():
            return await interaction.response.send_message(
                f"ℹ️ **{team.display_name}** is already using the **{emoji} {tactic_name}** strategy!",
                ephemeral=True,
            )

        # Apply tactical change and recalculate team line ratings immediately
        self.session.change_tactic(team_side, tactic_key)

        minute = self.session.current_minute
        announcement = (
            f"`{minute}'` 📋 **TACTICAL CHANGE!** **{team.display_name}** switches to "
            f"**{emoji} {tactic_name}**!"
        )
        self.tactical_events.append(announcement)

        await interaction.response.send_message(
            f"✅ **Tactical Strategy Updated!**\n"
            f"**{team.display_name}** shifted to **{emoji} {tactic_name}**!\n"
            f"*{details}*",
            ephemeral=True,
        )

    @discord.ui.button(label="Attack", emoji="⚔️", style=discord.ButtonStyle.danger, row=0)
    async def btn_attack(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_strategy(
            interaction, "attacking", "Attack", "⚔️",
            "Boosts attacking line (+10% Attack, -10% Defence)."
        )

    @discord.ui.button(label="Defend", emoji="🛡️", style=discord.ButtonStyle.primary, row=0)
    async def btn_defend(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_strategy(
            interaction, "defensive", "Defend", "🛡️",
            "Strengthens defensive backline (+10% Defence, -15% Attack, +35% counter chance)."
        )

    @discord.ui.button(label="Balanced", emoji="⚖️", style=discord.ButtonStyle.secondary, row=0)
    async def btn_balanced(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_strategy(
            interaction, "balanced", "Balanced", "⚖️",
            "Neutral line distribution across all areas."
        )

    @discord.ui.button(label="Counter", emoji="⚡", style=discord.ButtonStyle.success, row=0)
    async def btn_counter(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_strategy(
            interaction, "counter", "Counter", "⚡",
            "Solid defence (+8% Def) with maximum clearance counter-attack rate (45%)."
        )

    @discord.ui.button(label="Possession", emoji="🔄", style=discord.ButtonStyle.secondary, row=0)
    async def btn_possession(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self._change_strategy(
            interaction, "possession", "Possession", "🔄",
            "Controls midfield play (+15% Midfield) to dominate possession and tempo."
        )


    # ── Quick Sim live-feed runner ───────────────────────────────

    async def run_quicksim_live(self, interaction, p1, p2):
        """Run the simulation and live-update a Discord embed every ~3 seconds."""
        ch = interaction.channel

        # Lock both players
        ACTIVE_QUICKSIMS[p1.id] = True
        ACTIVE_QUICKSIMS[p2.id] = True

        try:
            from core.models import SimSeason, SimSeasonPlayer
            active_season = await SimSeason.objects.filter(is_active=True).afirst()
            if active_season:
                sp1, _ = await SimSeasonPlayer.objects.aget_or_create(user_id=p1.id, season=active_season, defaults={'trophies': 1000})
                sp2, _ = await SimSeasonPlayer.objects.aget_or_create(user_id=p2.id, season=active_season, defaults={'trophies': 1000})
                p1_trophies = sp1.trophies
                p2_trophies = sp2.trophies
            else:
                p1_trophies = "N/A"
                p2_trophies = "N/A"

            # Load lineups
            p1_cards, p1_tactic, p1_formation = await load_lineup_for_sim(p1.id)
            p2_cards, p2_tactic, p2_formation = await load_lineup_for_sim(p2.id)

            b1 = await get_logo_bonus(p1.id)
            b2 = await get_logo_bonus(p2.id)
            
            from core.models import UserManager
            @sync_to_async
            def get_manager(uid):
                try:
                    um = UserManager.objects.select_related("manager").get(owner__discord_id=uid)
                    return um.manager
                except UserManager.DoesNotExist:
                    return None
                    
            p1_manager = await get_manager(p1.id)
            p2_manager = await get_manager(p2.id)

            home = build_team_data(
                p1_cards, p1.display_name, tactic=p1_tactic, logo_bonus=b1,
                manager=p1_manager, formation=p1_formation
            )
            away = build_team_data(
                p2_cards, p2.display_name, tactic=p2_tactic, logo_bonus=b2,
                manager=p2_manager, formation=p2_formation
            )

            # Interactive live simulation session ported from RFM27
            session = MatchSimulationSession(home, away)
            view = MatchTacticsView(session, p1, p2)

            # ── Live Feed (RFM Moving Graphic Interface) ──────
            radar_line, zone_desc = render_pitch_radar(
                2, "possession", p1.display_name, p2.display_name
            )
            initial_mom = render_momentum_bar(
                int(round(session.current_home_momentum)),
                p1.display_name,
                p2.display_name,
            )

            embed = discord.Embed(
                title="🏟️ MatchDex Quick Sim — Kick Off!",
                description=(
                    f"## {p1.display_name} ({p1_trophies} 🏆)  ` 0 - 0 `  ({p2_trophies} 🏆) {p2.display_name}\n"
                    f"> **⏱️ 0' • 1st Half** • **🟢 KICK OFF!**\n\n"
                    f"📋 Strategies: **{home.display_name}** [{format_tactic_badge(home.tactic)}] ─ [{format_tactic_badge(away.tactic)}] **{away.display_name}**"
                ),
                color=discord.Color.gold(),
            )
            embed.add_field(
                name="📍 Pitch Action Radar",
                value=f"{radar_line}\n{zone_desc}",
                inline=False,
            )
            embed.add_field(
                name="📊 Momentum & Possession",
                value=initial_mom,
                inline=True,
            )
            embed.add_field(
                name="🎯 Match Stats",
                value="Shots (OT): **0 (0)** ─ **0 (0)**\nCards: **0 🟨** ─ **0 🟨**",
                inline=True,
            )
            embed.add_field(
                name="📜 Live Commentary Feed",
                value="`0'` 🟢 The referee blows the whistle! Match is underway!",
                inline=False,
            )
            embed.set_footer(text="⚡ MatchDex Quick Sim • RFM27 Match Engine • Midgame Tactics Active")
            msg = await ch.send(embed=embed, view=view)

            displayed_events: list[str] = []
            running_home = 0
            running_away = 0
            running_h_shots = 0
            running_a_shots = 0
            running_h_sot = 0
            running_a_sot = 0
            running_h_cards: list[str] = []
            running_a_cards: list[str] = []
            goalscorers: list[str] = []

            step_idx = 0
            while not session.is_finished:
                # Check if match was cancelled
                if (
                    ACTIVE_QUICKSIMS.get(p1.id) == "cancelled"
                    or ACTIVE_QUICKSIMS.get(p2.id) == "cancelled"
                ):
                    for child in view.children:
                        child.disabled = True
                    embed = discord.Embed(
                        title="🏟️ MatchDex Quick Sim — Cancelled",
                        description="The match was cancelled.",
                        color=discord.Color.red(),
                    )
                    try:
                        await msg.edit(embed=embed, view=view)
                    except discord.HTTPException:
                        pass
                    return

                # Harvest any manager tactical updates that were clicked
                while view.tactical_events:
                    displayed_events.append(view.tactical_events.pop(0))

                batch = session.step()
                step_idx += 1
                if not batch:
                    if session.is_finished:
                        break
                    continue

                last_event = batch[-1]
                for event in batch:
                    if event.event_type == "goal":
                        if event.team_side == "home":
                            running_home += 1
                        else:
                            running_away += 1
                        scorer_txt = f"⚽ `{event.minute}'` {event.scorer_name}"
                        if event.assist_name:
                            scorer_txt += f" *(Assist: {event.assist_name})*"
                        goalscorers.append(scorer_txt)

                    if getattr(event, "is_shot", False):
                        if event.team_side == "home":
                            running_h_shots += 1
                            if getattr(event, "is_shot_on_target", False):
                                running_h_sot += 1
                        else:
                            running_a_shots += 1
                            if getattr(event, "is_shot_on_target", False):
                                running_a_sot += 1

                    card_type = getattr(event, "card_type", None)
                    if card_type:
                        if event.team_side == "home":
                            running_h_cards.append(card_type)
                        else:
                            running_a_cards.append(card_type)

                    if event.event_type != "full_time":
                        displayed_events.append(f"`{event.minute}'` {event.text}")

                # Harvest any additional tactical events added during step processing
                while view.tactical_events:
                    displayed_events.append(view.tactical_events.pop(0))

                is_final = session.is_finished

                # Determine match status badge & period
                if is_final:
                    status_badge = "🏁 FULL TIME"
                elif last_event.event_type == "half_time":
                    status_badge = "⏱️ HALF TIME"
                elif last_event.minute <= 45:
                    status_badge = f"⏱️ {last_event.minute}' • 1st Half"
                else:
                    status_badge = f"⏱️ {last_event.minute}' • 2nd Half"

                # Embed Color based on action
                if is_final:
                    embed_color = discord.Color.dark_grey()
                elif any(e.event_type == "goal" for e in batch):
                    embed_color = discord.Color.green()
                elif any(getattr(e, "card_type", None) for e in batch):
                    embed_color = discord.Color.orange()
                elif any(e.event_type == "save" for e in batch):
                    embed_color = discord.Color.blue()
                elif last_event.event_type == "half_time":
                    embed_color = discord.Color.purple()
                else:
                    embed_color = discord.Color.gold()

                embed = discord.Embed(
                    title=f"🏟️ MatchDex Quick Sim — {'Full Time!' if is_final else 'Live Match'}",
                    color=embed_color,
                )

                # Header Description
                phase_title = getattr(last_event, "phase_title", "") or "⚡ ACTION IN PROGRESS"
                score_header = (
                    f"## {p1.display_name} ({p1_trophies} 🏆)  ` {running_home} - {running_away} `  ({p2_trophies} 🏆) {p2.display_name}\n"
                    f"> **{status_badge}** • **{phase_title}**\n\n"
                    f"📋 Strategies: **{home.display_name}** [{format_tactic_badge(home.tactic)}] ─ [{format_tactic_badge(away.tactic)}] **{away.display_name}**"
                )
                embed.description = score_header

                # 1. Pitch Action Radar Field
                radar_line, zone_desc = render_pitch_radar(
                    getattr(last_event, "zone", 2),
                    last_event.event_type,
                    p1.display_name,
                    p2.display_name,
                )
                embed.add_field(
                    name="📍 Pitch Action Radar",
                    value=f"{radar_line}\n{zone_desc}",
                    inline=False,
                )

                # 2. Momentum Field
                mom_str = render_momentum_bar(
                    getattr(last_event, "home_momentum", int(round(session.current_home_momentum))),
                    p1.display_name,
                    p2.display_name,
                )
                embed.add_field(
                    name="📊 Momentum & Possession",
                    value=mom_str,
                    inline=True,
                )

                # 3. Match Stats Field
                h_yellows = running_h_cards.count("yellow")
                h_reds = running_h_cards.count("red")
                a_yellows = running_a_cards.count("yellow")
                a_reds = running_a_cards.count("red")

                h_disc = f"{h_yellows} 🟨" + (f" {h_reds} 🟥" if h_reds else "")
                a_disc = f"{a_yellows} 🟨" + (f" {a_reds} 🟥" if a_reds else "")

                stats_str = (
                    f"Shots (OT): **{running_h_shots} ({running_h_sot})** ─ **{running_a_shots} ({running_a_sot})**\n"
                    f"Cards: **{h_disc}** ─ **{a_disc}**"
                )
                embed.add_field(
                    name="🎯 Match Stats",
                    value=stats_str,
                    inline=True,
                )

                # 4. Goalscorers Field (if any)
                if goalscorers:
                    embed.add_field(
                        name="⚽ Goalscorers",
                        value="\n".join(goalscorers[-6:]),
                        inline=False,
                    )

                # 5. Live Commentary Feed
                recent_feed = displayed_events[-4:]
                embed.add_field(
                    name="📜 Live Commentary Feed",
                    value="\n".join(recent_feed) if recent_feed else "`0'` Match starting...",
                    inline=False,
                )

                if is_final:
                    embed.set_footer(
                        text="⚡ MatchDex Quick Sim • RFM27 Match Engine • Full Time"
                    )
                    for child in view.children:
                        child.disabled = True
                elif last_event.event_type == "half_time":
                    embed.set_footer(
                        text="⚡ Half-Time Break • Adjust your team strategies below before 2nd Half!"
                    )
                else:
                    embed.set_footer(
                        text=f"⚡ MatchDex Quick Sim • Midgame Tactics Active • Phase {step_idx}/{session.total_phases}"
                    )

                try:
                    await msg.edit(embed=embed, view=view)
                except discord.HTTPException:
                    pass

                if not is_final:
                    is_ht = any(e.event_type == "half_time" for e in batch)
                    sleep_time = 6 if is_ht else 3
                    await asyncio.sleep(sleep_time)

            # Get final match result
            result = session.get_result()

            # ── Awards ────────────────────────────────────────
            from core.models import SimSeason, SimSeasonPlayer
            
            s1, s2 = result.home_score, result.away_score
            
            active_season = await SimSeason.objects.filter(is_active=True).afirst()
            
            if active_season:
                # Elo-based trophies system
                sp1, _ = await SimSeasonPlayer.objects.aget_or_create(
                    user_id=p1.id, season=active_season,
                    defaults={'trophies': 1000}
                )
                sp2, _ = await SimSeasonPlayer.objects.aget_or_create(
                    user_id=p2.id, season=active_season,
                    defaults={'trophies': 1000}
                )
                
                # Simple Elo calculation
                # Expected score (win probability)
                ea = 1 / (1 + 10 ** ((sp2.trophies - sp1.trophies) / 400))
                eb = 1 / (1 + 10 ** ((sp1.trophies - sp2.trophies) / 400))
                
                k = 32  # K-factor
                
                if s1 > s2:
                    sa, sb = 1, 0
                    sp1.wins += 1
                    sp2.losses += 1
                elif s2 > s1:
                    sa, sb = 0, 1
                    sp1.losses += 1
                    sp2.wins += 1
                else:
                    sa, sb = 0.5, 0.5
                    sp1.draws += 1
                    sp2.draws += 1
                    
                change_a = round(k * (sa - ea))
                change_b = round(k * (sb - eb))
                
                sp1.trophies += change_a
                sp2.trophies += change_b
                
                await sp1.asave()
                await sp2.asave()
                
                # Objectives for both
                u1 = await DiscordUser.objects.aget(discord_id=p1.id)
                u2 = await DiscordUser.objects.aget(discord_id=p2.id)
                await update_objective_progress(u1, "play_match")
                await update_objective_progress(u2, "play_match")
                
                # Format result text
                def format_change(c):
                    return f"+{c}" if c > 0 else str(c)
                    
                if s1 > s2:
                    result_txt = f"🏆 **{p1.display_name}** wins! ({format_change(change_a)} Trophies)\n"
                    result_txt += f"❌ **{p2.display_name}** loses. ({format_change(change_b)} Trophies)"
                elif s2 > s1:
                    result_txt = f"🏆 **{p2.display_name}** wins! ({format_change(change_b)} Trophies)\n"
                    result_txt += f"❌ **{p1.display_name}** loses. ({format_change(change_a)} Trophies)"
                else:
                    result_txt = f"🤝 **Draw!**\n{p1.display_name}: {format_change(change_a)} Trophies\n{p2.display_name}: {format_change(change_b)} Trophies"
                    
            else:
                # Fallback to classic points if no active season
                async def award(uid, pts, win, draw):
                    u = await DiscordUser.objects.aget(discord_id=uid)
                    u.points += pts
                    if win:
                        u.wins += 1
                    elif draw:
                        u.draws += 1
                    else:
                        u.losses += 1
                    await u.asave()
                    await update_objective_progress(u, "play_match")

                if s1 > s2:
                    result_txt = f"🏆 **{p1.display_name}** wins! (+3 pts)"
                    await award(p1.id, 3, True, False)
                    await award(p2.id, 0, False, False)
                elif s2 > s1:
                    result_txt = f"🏆 **{p2.display_name}** wins! (+3 pts)"
                    await award(p2.id, 3, True, False)
                    await award(p1.id, 0, False, False)
                else:
                    result_txt = "🤝 **Draw!** Both get 1 point."
                    await award(p1.id, 1, False, True)
                    await award(p2.id, 1, False, True)


            embed.add_field(name="Result", value=result_txt, inline=False)
            for child in view.children:
                child.disabled = True
            try:
                await msg.edit(embed=embed, view=view)
            except discord.HTTPException:
                pass

        finally:
            ACTIVE_QUICKSIMS.pop(p1.id, None)
            ACTIVE_QUICKSIMS.pop(p2.id, None)


# ════════════════════════════════════════════════════════════════
#  QUICK SIM — Views & helpers (module-level, used by MatchCog)
# ════════════════════════════════════════════════════════════════


ACTIVE_QUICKSIMS = {}  # track running sims to prevent duplicates


class QuickSimAcceptView(discord.ui.View):
    def __init__(self, p1: discord.Member, p2: discord.Member, cog: MatchCog):
        super().__init__(timeout=60)
        self.p1, self.p2, self.cog = p1, p2, cog
        self._processing = False

    @discord.ui.button(label="⚡ Accept Quick Sim", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            return await interaction.response.send_message(
                "Only the challenged player can accept!", ephemeral=True
            )
        if self._processing:
            return await interaction.response.send_message(
                "Already processing…", ephemeral=True
            )
        self._processing = True
        self.stop()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(view=self)
        await self.cog.run_quicksim_live(interaction, self.p1, self.p2)

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.secondary)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            return await interaction.response.send_message(
                "Only the challenged player can decline!", ephemeral=True
            )
        self.stop()
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        await interaction.response.edit_message(
            content=f"❌ {self.p2.display_name} declined the Quick Sim challenge.",
            view=self,
        )


@sync_to_async
def load_lineup_for_sim(uid):
    """Load a user's active lineup cards + tactic for the sim engine."""
    lineup = Lineup.objects.select_related(
        "gk__template",
        "df1__template", "df2__template", "df3__template",
        "df4__template", "df5__template",
        "md1__template", "md2__template", "md3__template",
        "md4__template", "md5__template",
        "at1__template", "at2__template", "at3__template", "at4__template",
    ).get(owner__discord_id=uid, is_active=True)

    cards = [
        getattr(lineup, s)
        for s in [
            "gk", "df1", "df2", "df3", "df4", "df5",
            "md1", "md2", "md3", "md4", "md5",
            "at1", "at2", "at3", "at4",
        ]
        if getattr(lineup, s)
    ]
    return cards, lineup.tactic, lineup.formation


async def setup(bot):
    await bot.add_cog(MatchCog(bot))

