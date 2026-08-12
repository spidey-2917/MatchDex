import asyncio
import logging
import random
import time

import discord
from asgiref.sync import sync_to_async
from discord.ext import commands

from core.models import CardTemplate, DiscordUser, ServerSettings, UserCard
from core.settings import settings
from core.utils import generate_card_image
from core.objectives import update_objective_progress
from django.utils import timezone

log = logging.getLogger("matchdex.spawn")


class CatchModal(discord.ui.Modal, title="Catch Player"):
    player_name = discord.ui.TextInput(
        label="Who's that player?",
        placeholder="Enter player name here...",
        required=True,
        max_length=100,
    )

    def __init__(self, card_template, view_instance):
        super().__init__()
        self.card_template = card_template
        self.view_instance = view_instance

    async def on_submit(self, interaction: discord.Interaction):
        if self.view_instance.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} RIP Bro Someone else got it !!"
            )
            return

        click_time = self.view_instance.click_times.get(interaction.user.id, 0)
        elapsed_time = max(time.time() - click_time, 0.001) if click_time else 1.0

        guessed = self.player_name.value.strip().lower()
        actual = self.card_template.name.lower()

        words = max(len(guessed), 1) / 5.0
        wpm = (words / elapsed_time) * 60.0

        if wpm > 75.0:
            await interaction.response.send_message(
                f"{interaction.user.mention} Stop that Autofill lil bro you ain't tuff with that"
            )
            return

        guessed = self.player_name.value.strip().lower()
        actual = self.card_template.name.lower()

        if guessed == actual:
            self.view_instance.caught = True

            user, _ = await DiscordUser.objects.aget_or_create(
                discord_id=interaction.user.id,
                defaults={"username": interaction.user.name},
            )
            user_card = await UserCard.objects.acreate(
                owner=user, template=self.card_template
            )
            user.cards_collected += 1
            await user.asave()
            
            # Update objectives
            await update_objective_progress(user, "claim_card")
            
            # Catch Logging
            if interaction.guild:
                server_settings = await ServerSettings.objects.exclude(catch_log_channel_id__isnull=True).afirst()
                if server_settings and server_settings.catch_log_channel_id:
                    log_channel = interaction.client.get_channel(server_settings.catch_log_channel_id)
                    if log_channel:
                        log_embed = discord.Embed(
                            title="New Card Caught!",
                            description=f"**{interaction.user.name}** caught **{self.card_template.name}** ({self.card_template.rarity})",
                            color=discord.Color.green(),
                            timestamp=timezone.now()
                        )
                        try:
                            await log_channel.send(embed=log_embed)
                        except discord.Forbidden:
                            pass

            await interaction.response.send_message(
                f"{interaction.user.mention} You caught **{self.card_template.display_name}**! "
                f"(#{user_card.card_id}, {self.card_template.rarity})"
            )
            if interaction.message:
                await interaction.message.edit(view=None)
            self.view_instance.stop()
        else:
            await interaction.response.send_message(
                f"❌ Wrong name! That's not {self.player_name.value}.",
                ephemeral=True,
            )


class CatchView(discord.ui.View):
    def __init__(self, card_template):
        super().__init__(timeout=300)
        self.card_template = card_template
        self.caught = False
        self.message = None
        self.click_times = {}
        # Use the label from config
        self.catch_button.label = settings.catch_button_label

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                if not self.caught:
                    embed = self.message.embeds[0] if self.message.embeds else None
                    if embed:
                        embed.description = "🏃💨 **Too slow!** The card got away..."
                        embed.color = discord.Color.red()
                        await self.message.edit(embed=embed, view=None)
            except discord.NotFound:
                pass

    @discord.ui.button(label="Catch", style=discord.ButtonStyle.green)
    async def catch_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} RIP Bro Someone else got it !!"
            )
            return
            
        self.click_times[interaction.user.id] = time.time()
        await interaction.response.send_modal(CatchModal(self.card_template, self))


