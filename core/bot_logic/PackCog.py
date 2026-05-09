import io
from datetime import datetime, timedelta

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands
from django.utils import timezone

from core.models import CardTemplate, DiscordUser, UserCard
from core.utils import generate_card_image, get_random_card_by_rarity, get_random_rarity


class PackCog(commands.Cog, name="Packs"):
    def __init__(self, bot):
        self.bot = bot

    async def open_pack(
        self,
        interaction: discord.Interaction,
        pack_type,
        card_filter_type=None,
        cooldown_days=None,
    ):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )

        # Cooldown check
        now = timezone.now()
        last_redeem_attr = f"last_pack_{pack_type}"
        last_redeem = getattr(user, last_redeem_attr)

        if cooldown_days and last_redeem:
            if now < last_redeem + timedelta(days=cooldown_days):
                remaining = (last_redeem + timedelta(days=cooldown_days)) - now
                hours, remainder = divmod(int(remaining.total_seconds()), 3600)
                minutes, _ = divmod(remainder, 60)
                await interaction.response.send_message(
                    f"You need to wait **{hours}h {minutes}m** before opening another {pack_type} pack!",
                    ephemeral=True,
                )
                return

        await interaction.response.defer()

        await interaction.response.defer()

        # Pick random card based on configuration
        from core.utils import pick_random_card
        card = await sync_to_async(pick_random_card)("PACK", card_type_filter=card_filter_type)

        if not card:
            await interaction.followup.send(
                "No cards available in this pack category yet!", ephemeral=True
            )
            return

        # Save record
        await UserCard.objects.acreate(owner=user, template=card)
        setattr(user, last_redeem_attr, now)
        user.cards_collected += 1
        await user.asave()

        # Generate image
        import asyncio

        image_buffer = await asyncio.to_thread(generate_card_image, card)
        file = discord.File(fp=image_buffer, filename=f"pack_{card.name}.png")

        embed = discord.Embed(
            title=f"New {pack_type.capitalize()} Pack Opened!",
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

    @app_commands.command(name="pack_event", description="Pack a weekly event card")
    async def pack_event(self, interaction: discord.Interaction):
        await self.open_pack(
            interaction, "event", card_filter_type="EVENT", cooldown_days=7
        )

    @app_commands.command(
        name="pack_premium",
        description="Pack a random icon or event card (Every 2 days)",
    )
    async def pack_premium(self, interaction: discord.Interaction):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id, defaults={"username": interaction.user.name}
        )
        if not user.is_premium:
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
            
            # Determine the card type filter based on pack category
            type_filter = None
            if pack_category == "daily":
                type_filter = "BASE"
            elif pack_category == "weekly":
                type_filter = "ICON"
            elif pack_category == "event":
                type_filter = "EVENT"
            elif pack_category == "premium":
                type_filter = ["ICON", "EVENT"]

            card = await sync_to_async(pick_random_card)("PACK", card_type_filter=type_filter)
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


async def setup(bot):
    await bot.add_cog(PackCog(bot))

