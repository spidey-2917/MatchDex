import io
import json

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, Trade, TradeItem, UserCard, CardTemplate
from core.utils import player_autocomplete, CardListView, SkipPageModal, clear_card_from_lineups


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

        from core.models import UserCard, Lineup, FavouriteCard, PromoCodeRedemption
        # Delete related data but keep the DiscordUser to preserve cooldowns
        await UserCard.objects.filter(owner=user).adelete()
        await Lineup.objects.filter(owner=user).adelete()
        await FavouriteCard.objects.filter(owner=user).adelete()
        await PromoCodeRedemption.objects.filter(user=user).adelete()

        # Reset profile stats
        user.points = 0
        user.wins = 0
        user.losses = 0
        user.draws = 0
        user.cards_collected = 0
        await user.asave()

        # Turn off buttons
        for child in self.children:
            child.disabled = True

        await interaction.response.edit_message(
            content="✅ **Account Deleted.** All your cards, lineups, stats, and profile data have been permanently erased from the Matchdex database.",
            view=self,
        )
        self.stop()

class ConfirmGiftView(discord.ui.View):
    def __init__(self, sender_id, recipient, card):
        super().__init__(timeout=60)
        self.sender_id = sender_id
        self.recipient = recipient
        self.card = card

    @discord.ui.button(label="Confirm Gift", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.sender_id:
            return await interaction.response.send_message("Only the sender can confirm this.", ephemeral=True)

        # Double check ownership right before transfer
        self.card = await UserCard.objects.select_related("template").aget(id=self.card.id)
        if self.card.owner_id != self.sender_id:
            return await interaction.response.send_message("You no longer own this card!", ephemeral=True)

        # Transfer
        target_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=self.recipient.id, defaults={"username": self.recipient.name}
        )
        sender_user = await DiscordUser.objects.aget(discord_id=self.sender_id)
        
        self.card.owner = target_user
        self.card.traded_by = sender_user
        await self.card.asave()

        # Clear from sender's lineups
        await clear_card_from_lineups(self.card.id)

        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"🎁 Gift confirmed!",
            view=self
        )

        # Send a PUBLIC message so everyone can see the gift
        if interaction.channel and isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"🎁 **{interaction.user.mention}** gave **{self.card.template.display_name}** "
                f"(`#{self.card.card_id}`) to {self.recipient.mention}!"
            )
        self.stop()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.sender_id:
            return await interaction.response.send_message("Only the sender can cancel this.", ephemeral=True)
            
        await interaction.response.edit_message(content="❌ Gift cancelled.", view=None)
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

        from django.db.models import Q

        @sync_to_async
        def find_card():
            # 1. Try finding by unique UserCard ID
            uc = (
                UserCard.objects.filter(card_id__iexact=identifier, owner__discord_id=interaction.user.id)
                .select_related("template", "owner", "traded_by")
                .first()
            )
            if uc:
                return uc, uc.template

            # 2. Try finding by Template name within user's inventory
            uc_by_name = (
                UserCard.objects.filter(owner__discord_id=interaction.user.id)
                .filter(Q(template__name__iexact=identifier) | Q(template__name__icontains=identifier))
                .select_related("template", "owner", "traded_by")
                .first()
            )
            if uc_by_name:
                return uc_by_name, uc_by_name.template
                
            return None, None

        user_card, template = await find_card()
        if not template:
            await interaction.followup.send(
                f"Could not find any card matching **{identifier}** in your inventory.",
                ephemeral=True,
            )
            return

        image_buffer = await asyncio.to_thread(generate_card_image, template)
        file = discord.File(fp=image_buffer, filename=f"{template.name}.png")

        if user_card:
            caught_str = f"Caught on {discord.utils.format_dt(user_card.caught_at, 'f')} ({discord.utils.format_dt(user_card.caught_at, 'R')})."
            
            # Build traded_by line
            traded_by_str = ""
            if user_card.traded_by:
                trader_name = user_card.traded_by.username or str(user_card.traded_by.discord_id)
                if interaction.guild:
                    member = interaction.guild.get_member(user_card.traded_by.discord_id)
                    if member:
                        trader_name = member.display_name
                traded_by_str = f"\nTraded by: {trader_name}"
            
            content = (
                f"ID: #{user_card.card_id}\n"
                f"{caught_str}{traded_by_str}\n\n"
                f"ATK: {template.attack_stat}\n"
                f"DEF: {template.defence_stat}"
            )
        else:
            content = (
                f"Player: {template.display_name}\n\n"
                f"ATK: {template.attack_stat}\n"
                f"DEF: {template.defence_stat}"
            )

        await interaction.followup.send(content=content, file=file)

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
    async def list_cards(self, interaction: discord.Interaction, sort_by: str = "ovr", reverse: bool = False, user: str | None = None, event: str | None = None):
        target_db = None
        target_id = None
        target_name = None

        if user:
            if hasattr(user, 'id'):
                user_val = str(user.id)
            else:
                user_val = str(user)
            clean = user_val.strip("<@!> ")
            if clean.isdigit():
                user_id = int(clean)
                target_db = await DiscordUser.objects.filter(discord_id=user_id).afirst()
                if not target_db:
                    try:
                        d_user = await self.bot.fetch_user(user_id)
                        target_db, _ = await DiscordUser.objects.aget_or_create(discord_id=user_id, defaults={"username": d_user.name})
                    except:
                        pass
            if not target_db:
                target_db = await DiscordUser.objects.filter(username__iexact=user).afirst()

            if not target_db:
                return await interaction.response.send_message(f"User '{user}' not found.", ephemeral=True)
            
            target_id = target_db.discord_id
            target_name = target_db.username
        else:
            target_id = interaction.user.id
            target_name = interaction.user.name
            target_db, _ = await DiscordUser.objects.aget_or_create(
                discord_id=target_id,
                defaults={"username": target_name},
            )

        # Privacy check: if viewing someone else's inventory
        if target_id != interaction.user.id and target_db.is_inventory_private:
            # Check if requester is a bot admin (they bypass privacy)
            is_requester_admin = await self.bot.is_admin(interaction.user)
            if not is_requester_admin:
                return await interaction.response.send_message(
                    f"🔒 **{target_name}**'s inventory is set to private.", ephemeral=True
                )

        # We'll initialize the view which will handle fetching and pagination
        view = SettingsCardListView(target_db, sort_by, self.bot, reverse, requester_id=interaction.user.id, event=event)
        await view.update_view(interaction)

    @md_group.command(
        name="give", description="Gift a player card to another user"
    )
    @app_commands.autocomplete(identifier=player_autocomplete)
    @app_commands.describe(
        user="The user you want to give the card to",
        identifier="The MatchDex card you want to give"
    )
    async def give(self, interaction: discord.Interaction, user: discord.Member, identifier: str):
        if user.id == interaction.user.id:
            return await interaction.response.send_message("You cannot give a card to yourself!", ephemeral=True)
        if user.bot:
            return await interaction.response.send_message("You cannot give cards to bots!", ephemeral=True)

        # Find the card
        card = await UserCard.objects.filter(
            card_id__iexact=identifier,
            owner__discord_id=interaction.user.id
        ).select_related("template").afirst()

        if not card:
            return await interaction.response.send_message(
                f"You don't own a card with ID **#{identifier}**.", ephemeral=True
            )

        view = ConfirmGiftView(interaction.user.id, user, card)
        await interaction.response.send_message(
            f"🤝 Are you sure you want to give your **{card.template.display_name}** (#{card.card_id}) to {user.mention}?\n"
            "*This action is irreversible!*",
            view=view,
            ephemeral=True
        )

    @md_group.command(
        name="privacy", description="Toggle your inventory privacy (hide your collection from others)"
    )
    async def privacy(self, interaction: discord.Interaction):
        db_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )
        # Toggle
        db_user.is_inventory_private = not db_user.is_inventory_private
        await db_user.asave()

        if db_user.is_inventory_private:
            await interaction.response.send_message(
                "🔒 Your inventory is now **private**. Other users cannot view your card list.\n"
                "*(Bot administrators can still view your inventory.)*",
                ephemeral=True
            )
        else:
            await interaction.response.send_message(
                "🔓 Your inventory is now **public**. Anyone can view your card list.",
                ephemeral=True
            )


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
            .select_related("template", "owner", "traded_by")
            .afirst()
        )
        if not card:
            return await interaction.followup.send("Card not found.", ephemeral=True)

        image_buffer = await asyncio.to_thread(generate_card_image, card.template)
        file = discord.File(fp=image_buffer, filename=f"{card.template.name}.png")

        caught_str = f"Caught on {discord.utils.format_dt(card.caught_at, 'f')} ({discord.utils.format_dt(card.caught_at, 'R')})."
        
        # Build traded_by line
        traded_by_str = ""
        if card.traded_by:
            trader_name = card.traded_by.username or str(card.traded_by.discord_id)
            if interaction.guild:
                member = interaction.guild.get_member(card.traded_by.discord_id)
                if member:
                    trader_name = member.display_name
            traded_by_str = f"\nTraded by: {trader_name}"
        
        content = (
            f"ID: #{card.card_id}\n"
            f"{caught_str}{traded_by_str}\n\n"
            f"ATK: {card.template.attack_stat}\n"
            f"DEF: {card.template.defence_stat}"
        )

        await interaction.followup.send(content=content, file=file)


class SettingsCardListView(CardListView):
    def add_selection_menu(self, cards):
        if cards:
            select = SettingsCardSelect(cards)
            select.row = 2
            self.add_item(select)


async def setup(bot):
    await bot.add_cog(MdSettingsCog(bot))
