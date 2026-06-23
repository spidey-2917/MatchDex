import asyncio
import logging

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.bot_logic.SpawningCog import CatchView
from core.models import (
    Blacklist,
    CardTemplate,
    CommandLog,
    DiscordUser,
    ServerSettings,
    UserCard,
)
from core.settings import settings
from core.utils import generate_card_image, player_autocomplete, template_autocomplete

log = logging.getLogger("matchdex.admin")


class AdminCog(commands.Cog, name="Admin"):
    """
    Bot owner / admin command group.

    All commands live under /admin and its subgroups.
    The tree is only synced to guilds listed in config.yml → admin.guild-ids,
    so these commands are invisible everywhere else.
    """

    def __init__(self, bot):
        self.bot = bot

    # ── Hierarchy: /admin ────────────────────────────────────
    admin_group = app_commands.Group(
        name="admin",
        description="Owner and admin commands",
        guild_ids=settings.admin_guild_ids,
    )
    spawn_group = app_commands.Group(
        name="spawn", description="Card spawning tools", parent=admin_group
    )
    md_group = app_commands.Group(
        name="md", description="Matchdex core admin commands", parent=admin_group
    )
    info_group = app_commands.Group(
        name="info", description="View info for users and guilds", parent=admin_group
    )
    bl_group = app_commands.Group(
        name="blacklist", description="Manage user blacklist", parent=admin_group
    )
    blg_group = app_commands.Group(
        name="blacklist-guild", description="Manage guild blacklist", parent=admin_group
    )
    logs_group = app_commands.Group(
        name="logs", description="Manage logging channels", parent=admin_group
    )
    history_group = app_commands.Group(
        name="history", description="View command/card history", parent=admin_group
    )
    server_group = app_commands.Group(
        name="server", description="Manage admin servers", parent=admin_group
    )
    packs_group = app_commands.Group(
        name="packs", description="Manage user packs", parent=admin_group
    )

    async def check_admin(self, interaction: discord.Interaction) -> bool:
        if not await self.bot.is_admin(interaction.user):
            await interaction.response.send_message(
                "Only authorized admins can use this command.", ephemeral=True
            )
            return False
        return True

    # ══════════════════════════════════════════════════════════
    #  /admin spawn — replaces legacy /admin_spawn + /admin_spawn_card
    # ══════════════════════════════════════════════════════════

    async def event_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        """Autocomplete for unique event names from CardTemplate."""
        @sync_to_async
        def get_events():
            qs = CardTemplate.objects.values_list("event_name", flat=True).distinct()
            if current:
                qs = qs.filter(event_name__icontains=current)
            return list(qs[:25])
        
        events = await get_events()
        return [
            app_commands.Choice(name=e, value=e)
            for e in events if e
        ]

    @spawn_group.command(
        name="random", description="Spawn catchable cards in this channel"
    )
    @app_commands.describe(
        count="Number of cards to spawn (1-15)",
        event="Filter spawns to a specific event (optional)"
    )
    @app_commands.autocomplete(event=event_autocomplete)
    async def spawn_random(self, interaction: discord.Interaction, count: int = 5, event: str | None = None):
        if not await self.check_admin(interaction):
            return

        if not interaction.channel or not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message("This command can only be used in a text channel.", ephemeral=True)
            return

        count = max(1, min(count, 15))
        event_label = f" (event: {event})" if event else ""
        await interaction.response.send_message(
            f"Spawning {count} card(s){event_label}…", ephemeral=True
        )

        from core.utils import pick_random_card

        spawned = 0
        for _ in range(count):
            card = await sync_to_async(pick_random_card)(
                "PACK",
                event_name_filter=event,
            )
            if not card:
                continue

            image_buffer = await asyncio.to_thread(generate_card_image, card)
            file = discord.File(fp=image_buffer, filename=f"{card.name}.png")

            embed = discord.Embed(
                title="A new card has spawned!",
                description="Click the button below to catch it!",
                color=discord.Color.gold(),
            )
            embed.set_image(url=f"attachment://{card.name}.png")
            embed.set_footer(
                text=f"Rarity: {card.rarity} | Position: {card.position} | OVR: {card.ovr}"
            )

            view = CatchView(card)
            message = await interaction.channel.send(file=file, embed=embed, view=view)
            view.message = message
            spawned += 1
            await asyncio.sleep(0.5)

        await interaction.followup.send(
            f"Done — spawned **{spawned}** card(s).", ephemeral=True
        )

    @spawn_group.command(name="card", description="Spawn a specific named player card")
    @app_commands.autocomplete(player_name=template_autocomplete)
    @app_commands.describe(
        player_name="The player to spawn", event="Specific event name (optional)"
    )
    async def spawn_card(
        self,
        interaction: discord.Interaction,
        player_name: str,
        event: str | None = None,
    ):
        if not await self.check_admin(interaction):
            return

        if not interaction.channel or not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message("This command can only be used in a text channel.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def find():
            # Handle autocomplete value (format: Name|Event)
            if "|" in player_name:
                name, ev = player_name.split("|")
                return CardTemplate.objects.filter(name=name, event_name=ev).first()

            qs = CardTemplate.objects.filter(name__icontains=player_name)
            if event:
                qs = qs.filter(event_name__icontains=event)
            return qs.first()

        card = await find()
        if not card:
            search_str = f"**'{player_name}'**"
            if event:
                search_str += f" in event **'{event}'**"
            await interaction.followup.send(
                f"No card found matching {search_str}.", ephemeral=True
            )
            return

        image_buffer = await asyncio.to_thread(generate_card_image, card)
        file = discord.File(fp=image_buffer, filename=f"{card.name}.png")

        embed = discord.Embed(
            title="A new card has spawned!",
            description="Click the button below to catch it!",
            color=discord.Color.gold(),
        )
        embed.set_image(url=f"attachment://{card.name}.png")
        embed.set_footer(
            text=f"Rarity: {card.rarity} | Position: {card.position} | OVR: {card.ovr}"
        )

        view = CatchView(card)
        await interaction.followup.send(
            f"Spawning **{card.display_name}** ({card.ovr} OVR, {card.rarity})…",
            ephemeral=True,
        )
        message = await interaction.channel.send(file=file, embed=embed, view=view)
        view.message = message

    # ══════════════════════════════════════════════════════════
    #  /admin give — replaces legacy /give + /give_full
    # ══════════════════════════════════════════════════════════

    @admin_group.command(name="give", description="Give a specific card to a user")
    @app_commands.autocomplete(player_search=template_autocomplete)
    async def give(
        self, interaction: discord.Interaction, user: discord.Member, player_search: str
    ):
        if not await self.check_admin(interaction):
            return

        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=user.id, defaults={"username": user.name}
        )

        @sync_to_async
        def find():
            # Handle autocomplete value (format: Name|Event)
            if "|" in player_search:
                name, ev = player_search.split("|", 1)
                return CardTemplate.objects.filter(name=name, event_name=ev).first()
            # Fallback to name search for manual typing
            return CardTemplate.objects.filter(name__icontains=player_search).first()

        card = await find()
        if not card:
            await interaction.response.send_message(
                f"No card found matching '{player_search}'.", ephemeral=True
            )
            return

        await UserCard.objects.acreate(owner=db_user, template=card)
        db_user.cards_collected += 1
        await db_user.asave()

        await interaction.response.send_message(
            f"Gave **{card.display_name}** ({card.rarity}) to {user.mention}!", ephemeral=True
        )

    @admin_group.command(
        name="give_full", description="Give a player every card in the game"
    )
    @app_commands.describe(include_all="Include ALL types (premium, specials, etc.)")
    async def give_full(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        include_all: bool = False,
    ):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=user.id, defaults={"username": user.name}
        )

        @sync_to_async
        def bulk_give():
            qs = (
                CardTemplate.objects.all()
                if include_all
                else CardTemplate.objects.exclude(card_type="PREMIUM")
            )
            cards = list(qs)
            created = 0
            for c in cards:
                if not UserCard.objects.filter(owner=db_user, template=c).exists():
                    UserCard.objects.create(owner=db_user, template=c)
                    created += 1
            return created

        count = await bulk_give()
        db_user.cards_collected += count
        await db_user.asave()

        scope = "ALL" if include_all else "non-premium"
        await interaction.followup.send(
            f"Gave {user.mention} every {scope} card ({count} new cards)!"
        )

    # ══════════════════════════════════════════════════════════
    #  /admin status, rarity, cooldown, guilds
    # ══════════════════════════════════════════════════════════

    @admin_group.command(
        name="status", description="Change the bot's presence status or activity text"
    )
    @app_commands.choices(
        status=[
            app_commands.Choice(name="Online", value="online"),
            app_commands.Choice(name="Idle", value="idle"),
            app_commands.Choice(name="Do Not Disturb", value="dnd"),
            app_commands.Choice(name="Invisible", value="invisible"),
        ]
    )
    async def admin_status(
        self,
        interaction: discord.Interaction,
        status: str | None = None,
        text: str | None = None,
    ):
        if not await self.check_admin(interaction):
            return

        stat_map = {
            "online": discord.Status.online,
            "idle": discord.Status.idle,
            "dnd": discord.Status.dnd,
            "invisible": discord.Status.invisible,
        }

        presence = stat_map.get(status, discord.Status.online) if status else None
        activity = discord.Game(name=text) if text else None

        if presence and activity:
            await self.bot.change_presence(status=presence, activity=activity)
        elif presence:
            await self.bot.change_presence(status=presence)
        elif activity:
            await self.bot.change_presence(activity=activity)

        await interaction.response.send_message("Status updated.", ephemeral=True)

    @admin_group.command(
        name="rarity", description="Show the rarity breakdown of all cards"
    )
    async def admin_rarity(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def get_counts():
            from django.db.models import Count

            return list(
                CardTemplate.objects.values("rarity")
                .annotate(count=Count("id"))
                .order_by("-count")
            )

        data = await get_counts()
        lines = [f"• **{row['rarity']}**: {row['count']} cards" for row in data]
        await interaction.followup.send(
            "**Card Rarity Breakdown:**\n" + "\n".join(lines)
        )

    @admin_group.command(
        name="cooldown", description="Show spawn cooldown status for a guild"
    )
    async def admin_cooldown(
        self, interaction: discord.Interaction, guild_id: str | None = None
    ):
        if not await self.check_admin(interaction):
            return

        target = int(guild_id) if guild_id else interaction.guild_id

        spawner = self.bot.get_cog("Spawning")
        if not spawner:
            await interaction.response.send_message(
                "Spawning system is offline.", ephemeral=True
            )
            return

        import time as _time

        last = spawner.last_spawn_time.get(target)
        count = spawner.message_counts.get(target, 0)
        threshold = spawner._get_threshold(target)

        embed = discord.Embed(
            title=f"Spawn Status for {target}", color=discord.Color.blue()
        )
        embed.add_field(name="Messages", value=f"{count} / {threshold}")
        if last:
            elapsed = int(_time.time() - last)
            embed.add_field(name="Last Spawn", value=f"{elapsed}s ago")
        else:
            embed.add_field(name="Last Spawn", value="Never")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @admin_group.command(name="guilds", description="Show guilds shared with a user")
    async def admin_guilds(
        self,
        interaction: discord.Interaction,
        user: discord.Member | None = None,
        user_id: str | None = None,
    ):
        if not await self.check_admin(interaction):
            return

        target_id = user.id if user else int(user_id) if user_id else None
        if not target_id:
            await interaction.response.send_message(
                "Provide a user or user_id.", ephemeral=True
            )
            return

        shared = [
            f"• {g.name} ({g.id})" for g in self.bot.guilds if g.get_member(target_id)
        ]

        msg = (
            f"**Shared Guilds ({len(shared)}):**\n" + "\n".join(shared[:20])
            if shared
            else "No shared guilds found."
        )
        if len(shared) > 20:
            msg += "\n…and more."

        await interaction.response.send_message(msg, ephemeral=True)

    # ══════════════════════════════════════════════════════════
    #  /admin packs
    # ══════════════════════════════════════════════════════════

    async def pack_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        from core.models import Pack
        @sync_to_async
        def get_choices():
            qs = Pack.objects.all()
            if current:
                qs = qs.filter(name__icontains=current)
            return list(qs[:25])
        
        packs = await get_choices()
        return [app_commands.Choice(name=p.name, value=p.code) for p in packs]

    @packs_group.command(name="add", description="Add packs to a user's stash")
    @app_commands.autocomplete(pack_name=pack_autocomplete)
    @app_commands.describe(pack_name="The code of the pack", count="Number of packs")
    async def packs_add(
        self,
        interaction: discord.Interaction,
        pack_name: str,
        user: discord.Member,
        count: int = 1,
    ):
        if not await self.check_admin(interaction):
            return
        
        from core.models import Pack, UserPack, DiscordUser
        pack_obj = await Pack.objects.filter(code=pack_name).afirst()
        if not pack_obj:
            return await interaction.response.send_message("Pack not found!", ephemeral=True)
            
        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=user.id, defaults={"username": user.name}
        )
        
        user_pack, _ = await UserPack.objects.aget_or_create(
            user=db_user, pack=pack_obj
        )
        user_pack.stash_count += count
        await user_pack.asave()
        
        await interaction.response.send_message(
            f"✅ Added {count}x **{pack_obj.name}** to {user.mention}'s stash.",
            ephemeral=False
        )

    # ══════════════════════════════════════════════════════════
    #  /admin md
    # ══════════════════════════════════════════════════════════

    @md_group.command(name="show", description="Show a card regardless of ownership, and last traded by")
    @app_commands.describe(bot_id="The unique bot ID of the card (e.g. #abcdef)")
    async def md_show(self, interaction: discord.Interaction, bot_id: str):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def get_card():
            return UserCard.objects.filter(card_id__iexact=bot_id).select_related("template", "owner", "traded_by").first()

        card = await get_card()
        if not card:
            return await interaction.followup.send(f"Card with ID **{bot_id}** not found.", ephemeral=True)

        image_buffer = await asyncio.to_thread(generate_card_image, card.template)
        file = discord.File(fp=image_buffer, filename=f"{card.template.name}.png")

        caught_str = f"Caught on {discord.utils.format_dt(card.caught_at, 'f')} ({discord.utils.format_dt(card.caught_at, 'R')})."

        traded_by_str = ""
        if card.traded_by:
            trader_name = card.traded_by.username or str(card.traded_by.discord_id)
            if interaction.guild:
                member = interaction.guild.get_member(card.traded_by.discord_id)
                if member:
                    trader_name = member.display_name
            traded_by_str = f"\nTraded by: {trader_name} ({card.traded_by.discord_id})"

        owner_name = card.owner.username or str(card.owner.discord_id)
        if interaction.guild:
            member = interaction.guild.get_member(card.owner.discord_id)
            if member:
                owner_name = member.display_name

        content = (
            f"ID: #{card.card_id}\n"
            f"Current Owner: {owner_name} ({card.owner.discord_id})\n"
            f"{caught_str}{traded_by_str}\n\n"
            f"ATK: {card.template.attack_stat}\n"
            f"DEF: {card.template.defence_stat}"
        )

        await interaction.followup.send(content=content, file=file, ephemeral=True)

    @admin_group.command(name="transfer", description="Transfer a specific card from one user to another")
    @app_commands.describe(
        source_user="User who currently owns the card",
        bot_id="The unique bot ID of the card (e.g. #abcdef)",
        target_user="User to transfer the card to"
    )
    async def admin_transfer(self, interaction: discord.Interaction, source_user: str, bot_id: str, target_user: str):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        from core.utils import clear_card_from_lineups
        
        async def resolve_user(user_input: str, create: bool = False):
            clean = user_input.strip("<@!> ")
            if clean.isdigit():
                user_id = int(clean)
                db_user = await DiscordUser.objects.filter(discord_id=user_id).afirst()
                if db_user:
                    return db_user
                if create:
                    try:
                        d_user = await self.bot.fetch_user(user_id)
                        db_user, _ = await DiscordUser.objects.aget_or_create(discord_id=user_id, defaults={"username": d_user.name})
                        return db_user
                    except:
                        pass
            return await DiscordUser.objects.filter(username__iexact=user_input).afirst()

        source_db = await resolve_user(source_user)
        if not source_db:
            return await interaction.followup.send(f"Source user '{source_user}' not found in database.", ephemeral=True)

        target_db = await resolve_user(target_user, create=True)
        if not target_db:
            return await interaction.followup.send(f"Target user '{target_user}' could not be resolved.", ephemeral=True)

        card = await UserCard.objects.filter(card_id__iexact=bot_id, owner=source_db).select_related("template").afirst()
        if not card:
            return await interaction.followup.send(f"Card **{bot_id}** not found in {source_db.username}'s inventory.", ephemeral=True)

        card.owner = target_db
        card.traded_by = source_db
        await card.asave()

        await clear_card_from_lineups(card.id)

        target_db.cards_collected += 1
        await target_db.asave()

        await interaction.followup.send(f"✅ Transferred **{card.template.display_name}** (#{card.card_id}) from **{source_db.username}** to **{target_db.username}**.")

    @admin_group.command(name="transfer_list", description="Transfer all cards from one user to another")
    @app_commands.describe(
        source_user="User whose entire collection will be transferred",
        target_user="User to receive the collection"
    )
    async def admin_transfer_list(self, interaction: discord.Interaction, source_user: str, target_user: str):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        async def resolve_user(user_input: str, create: bool = False):
            clean = user_input.strip("<@!> ")
            if clean.isdigit():
                user_id = int(clean)
                db_user = await DiscordUser.objects.filter(discord_id=user_id).afirst()
                if db_user:
                    return db_user
                if create:
                    try:
                        d_user = await self.bot.fetch_user(user_id)
                        db_user, _ = await DiscordUser.objects.aget_or_create(discord_id=user_id, defaults={"username": d_user.name})
                        return db_user
                    except:
                        pass
            return await DiscordUser.objects.filter(username__iexact=user_input).afirst()

        source_db = await resolve_user(source_user)
        if not source_db:
            return await interaction.followup.send(f"Source user '{source_user}' not found in database.", ephemeral=True)

        target_db = await resolve_user(target_user, create=True)
        if not target_db:
            return await interaction.followup.send(f"Target user '{target_user}' could not be resolved.", ephemeral=True)

        if source_db.discord_id == target_db.discord_id:
            return await interaction.followup.send("Source and target must be different users.", ephemeral=True)

        cards = UserCard.objects.filter(owner=source_db)
        count = await cards.acount()
        
        if count == 0:
            return await interaction.followup.send(f"**{source_db.username}** has no cards to transfer.", ephemeral=True)

        # Clear all cards from source lineups
        from core.models import Lineup
        await Lineup.objects.filter(owner=source_db).adelete()

        # Perform the bulk update
        await cards.aupdate(owner=target_db, traded_by=source_db)

        target_db.cards_collected += count
        await target_db.asave()

        await interaction.followup.send(f"✅ Transferred **{count}** cards from **{source_db.username}** to **{target_db.username}**.")

    @md_group.command(name="count", description="Count cards for a player or globally")
    async def md_count(
        self, interaction: discord.Interaction, user: discord.Member | None = None
    ):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)
        if user:
            u, _ = await DiscordUser.objects.aget_or_create(discord_id=user.id)
            count = await UserCard.objects.filter(owner=u).acount()
            await interaction.followup.send(f"{user.mention} owns **{count}** cards.")
        else:
            count = await UserCard.objects.all().acount()
            templates = await CardTemplate.objects.all().acount()
            await interaction.followup.send(
                f"**{count}** cards in existence across **{templates}** templates."
            )

    @md_group.command(
        name="access", description="Grant or revoke admin access for a user"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Grant", value="grant"),
            app_commands.Choice(name="Revoke", value="revoke"),
        ]
    )
    async def md_access(
        self, interaction: discord.Interaction, user: discord.Member, action: str
    ):
        if not await self.check_admin(interaction):
            return

        if action == "revoke":
            if user.id == interaction.user.id:
                await interaction.response.send_message(
                    "You cannot revoke your own access.", ephemeral=True
                )
                return
            if user.id in self.bot.admin_ids:
                await interaction.response.send_message(
                    f"{user.mention} is an absolute admin (Owner / .env) and cannot be revoked here.",
                    ephemeral=True,
                )
                return

        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=user.id, defaults={"username": user.name}
        )

        if action == "grant":
            db_user.is_admin = True
            await db_user.asave()
            await interaction.response.send_message(
                f"✅ Granted admin access to {user.mention}.", ephemeral=True
            )
        else:
            db_user.is_admin = False
            await db_user.asave()
            await interaction.response.send_message(
                f"❌ Revoked admin access from {user.mention}.", ephemeral=True
            )

    @md_group.command(
        name="grant", description="Grant or revoke premium access for a user"
    )
    @app_commands.choices(
        access_type=[
            app_commands.Choice(name="Premium", value="premium"),
        ],
        enabled=[
            app_commands.Choice(name="True", value="true"),
            app_commands.Choice(name="False", value="false"),
        ],
    )
    async def md_grant(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        access_type: str,
        enabled: str,
    ):
        if not await self.check_admin(interaction):
            return

        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=user.id, defaults={"username": user.name}
        )

        grant = enabled == "true"

        if access_type == "premium":
            db_user.is_premium = grant
            await db_user.asave()
            status = "✅ Granted" if grant else "❌ Revoked"
            await interaction.response.send_message(
                f"{status} **Premium** access for {user.mention}.", ephemeral=True
            )

    @md_group.command(
        name="premium_role", description="Add or remove a Discord role that grants premium access"
    )
    @app_commands.choices(
        action=[
            app_commands.Choice(name="Add", value="add"),
            app_commands.Choice(name="Remove", value="remove"),
            app_commands.Choice(name="List", value="list"),
        ]
    )
    @app_commands.describe(
        role="The Discord role to add/remove",
        action="Add, remove, or list premium roles",
    )
    async def md_premium_role(
        self,
        interaction: discord.Interaction,
        action: str,
        role: discord.Role | None = None,
    ):
        if not await self.check_admin(interaction):
            return

        from core.models import PremiumRole

        if action == "list":
            roles = []
            async for pr in PremiumRole.objects.all():
                roles.append(f"• <@&{pr.role_id}> (`{pr.role_id}`) — {pr.label or 'No label'}")
            if roles:
                await interaction.response.send_message(
                    "**Premium Roles:**\n" + "\n".join(roles), ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    "No premium roles configured.", ephemeral=True
                )
            return

        if not role:
            await interaction.response.send_message(
                "You must specify a role for add/remove.", ephemeral=True
            )
            return

        if action == "add":
            _, created = await PremiumRole.objects.aget_or_create(
                role_id=role.id, defaults={"label": role.name}
            )
            if created:
                await interaction.response.send_message(
                    f"✅ Added {role.mention} as a premium role.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{role.mention} is already a premium role.", ephemeral=True
                )
        elif action == "remove":
            deleted, _ = await PremiumRole.objects.filter(role_id=role.id).adelete()
            if deleted:
                await interaction.response.send_message(
                    f"❌ Removed {role.mention} from premium roles.", ephemeral=True
                )
            else:
                await interaction.response.send_message(
                    f"{role.mention} was not a premium role.", ephemeral=True
                )

    @md_group.command(
        name="reload_blacklist", description="Reload the in-memory blacklist cache"
    )
    async def md_reload_bl(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return
        await self.bot.load_blacklist_cache()
        await interaction.response.send_message(
            f"Blacklist cache reloaded: {len(self.bot.blacklisted_users)} users, "
            f"{len(self.bot.blacklisted_guilds)} guilds.",
            ephemeral=True,
        )

    @md_group.command(
        name="reload_all_cache", description="GLOBAL: Reload blacklist, config, and RESET all active bot states"
    )
    async def md_reload_all(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # 1. Reload Blacklist
        await self.bot.load_blacklist_cache()
        
        # 2. Reload Config
        from core.settings import read_settings
        read_settings()
        
        # 3. Clear Spawning Caches
        spawner = self.bot.get_cog("Spawning")
        if spawner:
            spawner.message_counts.clear()
            spawner.last_spawn_time.clear()
            spawner.spawn_thresholds.clear()
        
        # 4. Clear Active Trades
        from core.bot_logic.TradeCog import ACTIVE_TRADES
        from core.models import Trade
        
        # Mark all pending trades as cancelled in DB
        trade_count = len(ACTIVE_TRADES)
        ACTIVE_TRADES.clear()
        await Trade.objects.filter(status="PENDING").aupdate(status="CANCELLED")
        
        # 5. Clear Active Matches
        from core.bot_logic.MatchCog import ACTIVE_MATCHES
        match_count = len(ACTIVE_MATCHES)
        ACTIVE_MATCHES.clear()
        
        # 6. Clear Active Wagers
        wager_cog = self.bot.get_cog("Wagers")
        wager_count = 0
        if wager_cog:
            unique_wagers = list(set(wager_cog.active_wagers.values()))
            wager_count = len(unique_wagers)
            for view in unique_wagers:
                view.status = "🚫 Wager cancelled due to global cache reload."
                for child in view.children:
                    child.disabled = True
                view.stop()
            wager_cog.active_wagers.clear()

        await interaction.followup.send(
            "✅ **Global Cache Reload Complete**\n"
            "- Blacklist refreshed\n"
            "- `config.yml` reloaded\n"
            f"- Spawning timers reset globally\n"
            f"- {trade_count} active trades cancelled\n"
            f"- {match_count} active matches cancelled\n"
            f"- {wager_count} active wagers cancelled",
            ephemeral=True
        )

    @md_group.command(
        name="reload_config", description="Reload settings from config.yml without restart"
    )
    async def md_reload_config(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return
        
        from core.settings import read_settings
        read_settings()
        
        await interaction.response.send_message(
            "✅ Configuration reloaded from `config.yml` successfully!", ephemeral=True
        )

    @md_group.command(
        name="force_sync", description="Hard-reset command cache and spawn timers for this server"
    )
    async def md_force_sync(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return
        
        await interaction.response.defer(ephemeral=True)
        
        # 1. Reset Spawn Timers for this guild
        spawner = self.bot.get_cog("Spawning")
        if spawner:
            gid = interaction.guild_id
            spawner.message_counts[gid] = 0
            if gid in spawner.last_spawn_time:
                del spawner.last_spawn_time[gid]
            if gid in spawner.spawn_thresholds:
                del spawner.spawn_thresholds[gid]

        # 2. Hard Sync Command Tree
        try:
            # Clear guild-specific tree
            self.bot.tree.clear_commands(guild=interaction.guild)
            # Re-add admin group if this is an admin guild
            if interaction.guild_id in settings.admin_guild_ids:
                self.bot.tree.add_command(self.admin_group, guild=interaction.guild)
            
            # Sync
            await self.bot.tree.sync(guild=interaction.guild)
            await interaction.followup.send("✅ Command cache and spawn timers have been hard-reset for this server!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Force sync failed: {e}", ephemeral=True)

    @md_group.command(
        name="sync", description="Sync global commands manually (Warning: Ratelimited by Discord)"
    )
    async def md_sync(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return
        
        await interaction.response.defer(ephemeral=True)
        try:
            # Sync global command tree
            synced = await self.bot.tree.sync()
            await interaction.followup.send(f"✅ Successfully synced {len(synced)} global commands.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Failed to sync global commands: {e}", ephemeral=True)

    # ══════════════════════════════════════════════════════════
    #  /admin info
    # ══════════════════════════════════════════════════════════

    @info_group.command(name="user", description="Show information about a user")
    async def info_user(self, interaction: discord.Interaction, user: discord.Member):
        if not await self.check_admin(interaction):
            return

        u, _ = await DiscordUser.objects.aget_or_create(
            discord_id=user.id, defaults={"username": user.name}
        )
        embed = discord.Embed(
            title=f"User Info: {user.name}", color=discord.Color.green()
        )
        embed.add_field(name="Discord ID", value=user.id)
        embed.add_field(name="Cards", value=u.cards_collected)
        embed.add_field(name="Points", value=u.points)
        embed.add_field(name="Matches", value=f"{u.wins}W / {u.draws}D / {u.losses}L")
        embed.add_field(name="Premium", value="Yes" if u.is_premium else "No")
        embed.add_field(
            name="Joined",
            value=u.joined_at.strftime("%Y-%m-%d") if u.joined_at else "Unknown",
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @info_group.command(name="guild", description="Show information about a server")
    async def info_guild(
        self, interaction: discord.Interaction, guild_id: str | None = None
    ):
        if not await self.check_admin(interaction):
            return

        target_id = int(guild_id) if guild_id else interaction.guild_id
        target = self.bot.get_guild(target_id)

        embed = discord.Embed(
            title=f"Guild Info: {target.name if target else target_id}",
            color=discord.Color.purple(),
        )
        if target:
            embed.add_field(name="Members", value=target.member_count)
            embed.add_field(name="Owner", value=f"<@{target.owner_id}>")

        setting = await ServerSettings.objects.filter(guild_id=target_id).afirst()
        if setting:
            ch = (
                f"<#{setting.spawn_channel_id}>" if setting.spawn_channel_id else "None"
            )
            embed.add_field(name="Spawn Channel", value=ch)
        else:
            embed.add_field(name="Database Record", value="None")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ══════════════════════════════════════════════════════════
    #  /admin blacklist + /admin blacklist-guild
    # ══════════════════════════════════════════════════════════

    @bl_group.command(name="add", description="Add a user to the blacklist")
    async def bl_add(
        self,
        interaction: discord.Interaction,
        user: discord.Member,
        reason: str = "No reason provided",
    ):
        if not await self.check_admin(interaction):
            return
        await Blacklist.objects.aupdate_or_create(
            target_id=user.id, type="USER", defaults={"reason": reason}
        )
        # Update cache immediately
        self.bot.blacklisted_users.add(user.id)
        await interaction.response.send_message(
            f"Added {user.mention} to the blacklist.\nReason: {reason}",
            ephemeral=True,
        )

    @bl_group.command(name="remove", description="Remove a user from the blacklist")
    async def bl_remove(self, interaction: discord.Interaction, user: discord.Member):
        if not await self.check_admin(interaction):
            return
        deleted, _ = await Blacklist.objects.filter(
            target_id=user.id, type="USER"
        ).adelete()
        self.bot.blacklisted_users.discard(user.id)
        msg = (
            f"Removed {user.mention} from the blacklist."
            if deleted
            else f"{user.mention} was not blacklisted."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @bl_group.command(name="info", description="Check if a user is blacklisted")
    async def bl_info(self, interaction: discord.Interaction, user: discord.Member):
        if not await self.check_admin(interaction):
            return
        record = await Blacklist.objects.filter(target_id=user.id, type="USER").afirst()
        if record:
            await interaction.response.send_message(
                f"⛔ {user.mention} is **blacklisted**.\nReason: {record.reason}\n"
                f"Date: {record.created_at.strftime('%Y-%m-%d')}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"✅ {user.mention} is not blacklisted.", ephemeral=True
            )

    @blg_group.command(name="add", description="Add a guild to the blacklist")
    async def blg_add(
        self,
        interaction: discord.Interaction,
        guild_id: str,
        reason: str = "No reason provided",
    ):
        if not await self.check_admin(interaction):
            return
        gid = int(guild_id)
        await Blacklist.objects.aupdate_or_create(
            target_id=gid, type="GUILD", defaults={"reason": reason}
        )
        self.bot.blacklisted_guilds.add(gid)
        await interaction.response.send_message(
            f"Added guild `{guild_id}` to the blacklist.\nReason: {reason}",
            ephemeral=True,
        )

    @blg_group.command(name="remove", description="Remove a guild from the blacklist")
    async def blg_remove(self, interaction: discord.Interaction, guild_id: str):
        if not await self.check_admin(interaction):
            return
        gid = int(guild_id)
        deleted, _ = await Blacklist.objects.filter(
            target_id=gid, type="GUILD"
        ).adelete()
        self.bot.blacklisted_guilds.discard(gid)
        msg = (
            f"Removed guild `{guild_id}` from the blacklist."
            if deleted
            else f"Guild `{guild_id}` was not blacklisted."
        )
        await interaction.response.send_message(msg, ephemeral=True)

    @blg_group.command(name="info", description="Check if a guild is blacklisted")
    async def blg_info(self, interaction: discord.Interaction, guild_id: str):
        if not await self.check_admin(interaction):
            return
        record = await Blacklist.objects.filter(
            target_id=int(guild_id), type="GUILD"
        ).afirst()
        if record:
            await interaction.response.send_message(
                f"⛔ Guild `{guild_id}` is **blacklisted**.\nReason: {record.reason}\n"
                f"Date: {record.created_at.strftime('%Y-%m-%d')}",
                ephemeral=True,
            )
        else:
            await interaction.response.send_message(
                f"✅ Guild `{guild_id}` is not blacklisted.", ephemeral=True
            )

    # ══════════════════════════════════════════════════════════
    #  /admin logs
    # ══════════════════════════════════════════════════════════

    @logs_group.command(
        name="catchlogs", description="Set or clear the catch logs channel"
    )
    async def logs_catch(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        if not await self.check_admin(interaction):
            return
        setting, _ = await ServerSettings.objects.aget_or_create(
            guild_id=interaction.guild_id
        )
        if channel:
            setting.catch_log_channel_id = channel.id
            await setting.asave()
            await interaction.response.send_message(
                f"Catch logs → {channel.mention}.", ephemeral=True
            )
        else:
            setting.catch_log_channel_id = None
            await setting.asave()
            await interaction.response.send_message(
                "Catch logs disabled.", ephemeral=True
            )

    @logs_group.command(
        name="commandlogs", description="Set or clear the command logs channel"
    )
    async def logs_command(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel | None = None,
    ):
        if not await self.check_admin(interaction):
            return
        setting, _ = await ServerSettings.objects.aget_or_create(
            guild_id=interaction.guild_id
        )
        if channel:
            setting.command_log_channel_id = channel.id
            await setting.asave()
            await interaction.response.send_message(
                f"Command logs → {channel.mention}.", ephemeral=True
            )
        else:
            setting.command_log_channel_id = None
            await setting.asave()
            await interaction.response.send_message(
                "Command logs disabled.", ephemeral=True
            )

    # ══════════════════════════════════════════════════════════
    #  /admin history
    # ══════════════════════════════════════════════════════════

    @history_group.command(
        name="user", description="Show a user's recent catch/pack history"
    )
    async def hist_user(self, interaction: discord.Interaction, user: discord.Member):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def get_recent():
            return list(
                UserCard.objects.filter(owner__discord_id=user.id)
                .select_related("template")
                .order_by("-caught_at")[:10]
            )

        recent = await get_recent()
        if not recent:
            await interaction.followup.send("This user has no cards.")
            return

        lines = [
            f"• **{c.template.name}** ({c.template.ovr} OVR) — "
            f"ID: `{c.card_id}` — *{c.caught_at.strftime('%Y-%m-%d')}*"
            for c in recent
        ]
        embed = discord.Embed(
            title=f"Recent History: {user.name}",
            description="\n".join(lines),
            color=discord.Color.light_grey(),
        )
        await interaction.followup.send(embed=embed)

    @history_group.command(
        name="card", description="Show the history of a specific card"
    )
    async def hist_card(self, interaction: discord.Interaction, card_id: str):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        card = await (
            UserCard.objects.filter(card_id=card_id)
            .select_related("template", "owner")
            .afirst()
        )
        if not card:
            await interaction.followup.send(f"Card `{card_id}` not found.")
            return

        embed = discord.Embed(
            title=f"Card History: {card.template.name} ({card_id})",
            color=discord.Color.gold(),
        )
        embed.add_field(
            name="Current Owner", value=f"<@{card.owner.discord_id}>", inline=False
        )
        embed.add_field(
            name="Caught At",
            value=card.caught_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            inline=False,
        )
        await interaction.followup.send(embed=embed)


    # ══════════════════════════════════════════════════════════
    #  /admin server
    # ══════════════════════════════════════════════════════════

    @server_group.command(name="add", description="Add a server to the admin command list")
    async def server_add(self, interaction: discord.Interaction, guild_id: str):
        if not await self.check_admin(interaction):
            return

        try:
            gid = int(guild_id)
        except ValueError:
            return await interaction.response.send_message("Invalid Server ID.", ephemeral=True)

        if gid in settings.admin_guild_ids:
            return await interaction.response.send_message(f"Server `{gid}` is already in the admin list.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # 1. Update config.yml safely preserving comments
        from pathlib import Path
        path = Path("config.yml")
        if path.exists():
            content = path.read_text(encoding="utf-8")
            lines = content.split('\n')
            for i, line in enumerate(lines):
                if line.strip().startswith("guild-ids:"):
                    # Insert the new guild ID immediately after
                    lines.insert(i + 1, f"    - {gid}")
                    break
            path.write_text('\n'.join(lines), encoding="utf-8")

        # 2. Update memory
        settings.admin_guild_ids.append(gid)

        # 3. Add to tree for this specific guild and sync
        target_guild = discord.Object(id=gid)
        self.bot.tree.add_command(self.admin_group, guild=target_guild)
        
        try:
            await self.bot.tree.sync(guild=target_guild)
            await interaction.followup.send(f"✅ Successfully added `{gid}` to admin list and synced commands!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Added `{gid}` to config, but failed to sync commands right now: {e}\n\n*Admin commands will be available in that server after the next bot restart.*", ephemeral=True)


    @server_group.command(name="remove", description="Remove a server from the admin command list")
    async def server_remove(self, interaction: discord.Interaction, guild_id: str):
        if not await self.check_admin(interaction):
            return

        try:
            gid = int(guild_id)
        except ValueError:
            return await interaction.response.send_message("Invalid Server ID.", ephemeral=True)

        if gid not in settings.admin_guild_ids:
            return await interaction.response.send_message(f"Server `{gid}` is not in the admin list.", ephemeral=True)

        await interaction.response.defer(ephemeral=True)

        # 1. Update config.yml safely preserving comments
        from pathlib import Path
        path = Path("config.yml")
        if path.exists():
            content = path.read_text(encoding="utf-8")
            lines = content.split('\n')
            new_lines = []
            skip = False
            for line in lines:
                # Basic check to remove the specific ID under guild-ids
                if line.strip() == f"- {gid}":
                    continue
                new_lines.append(line)
            path.write_text('\n'.join(new_lines), encoding="utf-8")

        # 2. Update memory
        settings.admin_guild_ids.remove(gid)

        # 3. Remove from tree for this specific guild and sync
        target_guild = discord.Object(id=gid)
        self.bot.tree.remove_command(self.admin_group.name, guild=target_guild)
        
        try:
            await self.bot.tree.sync(guild=target_guild)
            await interaction.followup.send(f"✅ Successfully removed `{gid}` from admin list and unsynced commands!", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"⚠️ Removed `{gid}` from config, but failed to unsync commands right now: {e}\n\n*Admin commands will disappear in that server after the next bot restart.*", ephemeral=True)

    @server_group.command(name="list", description="List the top servers the bot is in")
    async def server_list(self, interaction: discord.Interaction):
        if not await self.check_admin(interaction):
            return
        
        # Sort guilds by member count
        guilds = sorted(self.bot.guilds, key=lambda g: g.member_count or 0, reverse=True)
        total = len(guilds)
        
        # Get spawn status for the top guilds
        from core.models import ServerSettings
        guild_ids = [g.id for g in guilds[:20]]
        
        @sync_to_async
        def get_settings_map():
            return {s.guild_id: s.spawn_channel_id for s in ServerSettings.objects.filter(guild_id__in=guild_ids)}
        
        spawn_map = await get_settings_map()

        embed = discord.Embed(title=f"🌍 Bot Servers ({total} total)", color=discord.Color.blue())
        
        text = ""
        for i, g in enumerate(guilds[:20]):
            has_spawn = "✅" if spawn_map.get(g.id) else "❌"
            text += f"{i+1}. {has_spawn} **{g.name}** (`{g.id}`) - {g.member_count} members\n"
        
        if not text:
            text = "No servers found."
            
        embed.description = text
        embed.set_footer(text=f"Showing top 20 by member count. Total: {total}")
            
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @server_group.command(name="leave", description="Make the bot leave a specific server")
    async def server_leave(self, interaction: discord.Interaction, guild_id: str):
        if not await self.check_admin(interaction):
            return
        
        try:
            gid = int(guild_id)
            guild = self.bot.get_guild(gid)
            
            if not guild:
                return await interaction.response.send_message(
                    f"❌ Server with ID `{guild_id}` not found in bot's cache.", 
                    ephemeral=True
                )
            
            name = guild.name
            await guild.leave()
            await interaction.response.send_message(
                f"✅ Successfully left **{name}** (`{guild_id}`).", 
                ephemeral=True
            )
        except ValueError:
            await interaction.response.send_message(
                "❌ Please provide a valid numerical Guild ID.", 
                ephemeral=True
            )
        except Exception as e:
            await interaction.response.send_message(
                f"❌ Failed to leave server: {e}", 
                ephemeral=True
            )

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
