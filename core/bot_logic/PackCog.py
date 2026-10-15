import io
from datetime import datetime, timedelta

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands, tasks
from django.utils import timezone

from core.models import CardTemplate, DiscordUser, UserCard, ServerSettings
from core.utils import generate_card_image, get_random_card_by_rarity, get_random_rarity
from core.objectives import update_objective_progress


async def check_and_grant_weekly_packs(user: DiscordUser) -> list[str]:
    """
    Checks all packs configured with is_weekly_grant=True (or code='event' as fallback).
    If 7 days have passed since user's last_weekly_grant (or if never granted),
    credits 1 pack to user's wallet (UserPack.stash_count) and updates last_weekly_grant.
    Returns a list of pack names that were granted.
    """
    from core.models import Pack, UserPack

    now = timezone.now()
    granted_names = []

    weekly_packs = []
    async for p in Pack.objects.filter(is_weekly_grant=True):
        weekly_packs.append(p)

    if not weekly_packs:
        event_pack = await Pack.objects.filter(code="event").afirst()
        if event_pack:
            weekly_packs = [event_pack]

    for pack in weekly_packs:
        user_pack, _ = await UserPack.objects.aget_or_create(
            user=user, pack=pack
        )
        should_grant = False
        if user_pack.last_weekly_grant is None:
            # Respect previous last_pack_event timestamp if user opened one previously
            if user.last_pack_event and now < user.last_pack_event + timedelta(days=7):
                user_pack.last_weekly_grant = user.last_pack_event
                await user_pack.asave()
                should_grant = False
            else:
                should_grant = True
        elif now >= user_pack.last_weekly_grant + timedelta(days=7):
            should_grant = True

        if should_grant:
            user_pack.stash_count += 1
            user_pack.last_weekly_grant = now
            await user_pack.asave()
            granted_names.append(pack.name)

    return granted_names


