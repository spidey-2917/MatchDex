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
from core.utils import generate_card_image, player_autocomplete

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

    @spawn_group.command(
        name="random", description="Spawn catchable cards in this channel"
    )
    @app_commands.describe(count="Number of cards to spawn (1-15)")
    async def spawn_random(self, interaction: discord.Interaction, count: int = 5):
        if not await self.check_admin(interaction):
            return

        count = max(1, min(count, 15))
        await interaction.response.send_message(
            f"Spawning {count} card(s)…", ephemeral=True
        )

        from core.utils import get_random_card_by_rarity, get_random_rarity

        spawned = 0
        for _ in range(count):
            rarity = get_random_rarity()
            card = await sync_to_async(get_random_card_by_rarity)(rarity)
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
    @app_commands.autocomplete(player_name=player_autocomplete)
    async def spawn_card(self, interaction: discord.Interaction, player_name: str):
        if not await self.check_admin(interaction):
            return

        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def find():
            return CardTemplate.objects.filter(name__icontains=player_name).first()

        card = await find()
        if not card:
            await interaction.followup.send(
                f"No card found matching **'{player_name}'**.", ephemeral=True
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
            f"Spawning **{card.name}** ({card.ovr} OVR, {card.rarity})…", ephemeral=True
        )
        message = await interaction.channel.send(file=file, embed=embed, view=view)
        view.message = message

    # ══════════════════════════════════════════════════════════
    #  /admin give — replaces legacy /give + /give_full
    # ══════════════════════════════════════════════════════════

    @admin_group.command(name="give", description="Give a specific card to a user")
    @app_commands.autocomplete(player_search=player_autocomplete)
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
            f"Gave **{card.name}** ({card.rarity}) to {user.mention}!", ephemeral=True
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
    #  /admin md
    # ══════════════════════════════════════════════════════════

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

async def setup(bot):
    await bot.add_cog(AdminCog(bot))
