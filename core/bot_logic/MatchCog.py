import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, UserCard, UserLogo

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

    @discord.ui.button(label="Accept Match", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.p2.id:
            return await interaction.response.send_message(
                "Only the challenged player can accept!", ephemeral=True
            )
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

        # Already in a match?
        for m in ACTIVE_MATCHES.values():
            if interaction.user.id in (m["p1_id"], m["p2_id"]):
                return await interaction.followup.send(
                    "You're already in a match!", ephemeral=True
                )
            if opponent.id in (m["p1_id"], m["p2_id"]):
                return await interaction.followup.send(
                    f"**{opponent.name}** is already in a match!", ephemeral=True
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

    @app_commands.command(name="match_cancel", description="Cancel your active match")
    async def match_cancel(self, interaction: discord.Interaction):
        await interaction.response.defer()

        match_id_to_cancel = None
        for mid, m in ACTIVE_MATCHES.items():
            if interaction.user.id in (m["p1_id"], m["p2_id"]):
                match_id_to_cancel = mid
                break

        if not match_id_to_cancel:
            return await interaction.followup.send(
                "You do not have an active match to cancel.", ephemeral=True
            )

        m = ACTIVE_MATCHES[match_id_to_cancel]
        await interaction.followup.send(
            f"❌ The match between **{m['p1_name']}** and **{m['p2_name']}** has been canceled by {interaction.user.mention}."
        )
        del ACTIVE_MATCHES[match_id_to_cancel]

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
            hl_text = "\n".join(m["highlights"][-15:])
            embed.add_field(name="Match Highlights:", value=hl_text, inline=False)

        if m.get("main_message"):
            await m["main_message"].edit(embed=embed, view=None)
        else:
            await ch.send(embed=embed)
        del ACTIVE_MATCHES[mid]


async def setup(bot):
    await bot.add_cog(MatchCog(bot))
