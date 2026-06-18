import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, UserCard
from core.utils import player_autocomplete

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
        self, interaction: discord.Interaction, member: discord.Member | None = None
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
                return None, None, None, None

            formation_key = lineup.formation
            f_info = FORMATIONS.get(formation_key, FORMATIONS["433"])
            slots = get_formation_slots(formation_key)

            # Build the display
            sections = {"GK": [], "DEF": [], "MID": [], "ATT": []}
            for slot_name in slots:
                card = getattr(lineup, slot_name, None)
                prefix = slot_name[:2]
                group = SLOT_TO_GROUP.get(prefix, "MID")

                if card:
                    # Force evaluation of the template FK
                    template = card.template
                    sections[group].append(
                        f"• **{template.name}** ({template.position}) — {template.ovr} OVR"
                    )
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

            return f_info, sections, (power, filled, total), sub_lines

        result = await get_lineup_data()

        if result[0] is None or result[1] is None or result[2] is None or result[3] is None:
            await interaction.followup.send(
                f"**{target.name}** doesn't have a squad yet!", ephemeral=False
            )
            return

        f_info, sections, stats, sub_lines = result
        power, filled, total = stats

        # Build embed
        embed = discord.Embed(
            title=f"🏆 {target.name}'s Squad",
            description=f"**Formation:** {f_info['name']}",
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

        embed.set_footer(text="Use /team_add to place players • /team_sub to set subs")
        await interaction.followup.send(embed=embed, ephemeral=False)

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


async def setup(bot):
    await bot.add_cog(TeamCog(bot))
