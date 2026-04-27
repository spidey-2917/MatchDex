import asyncio
import io

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import CardTemplate, DiscordUser, FavouriteCard, UserCard
from core.utils import generate_card_image, player_autocomplete


class CollectionPagination(discord.ui.View):
    def __init__(self, user, cards, favourited_ids):
        super().__init__(timeout=60)
        self.user = user
        self.cards = cards
        self.favourited_ids = favourited_ids
        self.page = 0
        self.per_page = 10
        self.update_buttons()

    def update_buttons(self):
        self.prev_button.disabled = self.page == 0
        self.next_button.disabled = (self.page + 1) * self.per_page >= len(self.cards)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.gray)
    async def prev_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page -= 1
        await self.refresh(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.gray)
    async def next_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        self.page += 1
        await self.refresh(interaction)

    async def refresh(self, interaction):
        self.update_buttons()
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    def create_embed(self):
        start = self.page * self.per_page
        end = start + self.per_page
        page_cards = self.cards[start:end]

        embed = discord.Embed(
            title=f"{self.user.name}'s Collection", color=discord.Color.blue()
        )
        for card in page_cards:
            heart = " ❤️" if card.id in self.favourited_ids else ""
            embed.add_field(
                name=f"{card.template.display_name} ({card.template.position}){heart}",
                value=(
                    f"OVR: {card.template.ovr} | Rarity: {card.template.rarity} "
                    f"| ID: {card.card_id}"
                ),
                inline=False,
            )
        total_pages = max(1, (len(self.cards) - 1) // self.per_page + 1)
        embed.set_footer(text=f"Page {self.page + 1} of {total_pages}")
        return embed


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

    @app_commands.command(name="collection", description="Show your card collection")
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="OVR (Highest)", value="ovr"),
            app_commands.Choice(name="Rarity", value="rarity"),
            app_commands.Choice(name="Catch Date (Newest)", value="newest"),
            app_commands.Choice(name="Card Type (Events)", value="type"),
            app_commands.Choice(name="Favourites Only", value="favourites"),
        ]
    )
    async def collection(
        self,
        interaction: discord.Interaction,
        sort_by: str = "ovr",
        player: str = None,
    ):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        @sync_to_async
        def get_user_cards():
            qs = UserCard.objects.filter(owner=user).select_related("template")

            if player:
                qs = qs.filter(template__name__icontains=player)

            if sort_by == "ovr":
                qs = qs.order_by("-template__ovr", "-caught_at")
            elif sort_by == "rarity":
                qs = qs.order_by("template__rarity", "-template__ovr")
            elif sort_by == "newest":
                qs = qs.order_by("-caught_at")
            elif sort_by == "type":
                qs = qs.order_by("template__card_type", "-template__ovr")
            elif sort_by == "favourites":
                fav_ids = set(
                    FavouriteCard.objects.filter(owner=user).values_list(
                        "card_id", flat=True
                    )
                )
                qs = qs.filter(id__in=fav_ids).order_by("-template__ovr")

            # Gather favourite IDs for the heart display
            favourited = set(
                FavouriteCard.objects.filter(owner=user).values_list(
                    "card_id", flat=True
                )
            )
            return list(qs), favourited

        cards, favourited_ids = await get_user_cards()
        if not cards:
            msg = (
                "No favourited cards yet! Use `/favourite <card_id>` to mark some."
                if sort_by == "favourites"
                else "Your collection is empty! Go catch some cards."
            )
            await interaction.response.send_message(msg, ephemeral=True)
            return

        view = CollectionPagination(interaction.user, cards, favourited_ids)
        await interaction.response.send_message(embed=view.create_embed(), view=view)

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
            qs = DiscordUser.objects.order_by("-points")
            if scope == "server" and interaction.guild:
                # Filter to members in this guild
                member_ids = [m.id for m in interaction.guild.members]
                qs = qs.filter(discord_id__in=member_ids)
            return list(qs[:10])

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


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
