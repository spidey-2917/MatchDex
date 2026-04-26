import asyncio
import logging
import random

import discord
from asgiref.sync import sync_to_async
from discord.ext import commands

from core.models import CardTemplate, DiscordUser, ServerSettings, UserCard
from core.settings import settings
from core.utils import generate_card_image

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
                "Someone else already caught this card!", ephemeral=True
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

            await interaction.response.send_message(
                f"🎉 {interaction.user.mention} caught **{self.card_template.name}** "
                f"({self.card_template.rarity})! (ID: `{user_card.card_id}`)"
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
                "This card has already been caught!", ephemeral=True
            )
            return
        await interaction.response.send_modal(CatchModal(self.card_template, self))


class SpawningCog(commands.Cog, name="Spawning"):
    def __init__(self, bot):
        self.bot = bot
        self.message_counts: dict[int, int] = {}
        self.last_spawn_time: dict[int, float] = {}
        self.spawn_thresholds: dict[int, int] = {}

    def cog_unload(self):
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
        self.message_counts[guild_id] = self.message_counts.get(guild_id, 0) + 1
        count = self.message_counts[guild_id]
        import time as _time
        now = _time.time()

        # Initialize last_spawn_time on first message to prevent immediate spawn on restart
        if guild_id not in self.last_spawn_time:
            self.last_spawn_time[guild_id] = now
            return

        last = self.last_spawn_time.get(guild_id)
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
        rarity = self._weighted_rarity()
        card = await sync_to_async(self._pick_card)(rarity)
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
        msg = await channel.send(file=file, embed=embed, view=view)
        view.message = msg

    def _weighted_rarity(self) -> str:
        rarities = list(settings.rarity_weights.keys())
        weights = list(settings.rarity_weights.values())
        return random.choices(rarities, weights=weights, k=1)[0]

    @staticmethod
    def _pick_card(rarity: str):
        chosen_type = random.choices(["BASE", "ICON"], weights=[95, 5], k=1)[0]
        cards = CardTemplate.objects.filter(card_type=chosen_type, rarity=rarity)
        if not cards.exists():
            return CardTemplate.objects.filter(card_type=chosen_type).order_by("?").first()
        return cards.order_by("?").first()


async def setup(bot):
    await bot.add_cog(SpawningCog(bot))
