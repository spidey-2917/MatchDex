import io
import json

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, Trade, TradeItem, UserCard, CardTemplate
from core.utils import player_autocomplete


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
        view = CardListView(user, sort_by, self.bot, reverse)
        await view.update_view(interaction)


class CardSelect(discord.ui.Select):
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


class SkipPageModal(discord.ui.Modal, title="Skip to Page"):
    page_input = discord.ui.TextInput(
        label="Enter Page Number",
        placeholder="e.g. 5",
        min_length=1,
        max_length=5,
    )

    def __init__(self, view, interaction):
        super().__init__()
        self.view = view
        self.original_interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_page = int(self.page_input.value)
            total_pages = (self.view.total_cards + self.view.page_size - 1) // self.view.page_size
            
            if 1 <= target_page <= total_pages:
                self.view.page = target_page - 1
                await interaction.response.defer()
                await self.view.update_view(self.original_interaction)
            else:
                await interaction.response.send_message(f"Invalid page! Please enter a number between 1 and {total_pages}.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Please enter a valid numerical page number.", ephemeral=True)


class CardListView(discord.ui.View):
    def __init__(self, user_db, sort_by, bot, reverse=False):
        super().__init__(timeout=180)
        self.user_db = user_db
        self.sort_by = sort_by
        self.bot = bot
        self.reverse = reverse
        self.page = 0
        self.page_size = 25
        self.total_cards = 0

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_db.discord_id:
            await interaction.response.send_message(
                "Only the user who opened this list can control it.", ephemeral=True
            )
            return False
        return True

    async def get_page_cards(self):
        @sync_to_async
        def fetch():
            qs = UserCard.objects.filter(owner=self.user_db).select_related("template")
            
            if self.sort_by == "ovr":
                order = ["-template__ovr", "-caught_at"]
                if self.reverse:
                    order = ["template__ovr", "caught_at"]
                qs = qs.order_by(*order)
            elif self.sort_by == "rarity":
                # Note: This is alphabetical by default. 
                # Reverse flips the alphabetical order.
                order = ["-template__rarity", "-template__ovr"]
                if self.reverse:
                    order = ["template__rarity", "template__ovr"]
                qs = qs.order_by(*order)
            elif self.sort_by == "type":
                order = ["-template__card_type", "-template__ovr"]
                if self.reverse:
                    order = ["template__card_type", "template__ovr"]
                qs = qs.order_by(*order)
            elif self.sort_by == "date":
                order = ["-caught_at"]
                if self.reverse:
                    order = ["caught_at"]
                qs = qs.order_by(*order)
            
            self.total_cards = qs.count()
            start = self.page * self.page_size
            end = start + self.page_size
            return list(qs[start:end])

        return await fetch()

    async def update_view(self, interaction: discord.Interaction):
        cards = await self.get_page_cards()
        total_pages = (self.total_cards + self.page_size - 1) // self.page_size
        
        if not cards and self.page == 0:
            msg = "You have no cards!"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
            return

        self.clear_items()

        # Row 0: Pagination Controls
        first_btn = discord.ui.Button(label="<<", style=discord.ButtonStyle.secondary, disabled=(self.page == 0), row=0)
        prev_btn = discord.ui.Button(label="...", style=discord.ButtonStyle.primary, disabled=(self.page == 0), row=0)
        
        # Page numbers
        curr_page_btn = discord.ui.Button(label=str(self.page + 1), style=discord.ButtonStyle.primary, row=0)
        next_page_val = self.page + 2 if self.page + 1 < total_pages else "-"
        next_page_btn = discord.ui.Button(
            label=str(next_page_val), 
            style=discord.ButtonStyle.secondary,
            disabled=(next_page_val == "-"), 
            row=0
        )
        
        last_btn = discord.ui.Button(label=">>", style=discord.ButtonStyle.secondary, disabled=(self.page + 1 >= total_pages), row=0)

        # Callbacks
        async def cb_first(it):
            self.page = 0
            await it.response.defer()
            await self.update_view(interaction)
        async def cb_prev(it):
            self.page = max(0, self.page - 1)
            await it.response.defer()
            await self.update_view(interaction)
        async def cb_next(it):
            self.page = min(total_pages - 1, self.page + 1)
            await it.response.defer()
            await self.update_view(interaction)
        async def cb_last(it):
            self.page = total_pages - 1
            await it.response.defer()
            await self.update_view(interaction)

        first_btn.callback = cb_first
        prev_btn.callback = cb_prev
        next_page_btn.callback = cb_next
        last_btn.callback = cb_last

        self.add_item(first_btn)
        self.add_item(prev_btn)
        self.add_item(curr_page_btn)
        self.add_item(next_page_btn)
        self.add_item(last_btn)

        # Row 1: Utility
        skip_btn = discord.ui.Button(label="Skip to page...", style=discord.ButtonStyle.secondary, row=1)
        async def cb_skip(it):
            await it.response.send_modal(SkipPageModal(self, interaction))
        skip_btn.callback = cb_skip
        self.add_item(skip_btn)

        quit_btn = discord.ui.Button(label="Quit", style=discord.ButtonStyle.danger, row=1)
        async def cb_quit(it):
            await it.response.edit_message(content="🛑 List closed.", view=None)
            self.stop()
        quit_btn.callback = cb_quit
        self.add_item(quit_btn)

        # Row 2: Select Menu
        if cards:
            select = CardSelect(cards)
            select.placeholder = "Make a selection"
            select.row = 2
            self.add_item(select)

        content = f"**{self.user_db.username}'s Cards** (Sorted by {self.sort_by.upper()}{' REVERSED' if self.reverse else ''})\n" \
                  f"Page {self.page + 1} of {max(1, total_pages)} ({self.total_cards} cards total)"

        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=self)
        else:
            await interaction.response.send_message(content=content, view=self)


async def setup(bot):
    await bot.add_cog(MdSettingsCog(bot))
