import asyncio
import random

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import CardTemplate, DiscordUser, UserCard
from core.utils import generate_card_image
from core.objectives import update_objective_progress

class WagerArena(discord.ui.View):
    def __init__(self, cog, user_a: discord.Member, user_b: discord.Member):
        super().__init__(timeout=600)  # 10 mins interaction timeout
        self.cog = cog
        self.user_a = user_a
        self.user_b = user_b

        self.stakes = {"A": [], "B": []}
        self.locked = {"A": False, "B": False}
        self.confirmed = {"A": False, "B": False}
        self.confirm_mode = False
        self.message = None
        self.status = "Waiting for players to build their stakes..."
        self.completed = False

        self.remove_item(self.btn_confirm)
        self.remove_item(self.btn_decline)

    def end_wager_state(self):
        self.completed = True
        self.cog.active_wagers.pop(self.user_a.id, None)
        self.cog.active_wagers.pop(self.user_b.id, None)

    def generate_embed(self):
        embed = discord.Embed(
            title="🏟️ WAGER ARENA 🏟️",
            description=f"{self.user_a.mention} vs {self.user_b.mention}\n\nUse `/wager add <player>` to add cards to your stake.\n\n**Status:** {self.status}",
            color=discord.Color.red(),
        )

        # Format A
        if self.confirm_mode:
            state_a = "✔️ Confirmed" if self.confirmed["A"] else "⏳ Waiting..."
        else:
            state_a = "🔒 LOCKED" if self.locked["A"] else "🔓 Unlocked"

        stake_a_text = (
            "\n".join(
                [
                    f"• [{c.card_id}] **{c.template.name}** ({c.template.ovr})"
                    for c in self.stakes["A"]
                ]
            )
            or "Empty"
        )
        embed.add_field(
            name=f"🔵 {self.user_a.display_name}'s Stake\n{state_a}",
            value=stake_a_text,
            inline=True,
        )

        # Format B
        if self.confirm_mode:
            state_b = "✔️ Confirmed" if self.confirmed["B"] else "⏳ Waiting..."
        else:
            state_b = "🔒 LOCKED" if self.locked["B"] else "🔓 Unlocked"

        stake_b_text = (
            "\n".join(
                [
                    f"• [{c.card_id}] **{c.template.name}** ({c.template.ovr})"
                    for c in self.stakes["B"]
                ]
            )
            or "Empty"
        )
        embed.add_field(
            name=f"🔴 {self.user_b.display_name}'s Stake\n{state_b}",
            value=stake_b_text,
            inline=True,
        )

        # Add a footer
        embed.set_footer(text="Matchdex Bot • Penalty Shootout Theme")

        return embed

    def get_user_key(self, user_id):
        if user_id == self.user_a.id:
            return "A"
        if user_id == self.user_b.id:
            return "B"
        return None

    def transition_to_confirm(self):
        self.confirm_mode = True
        self.status = "✅ Both players locked! Now confirm to conclude this wager."
        self.remove_item(self.btn_lock)
        self.remove_item(self.btn_clear)
        self.remove_item(self.btn_cancel)
        self.add_item(self.btn_confirm)
        self.add_item(self.btn_decline)

    async def update_message(self, interaction=None):
        embed = self.generate_embed()
        if interaction and not interaction.response.is_done():
            # If the interaction hasn't been responded to yet, we could use this,
            # but usually we've deferred. If deferred, we edit the original message.
            try:
                await interaction.message.edit(embed=embed, view=self)
            except Exception:
                await interaction.edit_original_response(embed=embed, view=self)
        elif self.message:
            await self.message.edit(embed=embed, view=self)

    @discord.ui.button(label="🟢 Lock / Unlock", style=discord.ButtonStyle.green, row=0)
    async def btn_lock(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        key = self.get_user_key(interaction.user.id)
        if not key:
            await interaction.response.send_message(
                "You are not part of this wager!", ephemeral=True
            )
            return

        # Toggle lock
        self.locked[key] = not self.locked[key]

        if self.locked["A"] and self.locked["B"]:
            # Both locked!
            self.transition_to_confirm()
            await interaction.response.defer()
            await self.update_message()
        else:
            await interaction.response.defer()
            await self.update_message()

    @discord.ui.button(
        label="🔄 Clear Stake", style=discord.ButtonStyle.secondary, row=1
    )
    async def btn_clear(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        key = self.get_user_key(interaction.user.id)
        if not key:
            await interaction.response.send_message(
                "You are not part of this wager!", ephemeral=True
            )
            return

        if self.locked[key]:
            await interaction.response.send_message(
                "You cannot clear stakes while locked! Unlock first.", ephemeral=True
            )
            return

        self.stakes[key] = []
        await interaction.response.defer()
        await self.update_message()

    @discord.ui.button(label="❌ Cancel Wager", style=discord.ButtonStyle.danger, row=1)
    async def btn_cancel(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        key = self.get_user_key(interaction.user.id)
        if not key:
            await interaction.response.send_message(
                "You are not part of this wager!", ephemeral=True
            )
            return

        self.status = f"🚫 Wager cancelled by {interaction.user.display_name}."
        for child in self.children:
            child.disabled = True

        self.end_wager_state()
        await interaction.response.defer()
        await self.update_message()

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.success, row=0)
    async def btn_confirm(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        key = self.get_user_key(interaction.user.id)
        if not key:
            await interaction.response.send_message(
                "You are not part of this wager!", ephemeral=True
            )
            return

        self.confirmed[key] = True

        if self.confirmed["A"] and self.confirmed["B"]:
            self.status = "⚽ Both players confirmed! Preparing for Penalty Shootout..."
            for child in self.children:
                child.disabled = True
            await interaction.response.defer()
            await self.update_message()
            await self.resolve_wager()
        else:
            await interaction.response.defer()
            await self.update_message()

    @discord.ui.button(label="❌ Decline", style=discord.ButtonStyle.danger, row=0)
    async def btn_decline(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        key = self.get_user_key(interaction.user.id)
        if not key:
            await interaction.response.send_message(
                "You are not part of this wager!", ephemeral=True
            )
            return

        self.status = f"🚫 Wager declined by {interaction.user.display_name}."
        for child in self.children:
            child.disabled = True

        self.end_wager_state()
        await interaction.response.defer()
        await self.update_message()

    async def resolve_wager(self):
        # 10 second penalty shootout simulation
        await asyncio.sleep(2)

        # Decide winner randomly 50/50
        winner_key = random.choice(["A", "B"])
        loser_key = "B" if winner_key == "A" else "A"

        winner_user = self.user_a if winner_key == "A" else self.user_b
        loser_user = self.user_b if winner_key == "A" else self.user_a

        # Determine who shoots first for immersion
        first_shooter = random.choice(["A", "B"])
        second_shooter = "B" if first_shooter == "A" else "A"

        first_user = self.user_a if first_shooter == "A" else self.user_b
        second_user = self.user_b if first_shooter == "A" else self.user_a

        first_scores = first_shooter == winner_key

        # Part 1: First shooter
        self.status = f"👟 {first_user.display_name} steps up to the penalty spot..."
        await self.update_message()
        await asyncio.sleep(2)

        if first_scores:
            self.status += " and SCORES! 🥅"
        else:
            self.status += " and it's SAVED! 🧤"
        await self.update_message()
        await asyncio.sleep(2)

        # Part 2: Second shooter
        self.status += (
            f"\n👟 {second_user.display_name} steps up to the penalty spot..."
        )
        await self.update_message()
        await asyncio.sleep(2)

        # The result must guarantee the predetermined winner wins
        # E.g. if User A won, but User B (first) scored, User A must score to tie, then User B misses sudden death, User A scores.
        # Alternatively, we can just script a direct outcome to avoid complex sudden death formatting for now.
        if winner_key == second_shooter:
            # Second shooter won
            if first_scores:
                self.status += " and SCORES! 🥅\n\n🔄 SUDDEN DEATH: The opposing keeper makes a crucial blunder next round!"
            else:
                self.status += " and SCORES! 🥅"
        else:
            # Second shooter lost
            if first_scores:
                self.status += " and it's SAVED! 🧤"
            else:
                self.status += " misses completely! 🚫"

        await self.update_message()
        await asyncio.sleep(2)

        # Part 3: Transfer logic DB
        cards_to_transfer = self.stakes[loser_key]

        @sync_to_async
        def get_wager_users():
            return DiscordUser.objects.get(discord_id=winner_user.id), DiscordUser.objects.get(discord_id=loser_user.id)
            
        winner_db, loser_db = await get_wager_users()

        if cards_to_transfer:
            @sync_to_async
            def do_transfer():
                for card in cards_to_transfer:
                    card.owner = winner_db
                    card.save()

            await do_transfer()
            
            from core.utils import clear_card_from_lineups
            for card in cards_to_transfer:
                await clear_card_from_lineups(card.id)

        if self.stakes["A"] or self.stakes["B"]:
            await update_objective_progress(winner_db, "play_wager")
            await update_objective_progress(loser_db, "play_wager")

        self.status += f"\n\n🏆 **{winner_user.mention} WINS THE SHOWDOWN!** 🏆\nAll staked cards have been transferred to the winner."
        self.end_wager_state()
        await self.update_message()


class WagerCog(commands.Cog, name="Wagers"):
    def __init__(self, bot):
        self.bot = bot
        self.active_wagers = {}  # Maps user_id -> WagerArena view instance

    wager_group = app_commands.Group(
        name="wager", description="Commands for the Wager Arena"
    )

    @wager_group.command(
        name="challenge",
        description="Challenge another user to a Wager Arena shootout!",
    )
    async def challenge(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.user.id:
            await interaction.response.send_message(
                "You cannot challenge yourself!", ephemeral=True
            )
            return

        if user.bot:
            await interaction.response.send_message(
                "You cannot challenge a bot!", ephemeral=True
            )
            return

        if interaction.user.id in self.active_wagers or user.id in self.active_wagers:
            await interaction.response.send_message(
                "One of you is already in an active wager! Finish or cancel it first.",
                ephemeral=True,
            )
            return

        # Ensure both users are in DB
        @sync_to_async
        def init_users():
            DiscordUser.objects.get_or_create(
                discord_id=interaction.user.id,
                defaults={"username": interaction.user.name},
            )
            DiscordUser.objects.get_or_create(
                discord_id=user.id, defaults={"username": user.name}
            )

        await init_users()

        view = WagerArena(self, interaction.user, user)
        self.active_wagers[interaction.user.id] = view
        self.active_wagers[user.id] = view

        # Send initial message
        await interaction.response.send_message(embed=view.generate_embed(), view=view)
        # Fetch the message to store in view
        view.message = await interaction.original_response()

    async def card_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        @sync_to_async
        def get_matching_cards():
            # Get cards owned by the user that match the search string
            qs = UserCard.objects.filter(
                owner__discord_id=interaction.user.id
            ).select_related("template")
            if current:
                qs = qs.filter(template__name__icontains=current)
            # Limit to 25 to comply with Discord limits
            return list(qs[:25])

        cards = await get_matching_cards()
        choices = []
        for c in cards:
            name_display = f"{c.template.name} ({c.template.ovr} OVR) - ID: {c.card_id}"
            # value must be string, we pass the card_id
            choices.append(
                app_commands.Choice(name=name_display[:100], value=c.card_id)
            )
        return choices

    @wager_group.command(
        name="add", description="Add a player from your collection to your active wager"
    )
    @app_commands.autocomplete(card_id=card_autocomplete)
    async def add(self, interaction: discord.Interaction, card_id: str):
        view = self.active_wagers.get(interaction.user.id)
        if not view:
            await interaction.response.send_message(
                "You are not part of an active wager!", ephemeral=True
            )
            return

        key = view.get_user_key(interaction.user.id)
        if view.locked[key]:
            await interaction.response.send_message(
                "Your wager is locked! Unlock it first.", ephemeral=True
            )
            return

        @sync_to_async
        def get_card():
            return (
                UserCard.objects.filter(
                    card_id=card_id, owner__discord_id=interaction.user.id
                )
                .select_related("template")
                .first()
            )

        card = await get_card()
        if not card:
            await interaction.response.send_message(
                f"You don't own a card with ID `{card_id}`.", ephemeral=True
            )
            return

        # Check if already in wager
        all_staked = [c.card_id for c in view.stakes["A"]] + [
            c.card_id for c in view.stakes["B"]
        ]
        if card_id in all_staked:
            await interaction.response.send_message(
                "That card is already in the wager!", ephemeral=True
            )
            return

        # Add to stakes
        view.stakes[key].append(card)

        await interaction.response.send_message(
            f"Added **{card.template.name}** to your wager stakes!", ephemeral=True
        )
        await view.update_message()

    @wager_group.command(name="cancel", description="Cancel your active wager")
    async def cancel(self, interaction: discord.Interaction):
        view = self.active_wagers.get(interaction.user.id)
        if not view:
            await interaction.response.send_message(
                "You are not part of an active wager!", ephemeral=True
            )
            return

        # Determine who cancelled it for the status message
        view.status = (
            f"🚫 Wager cancelled by {interaction.user.display_name} via command."
        )
        for child in view.children:
            child.disabled = True

        view.end_wager_state()
        await interaction.response.send_message(
            "✅ Your active wager has been cancelled.", ephemeral=True
        )
        await view.update_message()


async def setup(bot):
    await bot.add_cog(WagerCog(bot))
