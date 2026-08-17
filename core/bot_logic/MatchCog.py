import asyncio

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, UserCard, UserLogo
from core.objectives import update_objective_progress
from core.bot_logic.quick_sim import build_team_data, simulate_match

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
            f"⚡ {opponent.mention}, you have been challenged to a "
            f"**Quick Sim** by {interaction.user.mention}!",
            view=QuickSimAcceptView(interaction.user, opponent, self),
        )

    # ── Quick Sim live-feed runner ───────────────────────────────

    async def run_quicksim_live(self, interaction, p1, p2):
        """Run the simulation and live-update a Discord embed every ~3 seconds."""
        ch = interaction.channel

        # Lock both players
        ACTIVE_QUICKSIMS[p1.id] = True
        ACTIVE_QUICKSIMS[p2.id] = True

        try:
            # Load lineups
            p1_cards, p1_tactic = await load_lineup_for_sim(p1.id)
            p2_cards, p2_tactic = await load_lineup_for_sim(p2.id)

            b1 = await get_logo_bonus(p1.id)
            b2 = await get_logo_bonus(p2.id)

            home = build_team_data(
                p1_cards, p1.display_name, tactic=p1_tactic, logo_bonus=b1
            )
            away = build_team_data(
                p2_cards, p2.display_name, tactic=p2_tactic, logo_bonus=b2
            )

            # Run the simulation (instant, pure math)
            result = simulate_match(home, away)

            # ── Live Feed ─────────────────────────────────────
            embed = discord.Embed(
                title="🏟️ MatchDex Quick Sim",
                description=(
                    f"**{p1.display_name}** vs **{p2.display_name}**\n\n"
                    f"📋 Tactics: `{p1_tactic.title()}` vs `{p2_tactic.title()}`\n\n"
                    "⏱️ Kick off!"
                ),
                color=discord.Color.gold(),
            )
            embed.set_footer(text="⚡ Quick Sim — Live")
            msg = await ch.send(embed=embed)

            displayed_events: list[str] = []
            running_home = 0
            running_away = 0

            # Batch events: group by ~8 minute windows
            batches: list[list] = []
            current_batch: list = []
            batch_start_minute = 0

            for event in result.events:
                if event.event_type == "full_time":
                    if current_batch:
                        batches.append(current_batch)
                        current_batch = []
                    batches.append([event])
                    continue

                if event.minute - batch_start_minute > 8 and current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    batch_start_minute = event.minute

                current_batch.append(event)

            if current_batch:
                batches.append(current_batch)

            for batch_idx, batch in enumerate(batches):
                for event in batch:
                    if event.event_type == "goal":
                        if event.team_side == "home":
                            running_home += 1
                        else:
                            running_away += 1

                    displayed_events.append(f"`{event.minute}'` {event.text}")

                # Build the updated embed
                score_line = (
                    f"**{p1.display_name}** {running_home} - "
                    f"{running_away} **{p2.display_name}**"
                )

                visible = displayed_events[-12:]
                hl_text = "\n".join(visible)
                if len(displayed_events) > 12:
                    hl_text = "…\n" + hl_text

                is_final = batch_idx == len(batches) - 1

                if is_final:
                    embed = discord.Embed(
                        title="🏟️ MatchDex Quick Sim — Full Time!",
                        color=discord.Color.red(),
                    )
                else:
                    embed = discord.Embed(
                        title="🏟️ MatchDex Quick Sim",
                        color=discord.Color.gold(),
                    )

                embed.description = score_line + "\n\n" + hl_text

                if not is_final:
                    embed.set_footer(text="⚡ Quick Sim — Live")
                else:
                    embed.set_footer(text="⚡ Quick Sim — Match Over")

                try:
                    await msg.edit(embed=embed)
                except discord.HTTPException:
                    pass

                if not is_final:
                    # Check if cancelled before sleeping
                    if ACTIVE_QUICKSIMS.get(p1.id) == "cancelled" or ACTIVE_QUICKSIMS.get(p2.id) == "cancelled":
                        embed = discord.Embed(
                            title="🏟️ MatchDex Quick Sim — Cancelled",
                            description="The match was cancelled.",
                            color=discord.Color.red(),
                        )
                        try:
                            await msg.edit(embed=embed)
                        except discord.HTTPException:
                            pass
                        return

                    await asyncio.sleep(3)

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
            try:
                await msg.edit(embed=embed)
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
    return cards, lineup.tactic


async def setup(bot):
    await bot.add_cog(MatchCog(bot))