class PackCog(commands.Cog, name="Packs"):
    def __init__(self, bot):
        self.bot = bot
        self.weekly_pack_distribution.start()

    def cog_unload(self):
        self.weekly_pack_distribution.cancel()

    @tasks.loop(hours=6)
    async def weekly_pack_distribution(self):
        """Periodically grant weekly packs to all users in the background."""
        try:
            from core.models import DiscordUser
            async for user in DiscordUser.objects.all():
                await check_and_grant_weekly_packs(user)
        except Exception:
            pass

    @weekly_pack_distribution.before_loop
    async def before_weekly_pack_distribution(self):
        await self.bot.wait_until_ready()

    async def open_pack(
        self,
        interaction: discord.Interaction,
        pack_type,
        card_filter_type=None,
        cooldown_days=None,
        event_name_filter=None,
        min_ovr_filter=None,
        max_ovr_filter=None,
    ):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Stash check
        from core.models import Pack, UserPack
        from django.db import models
        pack_obj = await Pack.objects.filter(
            models.Q(code__iexact=pack_type) | models.Q(name__iexact=pack_type)
        ).afirst()
        used_stash = False
        user_pack = None
        
        if pack_obj:
            user_pack = await UserPack.objects.filter(user=user, pack=pack_obj).afirst()
            if user_pack and user_pack.stash_count > 0:
                used_stash = True

        # Cooldown check: stashed wallet packs bypass cooldown completely
        now = timezone.now()
        last_redeem_attr = f"last_pack_{pack_type}"
        last_redeem = getattr(user, last_redeem_attr, None)

        if last_redeem is None and user_pack and user_pack.last_opened_at:
            last_redeem = user_pack.last_opened_at

        if not used_stash and cooldown_days and last_redeem:
            if now < last_redeem + timedelta(days=cooldown_days):
                remaining = (last_redeem + timedelta(days=cooldown_days)) - now
                total_hours = int(remaining.total_seconds()) // 3600
                days, hours = divmod(total_hours, 24)
                minutes = (int(remaining.total_seconds()) % 3600) // 60
                
                pack_label = pack_obj.name if pack_obj else pack_type
                if days > 0:
                    msg = f"You need to wait **{days}d {hours}h {minutes}m** before opening another {pack_label} pack!"
                else:
                    msg = f"You need to wait **{hours}h {minutes}m** before opening another {pack_label} pack!"
                    
                if interaction.response.is_done():
                    await interaction.followup.send(msg, ephemeral=True)
                else:
                    await interaction.response.send_message(msg, ephemeral=True)
                return

        if not interaction.response.is_done():
            await interaction.response.defer()

        # Pick random card based on configuration
        from core.utils import pick_random_card
        category = "PACK_PREMIUM" if pack_type == "premium" else "PACK"
        
        # Pull all rate and filter configurations dynamically from the panel-managed pack_obj
        if pack_obj:
            if pack_obj.rate_config_category:
                category = pack_obj.rate_config_category
            if card_filter_type is None and pack_obj.card_type_filter != "ANY":
                card_filter_type = pack_obj.card_type_filter
            if event_name_filter is None and pack_obj.event_name_filter:
                event_name_filter = pack_obj.event_name_filter
            if min_ovr_filter is None and pack_obj.min_ovr_filter is not None:
                min_ovr_filter = pack_obj.min_ovr_filter
            if max_ovr_filter is None and pack_obj.max_ovr_filter is not None:
                max_ovr_filter = pack_obj.max_ovr_filter

        card = await sync_to_async(pick_random_card)(
            category,
            card_type_filter=card_filter_type,
            event_name_filter=event_name_filter,
            min_ovr_filter=min_ovr_filter,
            max_ovr_filter=max_ovr_filter,
        )

        if not card:
            await interaction.followup.send(
                "No cards available in this pack category yet!", ephemeral=True
            )
            return

        # Save record
        await UserCard.objects.acreate(owner=user, template=card)
        if used_stash:
            user_pack.stash_count -= 1
            user_pack.last_opened_at = now
            await user_pack.asave()
            
        if hasattr(user, last_redeem_attr):
            setattr(user, last_redeem_attr, now)
        elif user_pack:
            user_pack.last_opened_at = now
            await user_pack.asave()
            
        user.cards_collected += 1
        user.total_packs_opened += 1
        await user.asave()

        # Update objectives
        await update_objective_progress(user, "open_pack")
        await update_objective_progress(user, "claim_card")

        # Catch Logging
        if interaction.guild:
            settings = await ServerSettings.objects.exclude(catch_log_channel_id__isnull=True).afirst()
            if settings and settings.catch_log_channel_id:
                log_channel = interaction.client.get_channel(settings.catch_log_channel_id)
                if log_channel:
                    log_embed = discord.Embed(
                        title="New Card Caught!",
                        description=f"**{interaction.user.name}** caught **{card.name}** ({card.rarity})",
                        color=discord.Color.green(),
                        timestamp=timezone.now()
                    )
                    try:
                        await log_channel.send(embed=log_embed)
                    except discord.Forbidden:
                        pass


        # ── Referral Completion Logic ──
        if user.total_packs_opened == 25:
            from core.models import Referral
            pending_ref = await Referral.objects.filter(invited_user=user, status="PENDING").afirst()
            if pending_ref:
                pending_ref.status = "COMPLETED"
                pending_ref.completed_at = timezone.now()
                await pending_ref.asave()

                # Grant milestone rewards to inviter
                inviter = pending_ref.inviter
                completed_count = await Referral.objects.filter(inviter=inviter, status="COMPLETED").acount()
                
                reward_msg = ""
                
                if completed_count == 1:
                    reward_msg = "a **Rare Card**"
                elif completed_count == 3:
                    reward_msg = "a **Guaranteed Special Card**"
                elif completed_count == 5:
                    reward_msg = "an **Event Pack**"
                elif completed_count == 10:
                    reward_msg = "an **Exclusive Card**"
                elif completed_count == 25:
                    reward_msg = "a **Limited Icon**"

                if reward_msg:
                    try:
                        inviter_discord = await self.bot.fetch_user(inviter.discord_id)
                        await inviter_discord.send(
                            f"🎉 **Referral Milestone Reached!**\n"
                            f"Your invitee **{user.username}** has completed the 25-pack requirement.\n"
                            f"You have reached **{completed_count} completed invites** and are eligible for {reward_msg}!\n\n"
                            f"**Please open a ticket in our support server to claim your reward.**"
                        )
                    except Exception:
                        pass


        # Generate image
        import asyncio

        image_buffer = await asyncio.to_thread(generate_card_image, card)
        file = discord.File(fp=image_buffer, filename=f"pack_{card.name}.png")

        pack_title = pack_obj.name if pack_obj else f"{pack_type.capitalize()} Pack"
        embed = discord.Embed(
            title=f"New {pack_title} Opened!",
            description=f"You got **{card.name}**!",
        )
        embed.set_image(url=f"attachment://pack_{card.name}.png")
        embed.set_footer(text=f"OVR: {card.ovr} | Rarity: {card.rarity}")

        await interaction.followup.send(file=file, embed=embed)

    @app_commands.command(
        name="pack_daily", description="Pack a daily base player card"
    )
    async def pack_daily(self, interaction: discord.Interaction):
        await self.open_pack(
            interaction, "daily", card_filter_type="BASE", cooldown_days=1
        )

    @app_commands.command(name="pack_weekly", description="Pack a weekly icon card")
    async def pack_weekly(self, interaction: discord.Interaction):
        await self.open_pack(
            interaction, "weekly", card_filter_type="ICON", cooldown_days=7
        )

    @app_commands.command(
        name="pack_premium",
        description="Pack a random icon or event card (Every 2 days)",
    )
    async def pack_premium(self, interaction: discord.Interaction):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Check if user has premium via DB flag or a premium Discord role
        has_premium_role = False
        if not user.is_premium and isinstance(interaction.user, discord.Member):
            from core.models import PremiumRole
            premium_role_ids = set()
            async for pr in PremiumRole.objects.all():
                premium_role_ids.add(pr.role_id)
            has_premium_role = any(
                role.id in premium_role_ids for role in interaction.user.roles
            )

        if not user.is_premium and not has_premium_role:
            await interaction.response.send_message(
                "This pack is only for Premium members!", ephemeral=True
            )
            return
        await self.open_pack(
            interaction, "premium", card_filter_type=["ICON", "EVENT"], cooldown_days=2
        )

    @app_commands.command(name="promo", description="Enter a promo code for a reward")
    async def promo(self, interaction: discord.Interaction, code: str):
        from core.models import PromoCode, PromoCodeRedemption

        promo = await PromoCode.objects.select_related("reward_card").filter(code=code).afirst()
        if not promo:
            await interaction.response.send_message(
                "Invalid promo code!", ephemeral=True
            )
            return

        if promo.expires_at and promo.expires_at < timezone.now():
            await interaction.response.send_message(
                "This promo code has expired!", ephemeral=True
            )
            return

        if promo.uses >= promo.max_uses:
            await interaction.response.send_message(
                "This promo code has reached its maximum uses!", ephemeral=True
            )
            return

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Check if this user already redeemed this code
        already_used = await PromoCodeRedemption.objects.filter(
            user=user, promo_code=promo
        ).aexists()
        if already_used:
            await interaction.response.send_message(
                "You have already redeemed this promo code!", ephemeral=True
            )
            return

        # Reward logic
        if promo.reward_type == "POINTS":
            user.points += promo.reward_points
            await user.asave()
            await PromoCodeRedemption.objects.acreate(user=user, promo_code=promo)
            promo.uses += 1
            await promo.asave()
            await interaction.response.send_message(
                f"Promo code redeemed! You received **{promo.reward_points} points**.",
                ephemeral=True,
            )
        elif promo.reward_type == "CARD":
            await interaction.response.defer()
            card_template = promo.reward_card
            if not card_template:
                await interaction.followup.send("This promo code has no card assigned!", ephemeral=True)
                return
            await UserCard.objects.acreate(owner=user, template=card_template)
            await PromoCodeRedemption.objects.acreate(user=user, promo_code=promo)
            promo.uses += 1
            await promo.asave()

            import asyncio
            image_buffer = await asyncio.to_thread(generate_card_image, card_template)
            file = discord.File(fp=image_buffer, filename=f"promo_{card_template.name}.png")
            embed = discord.Embed(
                title="Promo Code Redeemed!",
                description=f"You received a specific card: **{card_template.name}**!",
                color=discord.Color.green(),
            )
            embed.set_image(url=f"attachment://promo_{card_template.name}.png")
            embed.set_footer(text=f"OVR: {card_template.ovr} | Rarity: {card_template.rarity}")
            await interaction.followup.send(file=file, embed=embed)

        elif promo.reward_type.startswith("PACK_"):
            await interaction.response.defer()
            # Parse the pack category from reward_type (e.g. PACK_DAILY → daily)
            pack_category = promo.reward_type[5:].lower()

            # Pick random card based on configuration
            from core.utils import pick_random_card
            from core.models import Pack
            
            pack_obj = await Pack.objects.filter(code=pack_category).afirst()
            category = pack_obj.rate_config_category if pack_obj and pack_obj.rate_config_category else "PACK"
            type_filter = pack_obj.card_type_filter if pack_obj and pack_obj.card_type_filter != "ANY" else None
            event_filter = pack_obj.event_name_filter if pack_obj else None
            min_ovr = pack_obj.min_ovr_filter if pack_obj else None
            max_ovr = pack_obj.max_ovr_filter if pack_obj else None

            if not pack_obj:
                if pack_category == "daily":
                    type_filter = "BASE"
                elif pack_category == "weekly":
                    type_filter = "ICON"
                elif pack_category == "event":
                    type_filter = "EVENT"
                elif pack_category == "premium":
                    type_filter = ["ICON", "EVENT"]
                    category = "PACK_PREMIUM"

            card = await sync_to_async(pick_random_card)(
                category,
                card_type_filter=type_filter,
                event_name_filter=event_filter,
                min_ovr_filter=min_ovr,
                max_ovr_filter=max_ovr,
            )
            if not card:
                await interaction.followup.send("No cards available for this promo!", ephemeral=True)
                return

            await UserCard.objects.acreate(owner=user, template=card)
            await PromoCodeRedemption.objects.acreate(user=user, promo_code=promo)
            promo.uses += 1
            await promo.asave()

            import asyncio
            image_buffer = await asyncio.to_thread(generate_card_image, card)
            file = discord.File(fp=image_buffer, filename=f"promo_{card.name}.png")
            embed = discord.Embed(
                title=f"Promo Code Redeemed: {pack_category.capitalize()} Pack!",
                description=f"You got **{card.name}**!",
                color=discord.Color.green(),
            )
            embed.set_image(url=f"attachment://promo_{card.name}.png")
            embed.set_footer(text=f"OVR: {card.ovr} | Rarity: {card.rarity}")
            await interaction.followup.send(file=file, embed=embed)

        else:
            await interaction.response.send_message(
                "Unknown reward type for this promo code.", ephemeral=True
            )

    async def pack_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        from core.models import Pack, DiscordUser, UserPack
        from django.db import models

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )
        await check_and_grant_weekly_packs(user)

        @sync_to_async
        def get_choices():
            user_packs = {
                up.pack_id: up.stash_count
                for up in UserPack.objects.filter(user=user, stash_count__gt=0)
            }
            qs = Pack.objects.all()
            if current:
                qs = qs.filter(
                    models.Q(name__icontains=current) | models.Q(code__icontains=current)
                )
            packs = list(qs[:25])
            choices = []
            for p in packs:
                count = user_packs.get(p.id, 0)
                label = f"{p.name} ({count}x available)" if count > 0 else f"{p.name} (0 in wallet)"
                choices.append(app_commands.Choice(name=label, value=p.code))
            return choices

        return await get_choices()

    @app_commands.command(
        name="pack_open", description="Open a custom or stashed pack from your wallet by name"
    )
    @app_commands.autocomplete(pack_name=pack_autocomplete)
    async def pack_open(self, interaction: discord.Interaction, pack_name: str):
        from core.models import Pack, UserPack
        from django.db import models

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Check and grant weekly pack if due
        await check_and_grant_weekly_packs(user)

        pack_obj = await Pack.objects.filter(
            models.Q(code__iexact=pack_name) | models.Q(name__iexact=pack_name)
        ).afirst()
        if not pack_obj:
            return await interaction.response.send_message("Pack not found!", ephemeral=True)

        # Ensure the user has the pack in their wallet to prevent infinite openings
        user_pack = await UserPack.objects.filter(user=user, pack=pack_obj).afirst()
        if not user_pack or user_pack.stash_count <= 0:
            return await interaction.response.send_message(
                f"You don't have any **{pack_obj.name}** packs in your wallet! Check `/pack_wallet` to see your inventory.",
                ephemeral=True,
            )

        # Pull card type filter directly from pack_obj (configured in panel)
        c_filter = pack_obj.card_type_filter if pack_obj.card_type_filter != "ANY" else None

        await self.open_pack(
            interaction,
            pack_type=pack_obj.code,
            card_filter_type=c_filter,
            cooldown_days=None,  # Stashed packs bypass cooldown
            event_name_filter=pack_obj.event_name_filter or None,
            min_ovr_filter=pack_obj.min_ovr_filter,
            max_ovr_filter=pack_obj.max_ovr_filter,
        )

    # ══════════════════════════════════════════════════════════
    #  /pack_wallet — View and open stashed packs
    # ══════════════════════════════════════════════════════════

    @app_commands.command(
        name="pack_wallet", description="View your stashed packs and open them"
    )
    async def pack_wallet(self, interaction: discord.Interaction):
        from core.models import UserPack

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Check and grant weekly pack if due
        await check_and_grant_weekly_packs(user)

        @sync_to_async
        def get_stashed_packs():
            return list(
                UserPack.objects.filter(user=user, stash_count__gt=0)
                .select_related("pack")
                .order_by("-stash_count")
            )

        stashed = await get_stashed_packs()

        if not stashed:
            return await interaction.response.send_message(
                "📦 Your pack wallet is empty! Earn packs through SBCs, weekly event distributions, or admin grants.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="📦 Pack Wallet",
            description="Your stashed packs. Select one to open it!",
            color=discord.Color.blue(),
        )
        for up in stashed:
            embed.add_field(
                name=f"{up.pack.name}",
                value=f"**{up.stash_count}x** available",
                inline=True,
            )

        # Build select menu
        options = [
            discord.SelectOption(
                label=f"{up.pack.name} ({up.stash_count}x)",
                description=f"Open one {up.pack.name} pack",
                value=up.pack.code,
            )
            for up in stashed[:25]
        ]

        select = discord.ui.Select(
            placeholder="Choose a pack to open...",
            options=options,
        )

        cog_ref = self

        async def on_select(select_interaction: discord.Interaction):
            pack_code = select.values[0]
            from core.models import Pack
            pack_obj = await Pack.objects.filter(code=pack_code).afirst()
            if not pack_obj:
                return await select_interaction.response.send_message("Pack not found!", ephemeral=True)

            # Check stash
            user_pack = await UserPack.objects.filter(user=user, pack=pack_obj, stash_count__gt=0).afirst()
            if not user_pack:
                return await select_interaction.response.send_message(
                    "You don't have any of these packs left!", ephemeral=True
                )

            # Pull card type filter directly from pack_obj (configured in panel)
            c_filter = pack_obj.card_type_filter if pack_obj.card_type_filter != "ANY" else None

            await cog_ref.open_pack(
                select_interaction,
                pack_type=pack_obj.code,
                card_filter_type=c_filter,
                cooldown_days=None,  # Stashed packs bypass cooldown
                event_name_filter=pack_obj.event_name_filter or None,
                min_ovr_filter=pack_obj.min_ovr_filter,
                max_ovr_filter=pack_obj.max_ovr_filter,
            )

        select.callback = on_select

        view = discord.ui.View(timeout=120)
        view.add_item(select)

        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PackCog(bot))
