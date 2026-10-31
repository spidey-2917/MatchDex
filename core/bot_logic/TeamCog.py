import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import (
    DiscordUser, Lineup, UserCard, CardTemplate,
    Manager, UserManager, UserOwnedManager,
    Logo, UserLogo, UserOwnedLogo
)
from core.utils import player_autocomplete, template_autocomplete

# ── Formation configs ───────────────────────────────────────────
FORMATIONS = {
    "442": {"name": "4-4-2", "gk": 1, "df": 4, "md": 4, "at": 2},
    "433": {"name": "4-3-3", "gk": 1, "df": 4, "md": 3, "at": 3},
    "352": {"name": "3-5-2", "gk": 1, "df": 3, "md": 5, "at": 2},
    "4231": {"name": "4-2-3-1", "gk": 1, "df": 4, "md": 5, "at": 1},
    "541": {"name": "5-4-1", "gk": 1, "df": 5, "md": 4, "at": 1},
    "4141": {"name": "4-1-4-1", "gk": 1, "df": 4, "md": 5, "at": 1},
    "343": {"name": "3-4-3", "gk": 1, "df": 3, "md": 4, "at": 3},
    "424": {"name": "4-2-4", "gk": 1, "df": 4, "md": 2, "at": 4},
}

# Position → Group mapping
POS_GROUPS = {
    "LW": "ATT",
    "ST": "ATT",
    "RW": "ATT",
    "CAM": "MID",
    "CM": "MID",
    "CDM": "MID",
    "LB": "DEF",
    "CB": "DEF",
    "RB": "DEF",
    "GK": "GK",
}

SLOT_TO_GROUP = {"gk": "GK", "df": "DEF", "md": "MID", "at": "ATT"}

# ── Emoji helpers ───────────────────────────────────────────────
POS_EMOJI = {"GK": "🧤", "DEF": "🛡️", "MID": "⚙️", "ATT": "⚔️"}


def get_formation_slots(formation_key):
    """Returns a list of slot names for the given formation."""
    f = FORMATIONS.get(formation_key, FORMATIONS["433"])
    slots = ["gk"]
    for i in range(1, f["df"] + 1):
        slots.append(f"df{i}")
    for i in range(1, f["md"] + 1):
        slots.append(f"md{i}")
    for i in range(1, f["at"] + 1):
        slots.append(f"at{i}")
    return slots


def calc_squad_power(lineup, formation_key):
    """Calculate squad power (0-100) based on filled slots and OVR."""
    slots = get_formation_slots(formation_key)
    total_ovr = 0
    filled = 0
    for s in slots:
        card = getattr(lineup, s, None)
        if card:
            filled += 1
            total_ovr += card.template.ovr
    if filled == 0:
        return 0, 0, len(slots)
    avg_ovr = total_ovr / filled
    fill_pct = (filled / len(slots)) * 100
    power = (
        int(fill_pct * 0.6 + avg_ovr * 0.4) if fill_pct == 100 else int(fill_pct * 0.8)
    )
    return power, filled, len(slots)


# ── Formation Dropdown ──────────────────────────────────────────
class FormationSelect(discord.ui.Select):
    def __init__(self):
        options = [
            discord.SelectOption(
                label=v["name"], value=k, description=f"Formation {v['name']}"
            )
            for k, v in FORMATIONS.items()
        ]
        super().__init__(placeholder="Pick your formation...", options=options)

    async def callback(self, interaction: discord.Interaction):
        chosen = self.values[0]
        formation_name = FORMATIONS[chosen]["name"]

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Create or update lineup
        lineup = await Lineup.objects.filter(owner=user, is_active=True).afirst()
        if lineup:
            lineup.formation = chosen
            # Clear all slots when changing formation
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
                "sub1",
                "sub2",
                "sub3",
            ]:
                setattr(lineup, s, None)
            await lineup.asave()
        else:
            lineup = await Lineup.objects.acreate(
                owner=user, name="Squad", is_active=True, formation=chosen
            )

        embed = discord.Embed(
            title="⚽ Squad Created!",
            description=f"Formation **{formation_name}** is locked in.\n\nUse these commands to build your squad:",
            color=discord.Color.green(),
        )
        embed.add_field(
            name="📋 `/team_view`", value="See your current squad", inline=False
        )
        embed.add_field(
            name="➕ `/team_add`", value="Place a player in a slot", inline=False
        )
        embed.add_field(
            name="🤖 `/team_auto`", value="Auto-fill with your best cards", inline=False
        )
        await interaction.response.edit_message(content=None, embed=embed, view=None)


class FormationView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=120)
        self.add_item(FormationSelect())


