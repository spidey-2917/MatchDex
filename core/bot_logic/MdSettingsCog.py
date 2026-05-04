import io
import json

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, Trade, TradeItem, UserCard, CardTemplate
from core.utils import player_autocomplete, CardListView, SkipPageModal


class ConfirmDeleteView(discord.ui.View):
    def __init__(self, user_id):
        super().__init__(timeout=60)
        self.user_id = user_id

    @discord.ui.button(
        label="Permanently Delete My Data", style=discord.ButtonStyle.danger
    )
    async def confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if interaction.user.id != self.user_id:
            return await interaction.response.send_message(
                "You cannot act on this.", ephemeral=True
            )

        user = await DiscordUser.objects.filter(discord_id=self.user_id).afirst()
        if not user:
            return await interaction.response.send_message(
                "You don't have an account registered.", ephemeral=True
            )

        # Due to cascade deletion, deleting the DiscordUser will delete their UserCards
        await user.adelete()

        # Turn off buttons
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="✅ **Account Deleted.** All your cards, lineups, stats, and profile data have been permanently erased from the Matchdex database.",
            view=self,
        )
        self.stop()


class MdSettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot):
        self.bot = bot

    md_group = app_commands.Group(
        name="md", description="Master configuration and data management commands"
    )

    @md_group.command(
        name="delete",
        description="Permanently delete your profile and all your cards from the game",
    )
    async def delete(self, interaction: discord.Interaction):
        # We must ask for a strict confirmation
        view = ConfirmDeleteView(interaction.user.id)
        embed = discord.Embed(
            title="⚠️ IRREVERSIBLE ACTION ⚠️",
            description=(
                "You are about to **permanently delete** your Matchdex profile.\n\n"
                "- All of your collected cards will be destroyed.\n"
                "- Your stats (Coins, Wins, Losses) will be wiped.\n"
                "- Your active lineups will be erased.\n\n"
                "**This action cannot be undone. Are you absolutely sure?**"
            ),
            color=discord.Color.red(),
        )
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    @md_group.command(
        name="export", description="Export a JSON file of all your user data"
    )
    async def export(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user = await DiscordUser.objects.filter(discord_id=interaction.user.id).afirst()
        if not user:
            return await interaction.followup.send(
                "You do not have a registered account yet.", ephemeral=True
            )

        # Get cards
        cards = []
        async for c in UserCard.objects.filter(owner=user).select_related("template"):
            cards.append(
                {
                    "card_id": c.card_id,
                    "name": c.template.name,
                    "ovr": c.template.ovr,
                    "rarity": c.template.rarity,
                    "caught_at": c.caught_at.isoformat(),
                }
            )

        data = {
            "discord_id": user.discord_id,
            "username": user.username,
            "stats": {
                "points": user.points,
                "wins": user.wins,
                "losses": user.losses,
                "draws": user.draws,
                "cards_collected": user.cards_collected,
            },
            "inventory": cards,
        }

        file_bytes = io.BytesIO(json.dumps(data, indent=4).encode("utf-8"))
        file = discord.File(
            fp=file_bytes, filename=f"matchdex_export_{user.discord_id}.json"
        )
        await interaction.followup.send(
            "Here is your requested data export:", file=file, ephemeral=True
        )


    @md_group.command(
        name="show", description="Show detailed information about a player card"
    )
    @app_commands.autocomplete(identifier=player_autocomplete)
    @app_commands.describe(identifier="The MatchDex card you want to inspect")
    async def show(self, interaction: discord.Interaction, identifier: str):
        from core.utils import generate_card_image
        import asyncio

        await interaction.response.defer()

        @sync_to_async
        def find_card():
            # 1. Try finding by unique UserCard ID
            uc = (
                UserCard.objects.filter(card_id__iexact=identifier)
                .select_related("template", "owner")
                .first()
            )
            if uc:
                return uc, uc.template

            # 2. Try finding by Template name
            tpl = (
                CardTemplate.objects.filter(name__iexact=identifier).first()
                or CardTemplate.objects.filter(name__icontains=identifier).first()
            )
            return None, tpl

        user_card, template = await find_card()
        if not template:
            await interaction.followup.send(
                f"Could not find any player or card matching **{identifier}**.",
                ephemeral=True,
            )
            return

        image_buffer = await asyncio.to_thread(generate_card_image, template)
        file = discord.File(fp=image_buffer, filename=f"{template.name}.png")

        title = f"Player Info: {template.display_name}"
        if user_card:
            title = f"Card #{user_card.card_id}: {template.display_name}"

        embed = discord.Embed(title=title, color=discord.Color.blue())
        if user_card:
            embed.add_field(name="Owner", value=user_card.owner.username, inline=True)
            embed.add_field(name="Card ID", value=user_card.card_id, inline=True)

        embed.add_field(name="Position", value=template.position, inline=True)
        embed.add_field(name="OVR", value=str(template.ovr), inline=True)
        embed.add_field(name="Rarity", value=template.rarity, inline=True)
        embed.add_field(name="Attack", value=str(template.attack_stat), inline=True)
        embed.add_field(name="Defence", value=str(template.defence_stat), inline=True)
        embed.add_field(name="Club", value=template.club, inline=True)
        embed.add_field(
            name="Card Type", value=template.get_card_type_display(), inline=True
        )
        embed.set_image(url=f"attachment://{template.name}.png")

        await interaction.followup.send(file=file, embed=embed)

    @md_group.command(
        name="list", description="Show a list of your cards with sorting and selection"
    )
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="OVR (Highest)", value="ovr"),
            app_commands.Choice(name="Rarity", value="rarity"),
            app_commands.Choice(name="Card Type", value="type"),
            app_commands.Choice(name="Catch Date (Newest)", value="date"),
        ]
    )
    async def list_cards(self, interaction: discord.Interaction, sort_by: str = "ovr", reverse: bool = False):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        # We'll initialize the view which will handle fetching and pagination
        view = SettingsCardListView(user, sort_by, self.bot, reverse)
        await view.update_view(interaction)


