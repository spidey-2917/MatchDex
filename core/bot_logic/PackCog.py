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

        # Pick rarity and card
        rarity = get_random_rarity()

        @sync_to_async
        def get_card():
            qs = CardTemplate.objects.filter(rarity=rarity)
            if card_filter_type:
                if isinstance(card_filter_type, list):
                    qs = qs.filter(card_type__in=card_filter_type)
                else:
                    qs = qs.filter(card_type=card_filter_type)

            if not qs.exists():
                # Fallback to any card of that type
                qs = CardTemplate.objects.all()
                if card_filter_type:
                    if isinstance(card_filter_type, list):
                        qs = qs.filter(card_type__in=card_filter_type)
                    else:
                        qs = qs.filter(card_type=card_filter_type)

            return qs.order_by("?").first()

        card = await get_card()

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
        from core.models import PromoCode

        promo = await PromoCode.objects.filter(code=code).afirst()
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

        # Reward logic
        if promo.reward_type == "POINTS":
            user.points += int(promo.reward_value)
            await user.asave()
            await interaction.response.send_message(
                f"Promo code redeemed! You received **{promo.reward_value} points**.",
                ephemeral=True,
            )
        elif promo.reward_type == "PACK":
            # Just give a random card for now for "PACK" reward
            rarity = get_random_rarity()
            card = await sync_to_async(get_random_card_by_rarity)(rarity)
            await UserCard.objects.acreate(owner=user, template=card)
            await interaction.response.send_message(
                f"Promo code redeemed! You received a **{card.name}** ({card.rarity})!",
                ephemeral=True,
            )

        promo.uses += 1
        await promo.asave()


async def setup(bot):
    await bot.add_cog(PackCog(bot))