# ── The Cog ─────────────────────────────────────────────────────
class TeamCog(commands.Cog, name="Teams"):
    def __init__(self, bot):
        self.bot = bot

    # ─── /team_begin ────────────────────────────────────────────
    @app_commands.command(
        name="team_begin", description="Create a new squad by selecting a formation"
    )
    async def team_begin(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🏟️ Build Your Squad",
            description="Choose a formation to start. This will reset your current lineup.",
            color=discord.Color.dark_teal(),
        )
        await interaction.response.send_message(
            embed=embed, view=FormationView(), ephemeral=True
        )

    # ─── /team_view ─────────────────────────────────────────────
    @app_commands.command(name="team_view", description="View a squad with details")
    async def team_view(
        self, interaction: discord.Interaction, member: discord.Member | None = None #, pitch_view: bool = False
    ):
        await interaction.response.defer(ephemeral=False)
        target = member or interaction.user
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=target.id, defaults={"username": target.name}
        )

        @sync_to_async
        def get_lineup_data():
            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            if not lineup:
                return None, None, None, None, None, None, None, None

            from core.models import UserManager, UserLogo
            um = UserManager.objects.select_related("manager").filter(owner=user).first()
            ul = UserLogo.objects.select_related("logo").filter(owner=user).first()
            equipped_manager = um.manager if um else None
            equipped_logo = ul.logo if ul else None

            formation_key = lineup.formation
            f_info = FORMATIONS.get(formation_key, FORMATIONS["433"])
            slots = get_formation_slots(formation_key)

            # Build the display
            sections = {"GK": [], "DEF": [], "MID": [], "ATT": []}
            pitch_data = {}
            for slot_name in slots:
                card = getattr(lineup, slot_name, None)
                prefix = slot_name[:2]
                group = SLOT_TO_GROUP.get(prefix, "MID")

                if card:
                    # Force evaluation of the template FK
                    template = card.template
                    badges = []
                    tier = getattr(card, "upgrade_tier", "NONE")
                    if tier == "SILVER":
                        badges.append("🥈 +5%")
                    elif tier == "GOLD":
                        badges.append("🥇 +10%")
                    elif tier == "DIAMOND":
                        badges.append("💎 +25%")

                    ref = getattr(card, "refractor", "NONE")
                    if ref == "RED":
                        badges.append("🔴 Red Edge")
                    elif ref == "RAINBOW":
                        badges.append("🌈 Rainbow Edge")
                    elif ref == "BLACK":
                        badges.append("🖤 Black Edge")
                    elif ref == "GOLD":
                        badges.append("🟡 Gold Edge")

                    badge_suffix = f" [{' | '.join(badges)}]" if badges else ""
                    sections[group].append(
                        f"• **{template.name}** ({template.position}) — {template.ovr} OVR{badge_suffix}"
                    )
                    pitch_data[slot_name] = {"ovr": template.ovr, "name": template.name}
                else:
                    sections[group].append(f"• *Empty Slot* ({slot_name.upper()})")

            power, filled, total = calc_squad_power(lineup, formation_key)

            # Subs
            sub_lines = []
            for s in ["sub1", "sub2", "sub3"]:
                c = getattr(lineup, s, None)
                if c:
                    t = c.template
                    sub_lines.append(f"• **{t.name}** ({t.position}) — {t.ovr} OVR")
                else:
                    sub_lines.append(f"• *Empty* ({s.upper()})")

            return formation_key, f_info, sections, (power, filled, total), sub_lines, pitch_data, equipped_manager, equipped_logo

        result = await get_lineup_data()

        if result[0] is None:
            await interaction.followup.send(
                f"**{target.name}** doesn't have a squad yet!", ephemeral=False
            )
            return

        formation_key, f_info, sections, stats, sub_lines, pitch_data, equipped_manager, equipped_logo = result
        power, filled, total = stats

        desc_lines = []
        if equipped_manager:
            from core.bot_logic.quick_sim import normalize_formation_str
            norm_m = normalize_formation_str(equipped_manager.preferred_formation)
            norm_f = normalize_formation_str(formation_key)
            has_syn = (norm_m == "all" or norm_m == norm_f)
            syn_label = f"🟢 Formation Synergy (+{equipped_manager.formation_boost_pct}% {equipped_manager.formation_boost_type})" if has_syn else "⚪ No Formation Synergy"
            desc_lines.append(f"👔 **Manager:** {equipped_manager.name} ({syn_label})")
        else:
            desc_lines.append("👔 **Manager:** None (Use `/manager equip`)")

        if equipped_logo:
            desc_lines.append(f"🛡️ **Logo:** {equipped_logo.name} (+{equipped_logo.boost_val})")

        desc_lines.append(f"**Formation:** {f_info['name']}")

        # Build embed
        embed = discord.Embed(
            title=f"🏆 {target.name}'s Squad",
            description="\n".join(desc_lines),
            color=discord.Color.blue(),
        )

        # Add each section
        for group_name, display_name in [
            ("ATT", "⚔️ Attack"),
            ("MID", "⚙️ Midfield"),
            ("DEF", "🛡️ Defence"),
            ("GK", "🧤 Goalkeeper"),
        ]:
            if sections[group_name]:
                embed.add_field(
                    name=display_name,
                    value="\n".join(sections[group_name]),
                    inline=False,
                )

        # Squad Power bar
        bar_filled = int(power / 10)
        bar_empty = 10 - bar_filled
        power_bar = "🟩" * bar_filled + "⬛" * bar_empty

        embed.add_field(
            name=f"💪 Squad Power: {power}%",
            value=f"{power_bar}\n{filled}/{total} slots filled",
            inline=False,
        )

        embed.add_field(name="🔄 Substitutes", value="\n".join(sub_lines), inline=False)

        embed.set_footer(text="Matchdex Bot")

        # file = None
        # if pitch_view:
        #     import asyncio
        #     from core.pitch_generator import generate_pitch_image
        #     
        #     loop = asyncio.get_event_loop()
        #     img_io = await loop.run_in_executor(None, generate_pitch_image, pitch_data, formation_key)
        #     if img_io:
        #         file = discord.File(fp=img_io, filename="pitch.png")
        #         embed.set_image(url="attachment://pitch.png")

        # if file:
        #     await interaction.followup.send(embed=embed, file=file)
        # else:
        await interaction.followup.send(embed=embed)

    # ─── /team_add ──────────────────────────────────────────────
    @app_commands.command(
        name="team_add", description="Add a player from your collection to a squad slot"
    )
    @app_commands.autocomplete(player_name=player_autocomplete)
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="🧤 GK", value="gk"),
            app_commands.Choice(name="🛡️ DF1", value="df1"),
            app_commands.Choice(name="🛡️ DF2", value="df2"),
            app_commands.Choice(name="🛡️ DF3", value="df3"),
            app_commands.Choice(name="🛡️ DF4", value="df4"),
            app_commands.Choice(name="🛡️ DF5", value="df5"),
            app_commands.Choice(name="⚙️ MD1", value="md1"),
            app_commands.Choice(name="⚙️ MD2", value="md2"),
            app_commands.Choice(name="⚙️ MD3", value="md3"),
            app_commands.Choice(name="⚙️ MD4", value="md4"),
            app_commands.Choice(name="⚙️ MD5", value="md5"),
            app_commands.Choice(name="⚔️ AT1", value="at1"),
            app_commands.Choice(name="⚔️ AT2", value="at2"),
            app_commands.Choice(name="⚔️ AT3", value="at3"),
            app_commands.Choice(name="⚔️ AT4", value="at4")
        ]
    )
    async def team_add(
        self, interaction: discord.Interaction, slot: str, player_name: str
    ):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def do_add():
            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            if not lineup:
                return "no_lineup"

            # Check slot is valid for this formation
            valid_slots = get_formation_slots(lineup.formation)
            if slot not in valid_slots:
                f_info = FORMATIONS.get(lineup.formation, FORMATIONS["433"])
                return f"invalid_slot:{f_info['name']}"

            # Find card in collection — try card_id first (autocomplete sends IDs),
            # then fall back to name search for manual typing
            user_card = (
                UserCard.objects.filter(owner=user, card_id=player_name)
                .select_related("template")
                .first()
            )
            if not user_card:
                user_card = (
                    UserCard.objects.filter(
                        owner=user, template__name__icontains=player_name
                    )
                    .select_related("template")
                    .first()
                )

            if not user_card:
                return "no_card"

            # Check if this player (template name) is already in another slot (including subs)
            all_slots = valid_slots + ["sub1", "sub2", "sub3"]
            for s in all_slots:
                if s == slot:
                    continue
                existing = getattr(lineup, s, None)
                if existing and existing.template.name.lower() == user_card.template.name.lower():
                    return f"duplicate_player:{user_card.template.name}"

            # Position group validation
            prefix = slot[:2]
            expected_group = SLOT_TO_GROUP.get(prefix, "MID")
            card_group = POS_GROUPS.get(user_card.template.position, "MID")

            if expected_group != card_group:
                return f"wrong_pos:{card_group}:{expected_group}"

            setattr(lineup, slot, user_card)
            lineup.save()
            return f"ok:{user_card.template.name}:{user_card.template.position}:{slot.upper()}"

        result = await do_add()

        if result == "no_lineup":
            await interaction.response.send_message(
                "Use `/team_begin` first!", ephemeral=True
            )
        elif result == "no_card":
            await interaction.response.send_message(
                f"❌ No card matching **{player_name}** in your collection.",
                ephemeral=True,
            )
        elif result.startswith("invalid_slot:"):
            fname = result.split(":")[1]
            await interaction.response.send_message(
                f"❌ Slot **{slot.upper()}** doesn't exist in your **{fname}** formation.",
                ephemeral=True,
            )
        elif result.startswith("wrong_pos:"):
            _, cg, eg = result.split(":")
            await interaction.response.send_message(
                f"❌ Can't put a **{cg}** player in a **{eg}** slot!", ephemeral=True
            )
        elif result.startswith("duplicate_player:"):
            name = result.split(":")[1]
            await interaction.response.send_message(
                f"❌ **{name}** is already in your squad! You cannot add duplicate players.",
                ephemeral=True,
            )
        elif result.startswith("ok:"):
            _, name, pos, s = result.split(":")
            await interaction.response.send_message(
                f"✅ **{name}** ({pos}) → **{s}**", ephemeral=True
            )

    # ─── /team_auto ─────────────────────────────────────────────
    @app_commands.command(
        name="team_auto",
        description="Auto-fill your squad with the best cards from your collection",
    )
    async def team_auto(self, interaction: discord.Interaction):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def do_auto():
            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            if not lineup:
                return None, []

            valid_slots = get_formation_slots(lineup.formation)
            placements = []
            # Clear existing slots
            for s in valid_slots:
                setattr(lineup, s, None)

            placements = []
            used_card_ids = set()
            used_names = set()

            for slot_name in valid_slots:
                prefix = slot_name[:2]
                expected_group = SLOT_TO_GROUP.get(prefix, "MID")

                group_positions = [
                    k for k, v in POS_GROUPS.items() if v == expected_group
                ]

                # Fetch available cards for this group
                available_cards = (
                    UserCard.objects.filter(
                        owner=user, template__position__in=group_positions
                    )
                    .exclude(id__in=used_card_ids)
                    .select_related("template")
                    .order_by("-template__ovr")
                )

                best_card = None
                for c in available_cards:
                    # Skip if a player with this name is already in the lineup
                    if c.template.name.lower() not in used_names:
                        best_card = c
                        break

                if best_card:
                    setattr(lineup, slot_name, best_card)
                    used_card_ids.add(best_card.id)
                    used_names.add(best_card.template.name.lower())
                    placements.append(
                        f"• **{best_card.template.name}** ({best_card.template.position}) → {slot_name.upper()}"
                    )
                else:
                    placements.append(f"• *No suitable card* for {slot_name.upper()}")

            lineup.save()
            f_info = FORMATIONS.get(lineup.formation, FORMATIONS["433"])
            return f_info, placements

        f_info, placements = await do_auto()

        if not f_info:
            await interaction.response.send_message(
                "Use `/team_begin` first!", ephemeral=True
            )
            return

        embed = discord.Embed(
            title="🤖 Squad Auto-Filled!",
            description=f"Your best available players were assigned to empty slots:\n\n"
            + "\n".join(placements),
            color=discord.Color.teal(),
        )
        embed.set_footer(
            text="Use /team_view to see your full squad • /team_add to swap individual players"
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ─── /team_remove ───────────────────────────────────────────
    @app_commands.command(name="team_remove", description="Remove a player from a slot")
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="🧤 GK", value="gk"),
            app_commands.Choice(name="🛡️ DF1", value="df1"),
            app_commands.Choice(name="🛡️ DF2", value="df2"),
            app_commands.Choice(name="🛡️ DF3", value="df3"),
            app_commands.Choice(name="🛡️ DF4", value="df4"),
            app_commands.Choice(name="🛡️ DF5", value="df5"),
            app_commands.Choice(name="⚙️ MD1", value="md1"),
            app_commands.Choice(name="⚙️ MD2", value="md2"),
            app_commands.Choice(name="⚙️ MD3", value="md3"),
            app_commands.Choice(name="⚙️ MD4", value="md4"),
            app_commands.Choice(name="⚙️ MD5", value="md5"),
            app_commands.Choice(name="⚔️ AT1", value="at1"),
            app_commands.Choice(name="⚔️ AT2", value="at2"),
            app_commands.Choice(name="⚔️ AT3", value="at3"),
            app_commands.Choice(name="⚔️ AT4", value="at4")
        ]
    )
    async def team_remove(self, interaction: discord.Interaction, slot: str):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def do_remove():
            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            if not lineup:
                return "no_lineup"
            card = getattr(lineup, slot, None)
            if not card:
                return "empty"
            name = card.template.name
            setattr(lineup, slot, None)
            lineup.save()
            return f"ok:{name}"

        result = await do_remove()
        if result == "no_lineup":
            await interaction.response.send_message(
                "Use `/team_begin` first!", ephemeral=True
            )
        elif result == "empty":
            await interaction.response.send_message(
                f"Slot **{slot.upper()}** is already empty.", ephemeral=True
            )
        elif result.startswith("ok:"):
            name = result.split(":")[1]
            await interaction.response.send_message(
                f"🗑️ Removed **{name}** from **{slot.upper()}**.", ephemeral=True
            )

    # ─── /team_sub ──────────────────────────────────────────────
    @app_commands.command(
        name="team_sub", description="Set a substitute player on the bench"
    )
    @app_commands.autocomplete(player_name=player_autocomplete)
    @app_commands.choices(
        slot=[
            app_commands.Choice(name="🔄 SUB1", value="sub1"),
            app_commands.Choice(name="🔄 SUB2", value="sub2"),
            app_commands.Choice(name="🔄 SUB3", value="sub3"),
        ]
    )
    async def team_sub(
        self, interaction: discord.Interaction, slot: str, player_name: str
    ):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def do_sub():
            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            if not lineup:
                return "no_lineup"

            # Try card_id first (autocomplete sends IDs), then fall back to name
            user_card = (
                UserCard.objects.filter(owner=user, card_id=player_name)
                .select_related("template")
                .first()
            )
            if not user_card:
                user_card = (
                    UserCard.objects.filter(
                        owner=user, template__name__icontains=player_name
                    )
                    .select_related("template")
                    .first()
                )

            if not user_card:
                return "no_card"

            # Check duplicate across all slots + subs
            all_slots = get_formation_slots(lineup.formation) + ["sub1", "sub2", "sub3"]
            for s in all_slots:
                if s == slot:
                    continue
                existing = getattr(lineup, s, None)
                if existing and existing.template.name.lower() == user_card.template.name.lower():
                    return f"duplicate:{user_card.template.name}"

            setattr(lineup, slot, user_card)
            lineup.save()
            return f"ok:{user_card.template.name}:{user_card.template.position}:{slot.upper()}"

        result = await do_sub()
        if result == "no_lineup":
            await interaction.response.send_message(
                "Use `/team_begin` first!", ephemeral=True
            )
        elif result == "no_card":
            await interaction.response.send_message(
                f"❌ No card matching **{player_name}**.", ephemeral=True
            )
        elif result.startswith("duplicate:"):
            name = result.split(":")[1]
            await interaction.response.send_message(
                f"❌ **{name}** is already in your squad!", ephemeral=True
            )
        elif result.startswith("ok:"):
            _, name, pos, s = result.split(":")
            await interaction.response.send_message(
                f"🔄 **{name}** ({pos}) → Bench **{s}**", ephemeral=True
            )

    # ── Tactic command ──────────────────────────────────────────

    @app_commands.command(
        name="tactic",
        description="Set your team's tactic for Quick Sim matches"
    )
    @app_commands.choices(tactic=[
        app_commands.Choice(name="⚖️ Balanced", value="balanced"),
        app_commands.Choice(name="⚔️ Attacking (+ATT, -DEF)", value="attacking"),
        app_commands.Choice(name="🛡️ Defensive (+DEF, -ATT)", value="defensive"),
        app_commands.Choice(name="⚙️ Possession (+MID, -ATT)", value="possession"),
        app_commands.Choice(name="⚡ Counter Attack (+DEF, counter bonus)", value="counter"),
    ])
    async def tactic(
        self, interaction: discord.Interaction, tactic: app_commands.Choice[str]
    ):
        await interaction.response.defer(ephemeral=True)

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id
        )
        lineup = await Lineup.objects.filter(owner=user, is_active=True).afirst()
        if not lineup:
            return await interaction.followup.send(
                "❌ You don't have an active lineup! Use `/squad` first.",
                ephemeral=True,
            )

        lineup.tactic = tactic.value
        await lineup.asave(update_fields=["tactic"])

        TACTIC_EMOJIS = {
            "balanced": "⚖️",
            "attacking": "⚔️",
            "defensive": "🛡️",
            "possession": "⚙️",
            "counter": "⚡",
        }
        emoji = TACTIC_EMOJIS.get(tactic.value, "📋")
        await interaction.followup.send(
            f"{emoji} Tactic set to **{tactic.name}**!",
            ephemeral=True,
        )

    # ── Manager Command Group ────────────────────────────────────
    manager_group = app_commands.Group(
        name="manager", description="Manage your squad manager and tactical boosts"
    )

    async def owned_manager_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        @sync_to_async
        def get_choices():
            qs = UserOwnedManager.objects.filter(user_id=interaction.user.id).select_related("manager")
            if current:
                qs = qs.filter(manager__name__icontains=current)
            return list(qs[:25])
        items = await get_choices()
        return [
            app_commands.Choice(
                name=f"{item.manager.name} ({item.manager.preferred_formation} | +{item.manager.formation_boost_pct}% {item.manager.formation_boost_type})",
                value=item.manager.name
            )
            for item in items
        ]

    async def all_manager_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        @sync_to_async
        def get_choices():
            qs = Manager.objects.all()
            if current:
                qs = qs.filter(name__icontains=current)
            return list(qs[:25])
        items = await get_choices()
        return [
            app_commands.Choice(
                name=f"{m.name} ({m.rarity} | {m.preferred_formation})",
                value=m.name
            )
            for m in items
        ]

    @manager_group.command(name="view", description="View your currently equipped manager")
    async def manager_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def get_mgr():
            um = UserManager.objects.select_related("manager").filter(owner=user).first()
            if not um:
                return None, None
            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            form = lineup.formation if lineup else "None"
            tactic = lineup.tactic if lineup else "balanced"
            return um.manager, (form, tactic)

        mgr, squad_info = await get_mgr()
        if not mgr:
            return await interaction.followup.send(
                "👔 You don't have a manager equipped! Use `/manager equip` to equip one from your collection.",
                ephemeral=True
            )

        form, tactic = squad_info
        from core.bot_logic.quick_sim import normalize_formation_str
        form_syn = normalize_formation_str(mgr.preferred_formation) in ("all", normalize_formation_str(form))
        tactic_syn = (mgr.preferred_tactic == "ALL" or mgr.preferred_tactic.lower() == (tactic or "").lower())

        embed = discord.Embed(
            title=f"👔 Manager: {mgr.name}",
            description=f"**Nationality:** {mgr.nationality or 'Unknown'} | **Rarity:** {mgr.rarity}",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="📐 Preferred Formation",
            value=f"`{mgr.preferred_formation}` — Boost: **+{mgr.formation_boost_pct}% {mgr.formation_boost_type}**\n"
                  f"Status: {'🟢 Active Synergy' if form_syn else f'⚪ Inactive (Your squad uses `{form}`)'}",
            inline=False
        )
        if mgr.tactic_boost_pct > 0 and mgr.tactic_boost_type != "NONE":
            embed.add_field(
                name="⚡ Preferred Tactic",
                value=f"`{mgr.preferred_tactic.capitalize()}` — Boost: **+{mgr.tactic_boost_pct}% {mgr.tactic_boost_type}**\n"
                      f"Status: {'🟢 Active Synergy' if tactic_syn else f'⚪ Inactive (Your tactic is `{tactic.capitalize()}`)'}",
                inline=False
            )
        embed.set_footer(text="Managers provide powerful stat boosts in Quick Sim matches!")
        await interaction.followup.send(embed=embed)

    @manager_group.command(name="list", description="List all managers in your collection")
    async def manager_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def get_all():
            um = UserManager.objects.filter(owner=user).first()
            equipped_id = um.manager_id if um else None
            owned = list(UserOwnedManager.objects.filter(user=user).select_related("manager").order_by("manager__name"))
            return owned, equipped_id

        owned, eq_id = await get_all()
        if not owned:
            return await interaction.followup.send(
                "👔 You don't own any managers yet! Catch them in wild spawns or with `/drop`.",
                ephemeral=True
            )

        lines = []
        for item in owned:
            m = item.manager
            tag = " ⭐ **[EQUIPPED]**" if m.id == eq_id else ""
            lines.append(
                f"• **{m.name}** ({m.rarity}){tag}\n"
                f"  Formation: `{m.preferred_formation}` (+{m.formation_boost_pct}% {m.formation_boost_type})"
            )

        embed = discord.Embed(
            title=f"👔 {interaction.user.display_name}'s Manager Locker ({len(owned)})",
            description="\n\n".join(lines),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Use /manager equip <name> to lead your team!")
        await interaction.followup.send(embed=embed)

    @manager_group.command(name="equip", description="Equip a manager from your locker")
    @app_commands.describe(manager_name="Name of the manager to equip")
    async def manager_equip(self, interaction: discord.Interaction, manager_name: str):
        await interaction.response.defer(ephemeral=True)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def do_equip():
            item = UserOwnedManager.objects.filter(user=user, manager__name__iexact=manager_name).select_related("manager").first()
            if not item:
                return None
            UserManager.objects.update_or_create(owner=user, defaults={"manager": item.manager})
            return item.manager

        mgr = await do_equip()
        if not mgr:
            return await interaction.followup.send(
                f"❌ You don't own a manager named **{manager_name}**! Check `/manager list`.",
                ephemeral=True
            )

        await interaction.followup.send(
            f"✅ **{mgr.name}** is now your active club manager!\n"
            f"Formation: `{mgr.preferred_formation}` (+{mgr.formation_boost_pct}% {mgr.formation_boost_type})",
            ephemeral=True
        )

    @manager_group.command(name="show", description="Show detailed card design and tactical boosts of a manager")
    @app_commands.describe(manager_name="Manager name to inspect (or leave blank for equipped manager)")
    @app_commands.autocomplete(manager_name=all_manager_autocomplete)
    async def manager_show(self, interaction: discord.Interaction, manager_name: str | None = None):
        await interaction.response.defer(ephemeral=False)
        from core.models import Manager, UserManager, UserOwnedManager
        from core.utils import generate_manager_image
        import asyncio

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def get_manager_data():
            if manager_name:
                mgr = Manager.objects.filter(name__iexact=manager_name).first() or Manager.objects.filter(name__icontains=manager_name).first()
            else:
                um = UserManager.objects.select_related("manager").filter(owner=user).first()
                mgr = um.manager if um else None

            if not mgr:
                return None, False, False

            is_owned = UserOwnedManager.objects.filter(user=user, manager=mgr).exists()
            is_equipped = UserManager.objects.filter(owner=user, manager=mgr).exists()
            return mgr, is_owned, is_equipped

        mgr, is_owned, is_equipped = await get_manager_data()
        if not mgr:
            msg = f"❌ Manager **{manager_name}** not found." if manager_name else "❌ You don't have a manager equipped! Specify a manager name: `/manager show <name>`."
            return await interaction.followup.send(msg, ephemeral=True)

        image_buffer = await asyncio.to_thread(generate_manager_image, mgr)
        file = discord.File(fp=image_buffer, filename=f"manager_{mgr.id}.png")

        ownership_badge = " ⭐ **Equipped Active Coach**" if is_equipped else (" ✅ **In Your Locker**" if is_owned else " 🔒 **Not In Your Locker**")

        embed = discord.Embed(
            title=f"👔 Manager: {mgr.name}",
            description=f"**Nationality:** {mgr.nationality or 'Unknown'} | **Rarity:** {mgr.rarity}\n**Status:**{ownership_badge}",
            color=discord.Color.gold() if mgr.rarity in ("Legendary", "World-Class") else discord.Color.blue()
        )
        embed.set_image(url=f"attachment://manager_{mgr.id}.png")

        pref_f = mgr.preferred_formation.upper() if mgr.preferred_formation != "ALL" else "Any Formation"
        embed.add_field(
            name="📐 Formation Synergy",
            value=f"Preferred: `{pref_f}`\nBoost: **+{mgr.formation_boost_pct}% {mgr.formation_boost_type}**",
            inline=True
        )

        tactic_name = getattr(mgr, "get_preferred_tactic_display", lambda: mgr.preferred_tactic)()
        tactic_boost_txt = f"+{mgr.tactic_boost_pct}% {mgr.tactic_boost_type}" if mgr.tactic_boost_pct > 0 and mgr.tactic_boost_type != "NONE" else "None"
        embed.add_field(
            name="⚡ Tactic Synergy",
            value=f"Preferred: `{tactic_name}`\nBoost: **{tactic_boost_txt}**",
            inline=True
        )
        embed.set_footer(text="Managers grant powerful formation & tactic stat boosts in Quick Sim!")
        await interaction.followup.send(file=file, embed=embed)

    # ── Logo Command Group ───────────────────────────────────────
    logo_group = app_commands.Group(
        name="logo", description="Manage your club logo and crest boosts"
    )

    async def owned_logo_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        @sync_to_async
        def get_choices():
            qs = UserOwnedLogo.objects.filter(user_id=interaction.user.id).select_related("logo")
            if current:
                qs = qs.filter(logo__name__icontains=current)
            return list(qs[:25])
        items = await get_choices()
        return [
            app_commands.Choice(
                name=f"{item.logo.name} (+{item.logo.boost_val})",
                value=item.logo.name
            )
            for item in items
        ]

    @logo_group.command(name="view", description="View your currently equipped club logo")
    async def logo_view(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def get_logo():
            ul = UserLogo.objects.select_related("logo").filter(owner=user).first()
            return ul.logo if ul else None

        logo = await get_logo()
        if not logo:
            return await interaction.followup.send(
                "🛡️ You don't have a logo equipped! Use `/logo equip` to equip one.",
                ephemeral=True
            )

        embed = discord.Embed(
            title=f"🛡️ Equipped Logo: {logo.name}",
            description=f"**Club:** {logo.club or 'Custom'} | **Rarity:** {logo.rarity}\n**Bonus:** +{logo.boost_val} to team stats",
            color=discord.Color.green()
        )
        await interaction.followup.send(embed=embed)

    @logo_group.command(name="list", description="List all club logos in your collection")
    async def logo_list(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=False)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def get_all():
            ul = UserLogo.objects.filter(owner=user).first()
            eq_id = ul.logo_id if ul else None
            owned = list(UserOwnedLogo.objects.filter(user=user).select_related("logo").order_by("logo__name"))
            return owned, eq_id

        owned, eq_id = await get_all()
        if not owned:
            return await interaction.followup.send(
                "🛡️ You don't own any logos yet! Catch them in wild spawns or with `/drop`.",
                ephemeral=True
            )

        lines = [
            f"• **{item.logo.name}** (+{item.logo.boost_val} {item.logo.rarity})" + (" ⭐ **[EQUIPPED]**" if item.logo.id == eq_id else "")
            for item in owned
        ]
        embed = discord.Embed(
            title=f"🛡️ {interaction.user.display_name}'s Club Crests ({len(owned)})",
            description="\n".join(lines),
            color=discord.Color.blue()
        )
        embed.set_footer(text="Use /logo equip <name> to set your team crest!")
        await interaction.followup.send(embed=embed)

    @logo_group.command(name="equip", description="Equip a club logo from your collection")
    @app_commands.describe(logo_name="Name of the logo to equip")
    async def logo_equip(self, interaction: discord.Interaction, logo_name: str):
        await interaction.response.defer(ephemeral=True)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def do_equip():
            item = UserOwnedLogo.objects.filter(user=user, logo__name__iexact=logo_name).select_related("logo").first()
            if not item:
                return None
            UserLogo.objects.update_or_create(owner=user, defaults={"logo": item.logo})
            return item.logo

        logo = await do_equip()
        if not logo:
            return await interaction.followup.send(
                f"❌ You don't own a logo named **{logo_name}**! Check `/logo list`.",
                ephemeral=True
            )

        await interaction.followup.send(
            f"✅ **{logo.name}** is now your active club crest (+{logo.boost_val} stat bonus)!",
            ephemeral=True
        )

    # ── /dupes Command ──────────────────────────────────────────
    @app_commands.command(name="dupes", description="Show your amount of duplicate copies for a card")
    @app_commands.describe(card="Card name to check duplicates for")
    @app_commands.autocomplete(card=template_autocomplete)
    async def dupes(self, interaction: discord.Interaction, card: str):
        await interaction.response.defer(ephemeral=False)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def check_dupes():
            qs = CardTemplate.objects.all()
            if "|" in card:
                name, ev = card.split("|", 1)
                t = qs.filter(name=name, event_name=ev).first()
            else:
                t = qs.filter(name__iexact=card).first() or qs.filter(name__icontains=card).first()

            if not t:
                return None, 0, []

            owned_cards = list(UserCard.objects.filter(owner=user, template=t).order_by("-caught_at"))
            return t, len(owned_cards), owned_cards

        template, count, owned_cards = await check_dupes()
        if not template:
            return await interaction.followup.send(f"❌ No card template found matching **'{card}'**.", ephemeral=True)

        if count == 0:
            return await interaction.followup.send(
                f"📦 You do not own any copies of **{template.display_name}** ({template.rarity}).",
                ephemeral=True
            )

        if count >= 50:
            next_upgrade = "💎 **Diamond Tier** ready! Use `/upgrade`"
        elif count >= 25:
            next_upgrade = f"🥇 **Gold Tier** ready! ({count}/50 for Diamond)"
        elif count >= 10:
            next_upgrade = f"🥈 **Silver Tier** ready! ({count}/25 for Gold)"
        else:
            next_upgrade = f"⚪ Need **{10 - count}** more copies for 🥈 Silver Tier"

        embed = discord.Embed(
            title=f"🃏 Duplicate Copies: {template.display_name}",
            description=f"You own **{count}** copies of this card.\n\n"
                        f"**Next Upgrade:** {next_upgrade}",
            color=discord.Color.gold()
        )
        embed.add_field(
            name="📊 Upgrade Requirements",
            value="• 🥈 **Silver (10 copies)**: +5% ATK & DEF boost\n"
                  "• 🥇 **Gold (25 copies)**: +10% ATK & DEF boost\n"
                  "• 💎 **Diamond (50 copies)**: +25% ATK & DEF boost",
            inline=False
        )
        embed.set_footer(text="Use /upgrade to power up your card using duplicates!")
        await interaction.followup.send(embed=embed)

    # ── /upgrade Command ────────────────────────────────────────
    @app_commands.command(name="upgrade", description="Upgrade a card using duplicate copies for a stat boost")
    @app_commands.describe(card="Card name or Card ID to upgrade")
    @app_commands.autocomplete(card=template_autocomplete)
    async def upgrade(self, interaction: discord.Interaction, card: str):
        await interaction.response.defer(ephemeral=False)
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        @sync_to_async
        def perform_upgrade():
            clean_id = card.strip("# ")
            target_card = UserCard.objects.filter(owner=user, card_id__iexact=clean_id).select_related("template").first()
            if target_card:
                t = target_card.template
            else:
                qs = CardTemplate.objects.all()
                if "|" in card:
                    name, ev = card.split("|", 1)
                    t = qs.filter(name=name, event_name=ev).first()
                else:
                    t = qs.filter(name__iexact=card).first() or qs.filter(name__icontains=card).first()

            if not t:
                return "not_found", None, None, None

            owned = list(UserCard.objects.filter(owner=user, template=t).order_by("caught_at"))
            if not owned:
                return "not_owned", t, None, None

            lineup = Lineup.objects.filter(owner=user, is_active=True).first()
            active_ids = set()
            if lineup:
                for s in get_formation_slots(lineup.formation) + ["sub1", "sub2", "sub3"]:
                    c = getattr(lineup, s, None)
                    if c:
                        active_ids.add(c.id)

            if not target_card:
                for c in owned:
                    if c.id in active_ids:
                        target_card = c
                        break
                if not target_card:
                    upgraded_first = [c for c in owned if c.upgrade_tier != "NONE"]
                    target_card = upgraded_first[0] if upgraded_first else owned[0]

            curr_tier = target_card.upgrade_tier
            if curr_tier == "NONE":
                target_tier = "SILVER"
                needed_copies = 10
                boost_str = "+5% ATK & DEF"
            elif curr_tier == "SILVER":
                target_tier = "GOLD"
                needed_copies = 25
                boost_str = "+10% ATK & DEF"
            elif curr_tier == "GOLD":
                target_tier = "DIAMOND"
                needed_copies = 50
                boost_str = "+25% ATK & DEF"
            else:
                return "max_tier", t, target_card, None

            # Filter candidates to burn: exclude target card and active lineup cards
            burn_candidates = [c for c in owned if c.id != target_card.id and c.id not in active_ids]
            if len(burn_candidates) < needed_copies:
                return "insufficient", t, (len(burn_candidates), needed_copies, target_tier), None

            # Prioritize sacrificing standard non-refractor, non-upgraded duplicates first
            burn_candidates.sort(key=lambda c: (
                0 if (c.refractor == "NONE" and c.upgrade_tier == "NONE") else 1,
                0 if c.refractor == "NONE" else 1,
                0 if c.upgrade_tier == "NONE" else 1,
                c.caught_at
            ))

            burn_ids = [c.id for c in burn_candidates[:needed_copies]]
            UserCard.objects.filter(id__in=burn_ids).delete()

            target_card.upgrade_tier = target_tier
            target_card.save(update_fields=["upgrade_tier"])

            user.cards_collected = max(0, user.cards_collected - needed_copies)
            user.save(update_fields=["cards_collected"])

            return "success", t, target_tier, (needed_copies, boost_str, target_card)

        status, t, data1, data2 = await perform_upgrade()
        if status == "not_found":
            return await interaction.followup.send(f"❌ No card matching **'{card}'**.", ephemeral=True)
        elif status == "not_owned":
            return await interaction.followup.send(f"❌ You don't own any copies of **{t.display_name}**.", ephemeral=True)
        elif status == "max_tier":
            return await interaction.followup.send(f"💎 **{t.display_name}** is already at maximum Diamond tier (+25%)!", ephemeral=True)
        elif status == "insufficient":
            avail, needed, target_tier = data1
            return await interaction.followup.send(
                f"❌ Insufficient duplicates! You have **{avail}** sacrificeable duplicates, but need **{needed}** copies to upgrade to **{target_tier}**.\n*(Note: Cards currently in your active squad lineup cannot be sacrificed)*",
                ephemeral=True
            )

        target_tier = data1
        needed_copies, boost_str, target_card = data2
        badge = "🥈" if target_tier == "SILVER" else ("🥇" if target_tier == "GOLD" else "💎")

        from core.utils import generate_card_image
        import asyncio
        image_buffer = await asyncio.to_thread(generate_card_image, target_card, target_card.refractor)
        file = discord.File(fp=image_buffer, filename=f"{target_card.template.name}.png")

        ref_str = f" • ✨ {target_card.refractor} Refractor" if target_card.refractor != "NONE" else ""

        embed = discord.Embed(
            title=f"🎉 Card Upgraded: {target_card.template.display_name}!",
            description=f"{badge} **{target_card.template.name}** reached **{target_tier.title()} Tier**!\n\n"
                        f"⚡ **Stat Boost:** **{boost_str}** in Quick Sim matches\n"
                        f"🔥 **Sacrificed:** {needed_copies} duplicate copies",
            color=discord.Color.green()
        )
        embed.set_image(url=f"attachment://{target_card.template.name}.png")
        embed.set_footer(text=f"Card ID: #{target_card.card_id}{ref_str} • Enjoy your powered-up card on the pitch!")
        await interaction.followup.send(file=file, embed=embed)


async def setup(bot):
    await bot.add_cog(TeamCog(bot))