class SettingsCardSelect(discord.ui.Select):
    def __init__(self, cards):
        options = [
            discord.SelectOption(
                label=f"{c.template.display_name} ({c.template.ovr})",
                description=f"ID: {c.card_id} | {c.template.rarity}",
                value=c.card_id,
            )
            for c in cards
        ]
        super().__init__(placeholder="Select a card to view...", options=options)

    async def callback(self, interaction: discord.Interaction):
        from core.models import UserCard
        from core.utils import generate_card_image
        import asyncio

        await interaction.response.defer()

        card_id = self.values[0]
        card = (
            await UserCard.objects.filter(card_id=card_id)
            .select_related("template", "owner")
            .afirst()
        )
        if not card:
            return await interaction.followup.send("Card not found.", ephemeral=True)

        image_buffer = await asyncio.to_thread(generate_card_image, card.template)
        file = discord.File(fp=image_buffer, filename=f"{card.template.name}.png")

        embed = discord.Embed(
            title=f"Card #{card.card_id}: {card.template.display_name}",
            color=discord.Color.blue(),
        )
        embed.add_field(name="Owner", value=card.owner.username, inline=True)
        embed.add_field(name="Position", value=card.template.position, inline=True)
        embed.add_field(name="OVR", value=str(card.template.ovr), inline=True)
        embed.add_field(name="Rarity", value=card.template.rarity, inline=True)
        embed.add_field(
            name="Card Type", value=card.template.get_card_type_display(), inline=True
        )
        embed.set_image(url=f"attachment://{card.template.name}.png")

        await interaction.followup.send(file=file, embed=embed)


class SettingsCardListView(CardListView):
    def add_selection_menu(self, cards):
        if cards:
            select = SettingsCardSelect(cards)
            select.row = 2
            self.add_item(select)


async def setup(bot):
    await bot.add_cog(MdSettingsCog(bot))
