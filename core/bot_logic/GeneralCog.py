import asyncio
import io

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import CardTemplate, DiscordUser, FavouriteCard, UserCard
from core.utils import generate_card_image, player_autocomplete




class GeneralCog(commands.Cog, name="General"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="stats", description="Show your basic stats")
    async def stats(self, interaction: discord.Interaction):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )
        embed = discord.Embed(
            title=f"Stats for {interaction.user.name}",
            color=discord.Color.orange(),
        )
        embed.add_field(name="Points", value=user.points)
        embed.add_field(
            name="Wins / Losses / Draws",
            value=f"{user.wins} / {user.losses} / {user.draws}",
        )
        embed.add_field(name="Cards Caught", value=user.cards_collected)
        await interaction.response.send_message(embed=embed)

    @app_commands.command(name="collection", description="Show your card collection summary")
    async def collection(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer()
        
        target_user = user or interaction.user
        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=target_user.id,
            defaults={"username": target_user.name},
        )

        from core.models import TradeItem, UserCard
        from django.db.models import Count

        @sync_to_async
        def get_stats():
            total = UserCard.objects.filter(owner=db_user).count()
            
            # Received from trade: Cards the user owns that appear as received in TradeItem
            received = UserCard.objects.filter(
                owner=db_user,
                id__in=TradeItem.objects.filter(receiver=db_user).values('card_id')
            ).count()
            
            caught = total - received
            
            event_qs = UserCard.objects.filter(owner=db_user).exclude(template__event_name="Base")
            total_specials = event_qs.count()
            
            event_breakdown = list(
                event_qs.values('template__event_name')
                .annotate(count=Count('id'))
                .order_by('-count')
            )
            
            return total, caught, received, total_specials, event_breakdown

        total, caught, received, total_specials, event_breakdown = await get_stats()

        embed = discord.Embed(
            title=f"{target_user.display_name}'s Collection",
            color=discord.Color.blue()
        )
        
        embed.add_field(
            name="Total Collection",
            value=f"**Total:** {total:,} ({caught:,} caught, {received:,} received from trade)\n"
                  f"**Total Events:** {total_specials:,}",
            inline=False
        )

        if event_breakdown:
            event_text = ""
            # Map common event names to emojis if possible, else generic
            emoji_map = {
                "Shiny": "✨",
                "Christmas": "🎄",
                "Christmas_white": "❄️",
                "Ramadan_white": "☪️",
                "Diwali_white": "🕯️",
                "Halloween_white": "🦇",
                "Eid_white": "🌙",
                "Icon": "🏆",
                "TOTY": "⭐",
                "TOTS": "🔥"
            }
            
            for item in event_breakdown:
                name = item['template__event_name']
                count = item['count']
                emoji = emoji_map.get(name, "🔹")
                # Fallback for names containing the key
                if emoji == "🔹":
                    for key, val in emoji_map.items():
                        if key.lower() in name.lower():
                            emoji = val
                            break
                
                event_text += f"{emoji} {name}: {count:,}\n"
            
            embed.add_field(name="Events:", value=event_text or "None", inline=False)
        else:
            embed.add_field(name="Events:", value="No event cards collected yet.", inline=False)

        await interaction.followup.send(embed=embed)

    @app_commands.command(
        name="last", description="Show your most recently caught card"
    )
    async def last(self, interaction: discord.Interaction):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        @sync_to_async
        def get_last_card():
            return (
                UserCard.objects.filter(owner=user)
                .select_related("template")
                .order_by("-caught_at")
                .first()
            )

        card = await get_last_card()
        if not card:
            await interaction.response.send_message(
                "You haven't caught any cards yet!", ephemeral=True
            )
            return

        await interaction.response.defer()

        image_buffer = await asyncio.to_thread(generate_card_image, card.template)
        file = discord.File(fp=image_buffer, filename=f"{card.template.name}.png")

        embed = discord.Embed(
            title=f"Last Caught: {card.template.name}",
            color=discord.Color.purple(),
        )
        embed.add_field(name="Position", value=card.template.position, inline=True)
        embed.add_field(name="OVR", value=str(card.template.ovr), inline=True)
        embed.add_field(name="Rarity", value=card.template.rarity, inline=True)
        embed.add_field(name="Card ID", value=card.card_id, inline=True)
        embed.add_field(
            name="Caught At",
            value=card.caught_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
            inline=True,
        )
        embed.set_image(url=f"attachment://{card.template.name}.png")

        await interaction.followup.send(file=file, embed=embed)

    @app_commands.command(name="leaderboard", description="Show the leaderboard")
    @app_commands.choices(
        scope=[
            app_commands.Choice(name="Server", value="server"),
            app_commands.Choice(name="Global", value="global"),
        ]
    )
    async def leaderboard(
        self, interaction: discord.Interaction, scope: str = "server"
    ):
        await interaction.response.defer()

        @sync_to_async
        def get_lb_users():
            if scope == "global" or not interaction.guild:
                return list(DiscordUser.objects.order_by("-points")[:10])
            
            # Without members intent, interaction.guild.members is unreliable.
            # We fetch top 200 global players and filter for those in this guild.
            # This covers the most active/top players in most servers.
            all_top = list(DiscordUser.objects.order_by("-points")[:200])
            guild_users = []
            for u in all_top:
                if interaction.guild.get_member(u.discord_id):
                    guild_users.append(u)
                if len(guild_users) >= 10:
                    break
            return guild_users

        users = await get_lb_users()

        title = "Server Leaderboard" if scope == "server" else "Global Leaderboard"
        embed = discord.Embed(title=f"🏆 {title}", color=discord.Color.gold())

        if not users:
            embed.description = "No players found."
        else:
            for i, u in enumerate(users, 1):
                medals = {1: "🥇", 2: "🥈", 3: "🥉"}
                prefix = medals.get(i, f"**{i}.**")
                embed.add_field(
                    name=f"{prefix} {u.username or u.discord_id}",
                    value=f"Points: {u.points} | Wins: {u.wins}",
                    inline=False,
                )

        await interaction.followup.send(embed=embed)

    @app_commands.command(name="about", description="About MatchDex Bot")
    async def about(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="MatchDex Bot",
            description="The ultimate soccer card collection and trading bot!",
            color=discord.Color.blue()
        )
        embed.add_field(name="Invite Me", value="[Click here to invite the bot to your server!](https://discord.com/oauth2/authorize?client_id=1485170319617429584&permissions=2147863616&integration_type=0&scope=bot+applications.commands)", inline=False)
        embed.set_footer(text="MatchDex Bot v1.0")
        await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
