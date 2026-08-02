import logging
import math
import os
import sys
import time
import traceback
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands
from django.core.management.base import BaseCommand
from dotenv import load_dotenv

from core.settings import read_settings, settings

load_dotenv()

TOKEN = os.getenv("DISCORD_BOT_TOKEN")

log = logging.getLogger("matchdex.bot")


# ── Custom CommandTree with timing guard ─────────────────────
class MatchdexTree(app_commands.CommandTree):
    """
    Subclassed tree that drops interactions arriving too late.
    Discord requires an initial response within 3 seconds; we bail
    at 2.8 to avoid wasting a response on something that will fail.
    """

    async def interaction_check(self, interaction: discord.Interaction, /):
        delta = datetime.now(tz=interaction.created_at.tzinfo) - interaction.created_at
        if delta.total_seconds() >= 2.8:
            log.warning(
                "Dropping interaction %s — arrived %.2fs late.",
                interaction.id,
                delta.total_seconds(),
            )
            return False

        bot = interaction.client
        if not bot.is_ready():
            if interaction.type != discord.InteractionType.autocomplete:
                await interaction.response.send_message(
                    "The bot is still starting up, please wait a moment…",
                    ephemeral=True,
                )
            return False

        return await bot.blacklist_check(interaction)

    async def on_app_command_completion(
        self,
        interaction: discord.Interaction,
        command: discord.app_commands.Command | discord.app_commands.ContextMenu
    ):
        if interaction.guild:
            from core.models import ServerSettings
            from django.utils import timezone
            
            # Fetch settings
            settings = await ServerSettings.objects.filter(guild_id=interaction.guild.id).afirst()
            if settings and settings.command_log_channel_id:
                log_channel = interaction.guild.get_channel(settings.command_log_channel_id)
                if log_channel:
                    command_name = getattr(command, 'qualified_name', command.name)
                    
                    # Create rich embed
                    embed = discord.Embed(
                        title="Command Executed",
                        color=discord.Color.blue(),
                        timestamp=timezone.now()
                    )
                    
                    embed.add_field(name="Command", value=f"`/{command_name}`", inline=False)
                    embed.add_field(name="User", value=f"{interaction.user} ({interaction.user.id})", inline=True)
                    embed.add_field(name="Channel", value=f"{interaction.channel.mention}", inline=True)
                    
                    # Append arguments if available
                    options_str = []
                    for name, value in getattr(interaction.namespace, '__dict__', {}).items():
                        if not name.startswith('_'):
                            options_str.append(f"**{name}**: {value}")
                    
                    if options_str:
                        embed.add_field(name="Arguments", value="\n".join(options_str), inline=False)
                        
                    embed.set_footer(text=f"User ID: {interaction.user.id}")
                    
                    try:
                        await log_channel.send(embed=embed)
                    except discord.Forbidden:
                        pass


