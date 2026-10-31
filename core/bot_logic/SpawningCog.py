import asyncio
import logging
import random
import time

import discord
from asgiref.sync import sync_to_async
from discord.ext import commands

from discord import app_commands
from core.models import (
    CardTemplate,
    DiscordUser,
    Logo,
    Manager,
    ServerSettings,
    UserCard,
    UserLogo,
    UserManager,
    UserOwnedLogo,
    UserOwnedManager,
)
from core.settings import settings
from core.utils import generate_card_image, roll_refractor
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

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return

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
            if getattr(self.view_instance, "force_refractor", None) and self.view_instance.force_refractor != "NONE":
                refractor = self.view_instance.force_refractor
            else:
                refractor = roll_refractor()
            user_card = await UserCard.objects.acreate(
                owner=user, template=self.card_template, refractor=refractor
            )
            user.cards_collected += 1
            await user.asave()

            refractor_note = ""
            if refractor != "NONE":
                ref_label = dict(UserCard.REFRACTOR_CHOICES).get(refractor, refractor)
                refractor_note = f"\n✨ **REFRACTOR PULL!** You caught a rare **{ref_label}** edition! ✨"
            
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
                            description=f"**{interaction.user.name}** caught **{self.card_template.name}** ({self.card_template.rarity}){refractor_note}",
                            color=discord.Color.green(),
                            timestamp=timezone.now()
                        )
                        try:
                            await log_channel.send(embed=log_embed)
                        except discord.Forbidden:
                            pass

            await interaction.response.send_message(
                f"{interaction.user.mention} You caught **{self.card_template.display_name}**! "
                f"(#{user_card.card_id}, {self.card_template.rarity}){refractor_note}"
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
    def __init__(self, card_template, force_refractor=None):
        super().__init__(timeout=300)
        self.card_template = card_template

        self.force_refractor = force_refractor

        self.caught = False
        self.message = None
        self.click_times = {}
        # Use the label from config
        self.catch_button.label = settings.catch_button_label

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

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
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return

        if self.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} RIP Bro Someone else got it !!"
            )
            return
            
        self.click_times[interaction.user.id] = time.time()
        await interaction.response.send_modal(CatchModal(self.card_template, self))


class SignManagerModal(discord.ui.Modal, title="Sign Manager"):
    manager_name = discord.ui.TextInput(
        label="Who's that manager?",
        placeholder="Enter manager name...",
        required=True,
        max_length=100,
    )

    def __init__(self, manager, view_instance):
        super().__init__()
        self.manager = manager
        self.view_instance = view_instance

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return

        if self.view_instance.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} Too late! Someone else already signed this manager."
            )
            return

        guessed = self.manager_name.value.strip().lower()
        actual = self.manager.name.strip().lower()

        # Allow exact name or last name match
        is_match = (guessed == actual) or (guessed in actual.split())

        if is_match:
            self.view_instance.caught = True

            user, _ = await DiscordUser.objects.aget_or_create(
                discord_id=interaction.user.id,
                defaults={"username": interaction.user.name},
            )
            owned, created = await UserOwnedManager.objects.aget_or_create(
                user=user, manager=self.manager
            )
            has_eq = await UserManager.objects.filter(owner=user).aexists()
            eq_text = ""
            if not has_eq:
                await UserManager.objects.acreate(owner=user, manager=self.manager)
                eq_text = " (Auto-equipped as your active manager!)"

            await update_objective_progress(user, "claim_card")

            await interaction.response.send_message(
                f"👔 {interaction.user.mention} successfully signed **{self.manager.name}**!{eq_text}"
            )
            if interaction.message:
                await interaction.message.edit(view=None)
            self.view_instance.stop()
        else:
            await interaction.response.send_message(
                f"❌ Wrong manager name! That's not {self.manager_name.value}.",
                ephemeral=True,
            )