class SpawningCog(commands.Cog, name="Spawning"):
    def __init__(self, bot):
        self.bot = bot
        self.message_counts: dict[int, int] = {}
        self.last_spawn_time: dict[int, float] = {}
        self.spawn_thresholds: dict[int, int] = {}
        self.enabled_guilds: set[int] = set()

    async def cog_load(self):
        # Populate enabled_guilds cache on startup
        from core.models import ServerSettings
        async for setting in ServerSettings.objects.filter(spawn_channel_id__isnull=False):
            self.enabled_guilds.add(setting.guild_id)

    async def cog_unload(self) -> None:
        pass

    def _roll_threshold(self, guild_id: int) -> int:
        """Pick a new random threshold for this guild."""
        lo, hi = settings.spawn_chance_range
        t = random.randint(lo, hi)
        self.spawn_thresholds[guild_id] = t
        return t

    def _get_threshold(self, guild_id: int) -> int:
        if guild_id not in self.spawn_thresholds:
            return self._roll_threshold(guild_id)
        return self.spawn_thresholds[guild_id]

    # ── Per-message spawn check (the BallsDex approach) ──────
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return

        guild_id = message.guild.id
        
        # Abuse prevention: Only count messages if a spawn channel is set
        if guild_id not in self.enabled_guilds:
            return

        # Servers need at least 50 members to trigger wild spawns
        m_count = message.guild.member_count or 0
        if m_count < 50:
            return
        self.message_counts[guild_id] = self.message_counts.get(guild_id, 0) + 1
        count = self.message_counts[guild_id]
        import time as _time
        now = _time.time()

        # Initialize last_spawn_time on first message to prevent immediate spawn on restart
        if guild_id not in self.last_spawn_time:
            self.last_spawn_time[guild_id] = now
            return

        last = self.last_spawn_time[guild_id]
        elapsed = now - last
        threshold = self._get_threshold(guild_id)

        # 1. Check if we should trigger a spawn due to time limit (for inactive servers)
        time_override = elapsed >= settings.spawn_max_interval

        # 2. Check if we should trigger due to message count
        count_reached = count >= threshold

        if not (time_override or count_reached):
            return

        # 3. Check cooldown (only if it wasn't a time-based override)
        if not time_override and elapsed < settings.spawn_cooldown_seconds:
            return

        # Find the spawn channel for this guild
        setting = await ServerSettings.objects.filter(
            guild_id=guild_id, spawn_channel_id__isnull=False
        ).afirst()
        if not setting:
            return

        channel = self.bot.get_channel(setting.spawn_channel_id)
        if not channel:
            return

        # Reset counters
        self.message_counts[guild_id] = 0
        self._roll_threshold(guild_id)
        self.last_spawn_time[guild_id] = now

        await self._spawn_card(channel)

    async def _spawn_card(self, channel: discord.TextChannel):
        """Pick a random card and send it as a catchable spawn."""
        from core.utils import pick_random_card
        card = await sync_to_async(pick_random_card)("SPAWN")
        if not card:
            return

        image_buffer = await asyncio.to_thread(generate_card_image, card)
        file = discord.File(fp=image_buffer, filename=f"{card.name}.png")

        # Pick a random spawn message from config
        spawn_text = random.choice(settings.spawn_messages)

        embed = discord.Embed(
            title=spawn_text,
            description="Click the button below to catch it!",
        )
        embed.set_image(url=f"attachment://{card.name}.png")
        embed.set_footer(text=f"Rarity: {card.rarity} | Position: {card.position}")

        view = CatchView(card)
        try:
            msg = await channel.send(file=file, embed=embed, view=view)
            view.message = msg
        except discord.Forbidden as e:
            log.error(f"Failed to spawn card in guild {channel.guild.id} channel {channel.id} due to Missing Permissions: {e}")
        except discord.HTTPException as e:
            log.error(f"Failed to spawn card in guild {channel.guild.id} channel {channel.id} due to HTTP error: {e}")


async def setup(bot):
    await bot.add_cog(SpawningCog(bot))