# ── The bot ──────────────────────────────────────────────────
class MatchdexBot(commands.AutoShardedBot):
    """
    Main Matchdex bot instance.

    Design notes drawn from BallsDex's architecture:
    - Config-driven package loading (config.yml decides which cogs load)
    - In-memory blacklist cache refreshed on startup
    - Centralised application-command error handler
    - Guild-locked admin command syncing
    - Startup-time tracking for /about uptime display
    """

    def __init__(self):
        intents = discord.Intents.default()
        # These are disabled to avoid the need for Discord Verification
        intents.message_content = False
        intents.members = False

        super().__init__(
            command_prefix="!",
            intents=intents,
            tree_cls=MatchdexTree,
        )

        # Admin IDs from .env (immutable base set)
        raw = os.getenv("ADMIN_IDS", "")
        self.admin_ids: set[int] = {
            int(v.strip()) for v in raw.split(",") if v.strip().isdigit()
        }
        # Merge co-admins from config.yml
        self.admin_ids.update(settings.co_admins)

        # Caches populated in on_ready
        self.blacklisted_users: set[int] = set()
        self.blacklisted_guilds: set[int] = set()

        self.startup_time: datetime | None = None
        self.tree.error(self.on_application_command_error)

    # ── Blacklist cache ──────────────────────────────────────
    async def load_blacklist_cache(self):
        from asgiref.sync import sync_to_async

        from core.models import Blacklist

        @sync_to_async
        def fetch():
            users = set(
                Blacklist.objects.filter(type="USER").values_list(
                    "target_id", flat=True
                )
            )
            guilds = set(
                Blacklist.objects.filter(type="GUILD").values_list(
                    "target_id", flat=True
                )
            )
            return users, guilds

        self.blacklisted_users, self.blacklisted_guilds = await fetch()
        log.info(
            "Blacklist cache: %d users, %d guilds.",
            len(self.blacklisted_users),
            len(self.blacklisted_guilds),
        )

    async def blacklist_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id in self.blacklisted_users:
            if interaction.type != discord.InteractionType.autocomplete:
                await interaction.response.send_message(
                    "You are **blacklisted** from using this bot.\n"
                    f"Appeal in our support server: {settings.discord_invite}",
                    ephemeral=True,
                )
            return False

        if interaction.guild_id and interaction.guild_id in self.blacklisted_guilds:
            if interaction.type != discord.InteractionType.autocomplete:
                await interaction.response.send_message(
                    "This server is **blacklisted** from using this bot.\n"
                    f"Appeal in our support server: {settings.discord_invite}",
                    ephemeral=True,
                )
            return False
        return True

    # ── Admin check ──────────────────────────────────────────
    async def is_admin(self, user: discord.User) -> bool:
        if user.id in self.admin_ids:
            return True
        if await self.is_owner(user):
            return True
        # DB-level admin flag
        from asgiref.sync import sync_to_async

        from core.models import DiscordUser

        @sync_to_async
        def db_check():
            return DiscordUser.objects.filter(
                discord_id=user.id, is_admin=True
            ).exists()

        return await db_check()

    # ── Lifecycle ────────────────────────────────────────────
    async def setup_hook(self):
        log.info("setup_hook: preparing shards…")

    async def on_ready(self):
        # Guard against reconnect spam
        if self.startup_time is not None:
            return

        self.startup_time = datetime.now(timezone.utc)

        assert self.user
        log.info("Logged in as %s (ID: %s)", self.user, self.user.id)

        # Merge co-admins from config (in case read_settings ran after __init__)
        self.admin_ids.update(settings.co_admins)
        log.info("Admins configured: %d", len(self.admin_ids))

        # Load caches
        await self.load_blacklist_cache()

        # Load packages from config
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(show_header=True, header_style="bold magenta", title="Extension Loading State")
        table.add_column("Package", style="cyan")
        table.add_column("Status", justify="center")

        loaded = []
        for package in settings.packages:
            try:
                await self.load_extension(package)
                loaded.append(package.rsplit(".", 1)[-1])
                table.add_row(package, "[green]✅ Success[/green]")
            except Exception:
                log.error("Failed to load %s", package, exc_info=True)
                table.add_row(package, "[red]❌ Failed[/red]")

        if loaded:
            console.print(table)

        # Automatic global sync removed for extremely fast boot times! 
        # But we need to sync globals right now because admin commands became global.
        try:
            synced = await self.tree.sync()
            log.info(f"Globally synced {len(synced)} commands.")
        except Exception as e:
            log.error(f"Failed to sync global commands: {e}")

        # Sync admin commands only to admin guilds
        for guild_id in settings.admin_guild_ids:
            guild = self.get_guild(guild_id)
            if guild:
                admin_synced = await self.tree.sync(guild=guild)
                log.info(
                    "Synced %d admin commands to guild %s.",
                    len(admin_synced),
                    guild_id,
                )

        log.info("──── %s is now operational! ────", settings.bot_name)

    # ── Global application-command error handler ─────────────
    async def on_application_command_error(
        self,
        interaction: discord.Interaction,
        error: app_commands.AppCommandError,
    ):
        async def reply(content: str):
            if interaction.response.is_done():
                await interaction.followup.send(content, ephemeral=True)
            else:
                await interaction.response.send_message(content, ephemeral=True)

        # Cooldowns
        if isinstance(error, app_commands.CommandOnCooldown):
            retry_at = math.ceil(time.time() + error.retry_after)
            await reply(f"This command is on cooldown. Try again <t:{retry_at}:R>.")
            return

        # Permission checks
        if isinstance(error, app_commands.CheckFailure):
            if isinstance(error, app_commands.BotMissingPermissions):
                perms = ", ".join(error.missing_permissions)
                await reply(f"I'm missing permissions: `{perms}`.")
                return
            if isinstance(error, app_commands.MissingPermissions):
                perms = ", ".join(error.missing_permissions)
                await reply(f"You need these permissions: `{perms}`.")
                return
            # Generic check failure (blacklist, etc.) — already handled upstream
            return

        # Bad argument parsing
        if isinstance(error, app_commands.TransformerError):
            await reply(
                "One of the arguments couldn't be parsed. Double-check and try again."
            )
            log.debug("Transformer error", exc_info=error)
            return

        # Command invocation blew up
        if isinstance(error, app_commands.CommandInvokeError):
            original = error.original
            assert interaction.command

            if isinstance(original, discord.Forbidden):
                await reply("I don't have the required permissions to do that.")
                log.warning(
                    "Missing permissions in /%s",
                    interaction.command.qualified_name,
                    exc_info=original,
                )
                return

            if isinstance(original, discord.InteractionResponded):
                log.warning(
                    "Interaction already responded for /%s",
                    interaction.command.qualified_name,
                )
                return

            # Log the full traceback
            log.error(
                "Error in /%s",
                interaction.command.qualified_name,
                exc_info=original,
            )
            await reply(
                "Something went wrong running that command. "
                "If this keeps happening, contact support."
            )

            # Forward to the error-log channel if configured
            await self._log_error_to_channel(interaction, original)
            return

        # Desync
        if isinstance(
            error,
            (app_commands.CommandNotFound, app_commands.CommandSignatureMismatch),
        ):
            await reply(
                "Commands are out of sync — the bot may have just updated. "
                "Try again in a minute."
            )
            log.error(error.args[0])
            return

        # Catch-all
        await reply("An unexpected error occurred. If this persists, contact support.")
        log.error("Unhandled interaction error", exc_info=error)
        await self._log_error_to_channel(interaction, error)

    async def _log_error_to_channel(self, interaction: discord.Interaction, error):
        """Post a formatted error embed to the configured error-log channel."""
        if not settings.error_log_channel:
            return
        channel = self.get_channel(settings.error_log_channel)
        if not channel:
            return

        command_name = "unknown"
        if interaction.command:
            command_name = interaction.command.qualified_name

        tb = "".join(
            traceback.format_exception(type(error), error, error.__traceback__)
        )
        # Truncate to fit embed limits
        if len(tb) > 1800:
            tb = tb[:1800] + "\n…(truncated)"

        embed = discord.Embed(
            title=f"Error in /{command_name}",
            color=discord.Color.red(),
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(
            name="User",
            value=f"{interaction.user} ({interaction.user.id})",
            inline=True,
        )
        embed.add_field(
            name="Guild",
            value=(
                f"{interaction.guild} ({interaction.guild_id})"
                if interaction.guild
                else "DM"
            ),
            inline=True,
        )
        embed.add_field(name="Traceback", value=f"```py\n{tb}\n```", inline=False)

        try:
            await channel.send(embed=embed)
        except discord.HTTPException:
            log.warning(
                "Could not send error log to channel %s.", settings.error_log_channel
            )


# ── Django management command entry point ────────────────────
class Command(BaseCommand):
    help = "Runs the Matchdex Discord Bot"

    def handle(self, *args, **options):
        if not TOKEN:
            self.stdout.write(
                self.style.ERROR("DISCORD_BOT_TOKEN not found in .env file")
            )
            return

        # Load config before starting
        read_settings()

        from rich.logging import RichHandler
        import logging
        
        logging.basicConfig(
            level=logging.INFO,
            format="%(message)s",
            datefmt="[%X]",
            handlers=[RichHandler(rich_tracebacks=True, markup=True)]
        )

        bot = MatchdexBot()
        bot.run(TOKEN, log_handler=None)