class SignManagerView(discord.ui.View):
    def __init__(self, manager):
        super().__init__(timeout=300)
        self.manager = manager
        self.caught = False
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message and not self.caught:
            try:
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.description = "🏃💨 **Too slow!** The manager signed with another club..."
                    embed.color = discord.Color.red()
                    await self.message.edit(embed=embed, view=None)
            except discord.NotFound:
                pass

    @discord.ui.button(label="Sign Manager", style=discord.ButtonStyle.primary, emoji="👔")
    async def sign_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return
        if self.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} Someone else already signed this manager!", ephemeral=True
            )
            return
        await interaction.response.send_modal(SignManagerModal(self.manager, self))


class ClaimLogoModal(discord.ui.Modal, title="Claim Club Crest"):
    club_name = discord.ui.TextInput(
        label="Which club's crest is this?",
        placeholder="Enter club / crest name...",
        required=True,
        max_length=100,
    )

    def __init__(self, logo, view_instance):
        super().__init__()
        self.logo = logo
        self.view_instance = view_instance

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return

        if self.view_instance.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} Too late! Someone else already claimed this crest."
            )
            return

        guessed = self.club_name.value.strip().lower()
        actual_name = self.logo.name.strip().lower()
        actual_club = (self.logo.club or "").strip().lower()

        is_match = (guessed == actual_name) or (actual_club and guessed == actual_club) or (actual_club and guessed in actual_club)

        if is_match:
            self.view_instance.caught = True

            user, _ = await DiscordUser.objects.aget_or_create(
                discord_id=interaction.user.id,
                defaults={"username": interaction.user.name},
            )
            owned, created = await UserOwnedLogo.objects.aget_or_create(
                user=user, logo=self.logo
            )
            has_eq = await UserLogo.objects.filter(owner=user).aexists()
            eq_text = ""
            if not has_eq:
                await UserLogo.objects.acreate(owner=user, logo=self.logo)
                eq_text = " (Auto-equipped as your active crest!)"

            await update_objective_progress(user, "claim_card")

            await interaction.response.send_message(
                f"🛡️ {interaction.user.mention} successfully claimed the **{self.logo.name}** crest!{eq_text}"
            )
            if interaction.message:
                await interaction.message.edit(view=None)
            self.view_instance.stop()
        else:
            await interaction.response.send_message(
                f"❌ Wrong club name! That's not {self.club_name.value}.",
                ephemeral=True,
            )


class ClaimLogoView(discord.ui.View):
    def __init__(self, logo):
        super().__init__(timeout=300)
        self.logo = logo
        self.caught = False
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message and not self.caught:
            try:
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.description = "🏃💨 **Too slow!** The club crest was not claimed."
                    embed.color = discord.Color.red()
                    await self.message.edit(embed=embed, view=None)
            except discord.NotFound:
                pass

    @discord.ui.button(label="Claim Crest", style=discord.ButtonStyle.secondary, emoji="🛡️")
    async def claim_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return
        if self.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} Someone else already claimed this crest!", ephemeral=True
            )
            return
        await interaction.response.send_modal(ClaimLogoModal(self.logo, self))




class DroppedCatchModal(discord.ui.Modal, title="Catch Dropped Card"):
    player_name = discord.ui.TextInput(
        label="Who's that player?",
        placeholder="Enter player name here...",
        required=True,
        max_length=100,
    )

    def __init__(self, user_card, view_instance):
        super().__init__()
        self.user_card = user_card
        self.view_instance = view_instance

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_submit(self, interaction: discord.Interaction):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return

        if self.view_instance.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} RIP Bro Someone else got it !!", ephemeral=True
            )
            return

        # Dropper cannot catch their own card
        if interaction.user.id == self.view_instance.dropper.discord_id:
            await interaction.response.send_message(
                f"{interaction.user.mention} ❌ You dropped this card! You cannot catch your own drop.",
                ephemeral=True
            )
            return

        guessed = self.player_name.value.strip().lower()
        actual = self.user_card.template.name.lower()

        if guessed == actual:
            self.view_instance.caught = True

            catcher, _ = await DiscordUser.objects.aget_or_create(
                discord_id=interaction.user.id,
                defaults={"username": interaction.user.name},
            )

            # Transfer ownership of the exact card (preserving refractor and upgrade tier without rerolling!)
            @sync_to_async
            def transfer_card():
                from core.models import UserCard, DiscordUser
                uc = UserCard.objects.select_related("template", "owner").get(id=self.user_card.id)
                old_owner = uc.owner
                uc.owner = catcher
                uc.save()

                catcher.cards_collected += 1
                catcher.save()

                if old_owner:
                    old_owner.cards_collected = max(0, old_owner.cards_collected - 1)
                    old_owner.save()
                return uc

            updated_card = await transfer_card()

            if hasattr(self.view_instance.cog, "active_dropped_cards"):
                self.view_instance.cog.active_dropped_cards.discard(self.user_card.id)

            await update_objective_progress(catcher, "claim_card")

            dropper_name = (
                getattr(self.view_instance, "dropper_name", None)
                or getattr(self.view_instance.dropper, "username", None)
                or f"User #{getattr(self.view_instance.dropper, 'discord_id', 'Unknown')}"
            )

            ref_note = ""
            if updated_card.refractor and updated_card.refractor != "NONE":
                ref_label = dict(UserCard.REFRACTOR_CHOICES).get(updated_card.refractor, updated_card.refractor)
                ref_note = f"\n✨ Transferred with its rare **{ref_label}** refractor from {dropper_name}! ✨"

            tier_note = ""
            if updated_card.upgrade_tier and updated_card.upgrade_tier != "NONE":
                tier_note = f" [{updated_card.upgrade_tier}]"

            await interaction.response.send_message(
                f"🎉 {interaction.user.mention} caught **{updated_card.template.display_name}**{tier_note} "
                f"(#{updated_card.card_id}, {updated_card.template.rarity}) dropped by **{dropper_name}**!{ref_note}"
            )
            if interaction.message:
                embed = interaction.message.embeds[0] if interaction.message.embeds else None
                if embed:
                    embed.description = f"✅ Successfully caught and claimed by **{interaction.user.display_name}**!"
                    embed.color = discord.Color.green()
                await interaction.message.edit(embed=embed, view=None)
            self.view_instance.stop()
        else:
            await interaction.response.send_message(
                f"❌ Wrong name! That's not {self.player_name.value}.",
                ephemeral=True,
            )


class DroppedCatchView(discord.ui.View):
    def __init__(self, user_card, dropper, cog, dropper_name=None):
        super().__init__(timeout=300)
        self.user_card = user_card
        self.dropper = dropper
        self.dropper_name = dropper_name or getattr(dropper, "username", None) or str(getattr(dropper, "discord_id", "Unknown"))
        self.cog = cog
        self.caught = False
        self.message = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            return await bot.blacklist_check(interaction)
        return True

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if hasattr(self.cog, "active_dropped_cards"):
            self.cog.active_dropped_cards.discard(self.user_card.id)

        if self.message and not self.caught:
            try:
                embed = self.message.embeds[0] if self.message.embeds else None
                if embed:
                    embed.description = f"⏰ Nobody caught the card in time! **{self.user_card.template.display_name}** was safely returned to <@{self.dropper.discord_id}>'s inventory."
                    embed.color = discord.Color.red()
                await self.message.edit(embed=embed, view=None)
            except discord.NotFound:
                pass

    @discord.ui.button(label="Catch Drop", style=discord.ButtonStyle.green, emoji="📦")
    async def catch_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        bot = interaction.client
        if hasattr(bot, "blacklist_check"):
            if not await bot.blacklist_check(interaction):
                return
        if self.caught:
            await interaction.response.send_message(
                f"{interaction.user.mention} Someone else already caught this card!", ephemeral=True
            )
            return

        if interaction.user.id == self.dropper.discord_id:
            await interaction.response.send_message(
                "❌ You dropped this card! You cannot catch your own drop.", ephemeral=True
            )
            return

        await interaction.response.send_modal(DroppedCatchModal(self.user_card, self))

class SpawningCog(commands.Cog, name="Spawning"):
    def __init__(self, bot):
        self.bot = bot
        self.message_counts: dict[int, int] = {}
        self.last_spawn_time: dict[int, float] = {}
        self.spawn_thresholds: dict[int, int] = {}
        self.enabled_guilds: set[int] = set()
        self.active_dropped_cards: set[int] = set()

    async def cog_load(self):
        # Populate enabled_guilds cache on startup, excluding blacklisted guilds
        from core.models import ServerSettings
        async for setting in ServerSettings.objects.filter(spawn_channel_id__isnull=False):
            if not await self.bot.is_guild_blacklisted(setting.guild_id):
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
        
        # Blacklisted servers must never spawn cards
        if await self.bot.is_guild_blacklisted(guild_id):
            return

        # Blacklisted users must not trigger spawn thresholds
        if await self.bot.is_user_blacklisted(message.author.id):
            return

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

        await self._spawn_random_entity(channel)

    async def _spawn_manager(self, channel: discord.TextChannel, manager=None, rarity=None) -> bool:
        """Spawn a catchable Manager with rarity weighting. Returns True if spawned successfully."""
        if not channel or not getattr(channel, "guild", None):
            return False
        if await self.bot.is_guild_blacklisted(channel.guild.id):
            return False

        if not manager:
            @sync_to_async
            def pick_manager():
                qs = Manager.objects.all()
                if not qs.exists():
                    return None
                if rarity:
                    match_qs = qs.filter(rarity__iexact=rarity)
                    if match_qs.exists():
                        return match_qs.order_by("?").first()

                # Weighted roll by manager rarity
                available_rarities = list(qs.values_list("rarity", flat=True).distinct())
                weights_map = {"Rare": 55, "Epic": 30, "Legendary": 12, "World-Class": 3}
                weights = [weights_map.get(r, 10) for r in available_rarities]
                chosen_rarity = random.choices(available_rarities, weights=weights, k=1)[0]
                return qs.filter(rarity=chosen_rarity).order_by("?").first()

            manager = await pick_manager()
        if not manager:
            return False

        embed = discord.Embed(
            title="👔 A World-Class Manager has appeared!",
            description="A renowned tactician has arrived! Click below to sign them to your club.",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Manager", value=f"**{manager.name}**", inline=True)
        embed.add_field(name="Rarity", value=manager.rarity, inline=True)
        pref_f = manager.preferred_formation.upper() if manager.preferred_formation != "ALL" else "Any Formation"
        embed.add_field(
            name="Formation Synergy",
            value=f"**{pref_f}** (+{manager.formation_boost_pct}% {manager.formation_boost_type})",
            inline=True
        )
        pref_t = manager.get_preferred_tactic_display()
        embed.add_field(
            name="Tactic Synergy",
            value=f"**{pref_t}** (+{manager.tactic_boost_pct}% {manager.tactic_boost_type})",
            inline=True
        )

        file = None
        if manager.image:
            try:
                import os
                if os.path.exists(manager.image.path):
                    file = discord.File(manager.image.path, filename=f"manager_{manager.id}.png")
                    embed.set_thumbnail(url=f"attachment://manager_{manager.id}.png")
            except Exception as e:
                log.warning(f"Failed to load manager image: {e}")

        if not file:
            try:
                from core.utils import generate_manager_image
                image_buffer = await asyncio.to_thread(generate_manager_image, manager)
                file = discord.File(fp=image_buffer, filename=f"manager_{manager.id}.png")
                embed.set_thumbnail(url=f"attachment://manager_{manager.id}.png")
            except Exception:
                pass

        view = SignManagerView(manager)
        try:
            if file:
                msg = await channel.send(file=file, embed=embed, view=view)
            else:
                msg = await channel.send(embed=embed, view=view)
            view.message = msg
            return True
        except Exception as e:
            log.error(f"Failed to send manager spawn message: {e}")
            return False

    async def _spawn_logo(self, channel: discord.TextChannel, logo=None) -> bool:
        """Spawn a catchable Club Crest / Logo. Returns True if spawned successfully."""
        if not channel or not getattr(channel, "guild", None):
            return False
        if await self.bot.is_guild_blacklisted(channel.guild.id):
            return False

        if not logo:
            logo = await sync_to_async(lambda: Logo.objects.order_by("?").first())()
        if not logo:
            return False

        embed = discord.Embed(
            title="🛡️ A Legendary Club Crest has appeared!",
            description="A historic club crest is up for grabs! Click below to claim it for your club.",
            color=discord.Color.purple(),
        )
        club_name = logo.club or logo.name
        embed.add_field(name="Crest / Club", value=f"**{club_name}**", inline=True)
        embed.add_field(name="Rarity", value=logo.rarity, inline=True)
        embed.add_field(
            name="Synergy Boost",
            value=f"+{logo.boost_val}% {logo.boost_type} (Tactic: {logo.get_preferred_tactic_display()})",
            inline=False
        )

        file = None
        if logo.image:
            try:
                import os
                if os.path.exists(logo.image.path):
                    file = discord.File(logo.image.path, filename=f"logo_{logo.id}.png")
                    embed.set_thumbnail(url=f"attachment://logo_{logo.id}.png")
            except Exception as e:
                log.warning(f"Failed to load logo image: {e}")

        view = ClaimLogoView(logo)
        try:
            if file:
                msg = await channel.send(file=file, embed=embed, view=view)
            else:
                msg = await channel.send(embed=embed, view=view)
            view.message = msg
            return True
        except Exception as e:
            log.error(f"Failed to send logo spawn message: {e}")
            return False

    async def _spawn_random_entity(self, channel: discord.TextChannel):
        """
        Spawns a random entity:
        - 0.75% Manager
        - 0.75% Logo
        - 98.5% Card
        """
        roll = random.randint(1, 1000)
        if roll <= 7:
            if await self._spawn_manager(channel):
                return
        elif roll <= 15:
            if await self._spawn_logo(channel):
                return

        await self._spawn_card(channel)

    async def _spawn_card(self, channel: discord.TextChannel):
        """Pick a random card and send it as a catchable spawn."""
        if not channel or not getattr(channel, "guild", None):
            return

        # Automatic block: blacklisted servers card spawning should automatically get blocked no matter what
        if await self.bot.is_guild_blacklisted(channel.guild.id):
            log.info("Blocked card spawn in blacklisted guild %s (channel %s)", channel.guild.id, channel.id)
            return

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
            if e.code in (50001, 50013):
                log.warning(f"Skipping spawn in guild {channel.guild.id} channel {channel.id} due to missing permissions ({e.code}).")
            else:
                log.error(f"Failed to spawn card in guild {channel.guild.id} channel {channel.id} due to Missing Permissions: {e}")
        except discord.HTTPException as e:
            log.error(f"Failed to spawn card in guild {channel.guild.id} channel {channel.id} due to HTTP error: {e}")

    async def user_owned_cards_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        from core.models import DiscordUser, UserCard
        user = await DiscordUser.objects.filter(discord_id=interaction.user.id).afirst()
        if not user:
            return []

        @sync_to_async
        def get_user_cards():
            qs = UserCard.objects.filter(owner=user).select_related("template")
            if current:
                qs = qs.filter(template__name__icontains=current)
            return list(qs[:25])

        cards = await get_user_cards()
        choices = []
        for c in cards:
            ref_txt = f" ✨[{c.refractor}]" if c.refractor != "NONE" else ""
            tier_txt = f" [{c.upgrade_tier}]" if c.upgrade_tier != "NONE" else ""
            label = f"{c.template.display_name} ({c.template.ovr} OVR){tier_txt}{ref_txt} #{c.card_id}"
            choices.append(app_commands.Choice(name=label[:100], value=str(c.card_id)))
        return choices

    @app_commands.command(
        name="drop",
        description="Drop a card from your inventory into the channel for someone else to catch (1h cooldown)"
    )
    @app_commands.describe(card="Choose a card from your inventory to drop (optional: drops a random card if empty)")
    @app_commands.autocomplete(card=user_owned_cards_autocomplete)
    async def drop_card(self, interaction: discord.Interaction, card: str | None = None):
        if not interaction.channel or not isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.response.send_message("This command can only be used in a text channel.", ephemeral=True)
            return

        if interaction.guild_id and await self.bot.is_guild_blacklisted(interaction.guild_id):
            await interaction.response.send_message("This server is blacklisted from using this bot.", ephemeral=True)
            return

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )
        if not user.username or user.username != interaction.user.name:
            user.username = interaction.user.name
            await user.asave(update_fields=["username"])

        now = timezone.now()
        if user.last_card_drop:
            elapsed = (now - user.last_card_drop).total_seconds()
            if elapsed < 3600:
                remaining = int(3600 - elapsed)
                target_ts = int(now.timestamp() + remaining)
                await interaction.response.send_message(
                    f"⏳ You are on cooldown! You can drop another card <t:{target_ts}:R>.",
                    ephemeral=True
                )
                return

        # Fetch the card to drop from user's inventory
        from core.models import Lineup, UserCard

        @sync_to_async
        def find_drop_card():
            in_lineup_ids = set()
            for lu in Lineup.objects.filter(owner=user):
                for slot in ["gk", "df1", "df2", "df3", "df4", "df5", "md1", "md2", "md3", "md4", "md5", "at1", "at2", "at3"]:
                    cid = getattr(lu, f"{slot}_id", None)
                    if cid:
                        in_lineup_ids.add(cid)

            user_cards_qs = UserCard.objects.filter(owner=user).select_related("template")

            if card:
                selected = user_cards_qs.filter(card_id=card).first()
                if not selected:
                    selected = user_cards_qs.filter(template__name__icontains=card).first()
                if not selected:
                    return None, "NOT_OWNED"
                if selected.id in in_lineup_ids:
                    return None, "IN_LINEUP"
                if selected.id in self.active_dropped_cards:
                    return None, "ALREADY_DROPPED"
                return selected, "OK"
            else:
                avail = list(user_cards_qs.exclude(id__in=in_lineup_ids).exclude(id__in=self.active_dropped_cards))
                if not avail:
                    return None, "NO_CARDS_AVAILABLE"
                import random
                return random.choice(avail), "OK"

        dropped_card, status = await find_drop_card()

        if status == "NOT_OWNED":
            await interaction.response.send_message(
                f"❌ You do not own the card `{card}` in your inventory!", ephemeral=True
            )
            return
        elif status == "IN_LINEUP":
            await interaction.response.send_message(
                "❌ This card is currently in your squad lineup! Remove it with `/team_remove` first before dropping.",
                ephemeral=True
            )
            return
        elif status == "ALREADY_DROPPED":
            await interaction.response.send_message(
                "❌ This card has already been dropped into a channel!", ephemeral=True
            )
            return
        elif status == "NO_CARDS_AVAILABLE":
            await interaction.response.send_message(
                "❌ You have no cards available to drop! (All your cards are in your squad lineup or you have no cards).",
                ephemeral=True
            )
            return

        # Mark as dropped & start cooldown
        self.active_dropped_cards.add(dropped_card.id)
        user.last_card_drop = now
        await user.asave()

        await interaction.response.defer()

        # Render image preserving refractor (NO new random reroll!)
        image_buffer = await asyncio.to_thread(
            generate_card_image, dropped_card.template, dropped_card.refractor
        )
        file = discord.File(fp=image_buffer, filename=f"{dropped_card.template.name}.png")

        ref_desc = ""
        if dropped_card.refractor and dropped_card.refractor != "NONE":
            ref_label = dict(UserCard.REFRACTOR_CHOICES).get(dropped_card.refractor, dropped_card.refractor)
            ref_desc = f"\n✨ **Includes {ref_label} Refractor!** ✨"

        tier_desc = f" [{dropped_card.upgrade_tier}]" if dropped_card.upgrade_tier != "NONE" else ""

        embed = discord.Embed(
            title=f"📦 {interaction.user.display_name} dropped a card!",
            description=f"A **{dropped_card.template.display_name}**{tier_desc} was dropped into the channel! Click below to catch it and claim it!{ref_desc}",
            color=discord.Color.gold(),
        )
        embed.set_image(url=f"attachment://{dropped_card.template.name}.png")
        embed.set_footer(
            text=f"Dropped by {interaction.user.name} | Rarity: {dropped_card.template.rarity} | Position: {dropped_card.template.position} | OVR: {dropped_card.template.ovr}"
        )

        dropper_display = interaction.user.display_name or interaction.user.name
        view = DroppedCatchView(dropped_card, user, self, dropper_name=dropper_display)
        msg = await interaction.followup.send(file=file, embed=embed, view=view)
        view.message = msg


async def setup(bot):
    await bot.add_cog(SpawningCog(bot))
