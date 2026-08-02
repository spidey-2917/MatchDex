import discord
from asgiref.sync import sync_to_async
from django.db import models
from discord import app_commands
from discord.ext import commands, tasks
from datetime import datetime, timedelta, timezone

from core.models import DiscordUser, Trade, TradeItem, UserCard
from core.objectives import update_objective_progress
from core.utils import CardListView

ACTIVE_TRADES = {}


class AcceptTradeView(discord.ui.View):
    def __init__(self, initiator, receiver, cog):
        super().__init__(timeout=120)
        self.initiator = initiator
        self.receiver = receiver
        self.cog = cog
        self._processing = False

    @discord.ui.button(label="Accept Trade", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.receiver.id:
            return await interaction.response.send_message(
                "Only the invited user can accept this trade.", ephemeral=True
            )
        if self._processing:
            return await interaction.response.send_message(
                "Already processing…", ephemeral=True
            )
        self._processing = True
        await interaction.response.defer()
        self.stop()

        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        await interaction.edit_original_response(
            content=f"✅ {self.receiver.mention} accepted the trade request from {self.initiator.mention}!",
            view=self,
        )

        await self.cog.init_trade(interaction.channel, self.initiator, self.receiver)


class TradeActionView(discord.ui.View):
    def __init__(self, trade_id, cog):
        super().__init__(timeout=None)
        self.trade_id = trade_id
        self.cog = cog
        self._processing = False
        # Overwrite custom IDs for persistence across bot restarts
        self.confirm_btn.custom_id = f"conf_{trade_id}"
        self.cancel_btn.custom_id = f"canc_{trade_id}"

    @discord.ui.button(
        label="Lock In (0/2)", style=discord.ButtonStyle.success, emoji="✅"
    )
    async def confirm_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self._processing:
            return await interaction.response.send_message(
                "Already processing…", ephemeral=True
            )
        self._processing = True
        await interaction.response.defer(ephemeral=True)
        t = ACTIVE_TRADES.get(self.trade_id)
        if not t:
            return await interaction.followup.send(
                "Trade not found or expired.", ephemeral=True
            )

        if interaction.user.id not in (t["initiator"].id, t["receiver"].id):
            return await interaction.followup.send(
                "You are not part of this trade.", ephemeral=True
            )

        if interaction.user.id == t["initiator"].id:
            t["initiator_confirm"] = True
        else:
            t["receiver_confirm"] = True

        confirms = sum([t["initiator_confirm"], t["receiver_confirm"]])
        button.label = f"Lock In ({confirms}/2)"

        await self.cog.save_trade_state(self.trade_id)
        await t["message"].edit(
            embed=self.cog.build_trade_embed(self.trade_id), view=self
        )
        await interaction.followup.send(
            "You have locked in your offer.", ephemeral=True
        )

        if t["initiator_confirm"] and t["receiver_confirm"]:
            for child in self.children:
                if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                    child.disabled = True
            await t["message"].edit(view=self)
            await self.cog.execute_trade(self.trade_id)
        self._processing = False

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌")
    async def cancel_btn(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ):
        if self._processing:
            return await interaction.response.send_message(
                "Already processing…", ephemeral=True
            )
        self._processing = True
        await interaction.response.defer()
        t = ACTIVE_TRADES.get(self.trade_id)
        if not t:
            return await interaction.followup.send("Trade not found.", ephemeral=True)

        if interaction.user.id not in (t["initiator"].id, t["receiver"].id):
            return await interaction.followup.send(
                "You are not part of this trade.", ephemeral=True
            )

        for child in self.children:
            if isinstance(child, (discord.ui.Button, discord.ui.Select)):
                child.disabled = True
        await t["message"].edit(
            content=f"❌ Trade cancelled by {interaction.user.mention}.", view=self
        )

        # Update DB
        db_trade = await Trade.objects.aget(id=t["db_id"])
        db_trade.status = "CANCELLED"
        await db_trade.asave()
        del ACTIVE_TRADES[self.trade_id]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        t = ACTIVE_TRADES.get(self.trade_id)
        if t:
            t["last_activity"] = datetime.now(timezone.utc)
        return True


class TradeBulkAddSelect(discord.ui.Select):
    def __init__(self, cards):
        options = [
            discord.SelectOption(
                label=f"{c.template.display_name} ({c.template.ovr})",
                description=f"ID: {c.card_id} | {c.template.rarity}",
                value=c.card_id,
            )
            for c in cards
        ]
        super().__init__(
            placeholder="Select cards to add to trade...",
            options=options,
            min_values=1,
            max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction):
        # The view handles the logic
        if isinstance(self.view, TradeBulkAddView):
            await self.view.add_selected_cards(interaction, self.values)


class TradeBulkAddView(CardListView):
    def __init__(self, user_db, sort_by, bot, trade_id, cog, reverse=False):
        super().__init__(user_db, sort_by, bot, reverse=reverse, ephemeral=True)
        self.trade_id = trade_id
        self.cog = cog

    def add_selection_menu(self, cards):
        if cards:
            select = TradeBulkAddSelect(cards)
            select.row = 2
            self.add_item(select)

    def add_utility_buttons(self, interaction):
        add_all_btn = discord.ui.Button(label="Add All on Page", style=discord.ButtonStyle.success, row=1)
        async def cb_add_all(interaction_add_all: discord.Interaction):
            card_ids = [c.card_id for c in self.current_cards]
            await self.add_selected_cards(interaction_add_all, card_ids)
        add_all_btn.callback = cb_add_all
        self.add_item(add_all_btn)

        quit_btn = discord.ui.Button(label="Done", style=discord.ButtonStyle.primary, row=1)
        async def cb_quit(interaction_quit: discord.Interaction):
            await interaction_quit.response.edit_message(content="✅ Finished adding cards to trade.", view=None)
            self.stop()
        quit_btn.callback = cb_quit
        self.add_item(quit_btn)

    async def add_selected_cards(self, interaction, card_ids):
        await interaction.response.defer(ephemeral=True)
        t = ACTIVE_TRADES.get(self.trade_id)
        if not t:
            return await interaction.followup.send("Trade not found or expired.", ephemeral=True)

        t["initiator_confirm"] = False
        t["receiver_confirm"] = False

        offer_list = t["initiator_offer"] if interaction.user.id == t["initiator"].id else t["receiver_offer"]
        
        added_count = 0
        for cid in card_ids:
            if any(c.card_id == cid for c in offer_list):
                continue
            
            try:
                card = await UserCard.objects.select_related("template").aget(
                    card_id=cid, owner__discord_id=interaction.user.id
                )
                offer_list.append(card)
                added_count += 1
            except UserCard.DoesNotExist:
                continue

        if added_count > 0:
            await self.cog.save_trade_state(self.trade_id)
            view = TradeActionView(self.trade_id, self.cog)
            await t["message"].edit(embed=self.cog.build_trade_embed(self.trade_id), view=view)
            await interaction.followup.send(f"Added **{added_count}** card(s) to the trade.", ephemeral=True)
        else:
            await interaction.followup.send("No new cards were added (they might already be in the trade).", ephemeral=True)


class TradeCog(commands.Cog, name="Trading"):
    def __init__(self, bot):
        self.bot = bot
        self.check_inactive_trades.start()

    async def cog_unload(self) -> None:
        self.check_inactive_trades.cancel()

    @tasks.loop(minutes=5)
    async def check_inactive_trades(self):
        """Automatically cancel trades that have been inactive for 30 minutes."""
        now = datetime.now(timezone.utc)
        to_cancel = []

        for tid, t in ACTIVE_TRADES.items():
            last_act = t.get("last_activity")
            if not last_act:
                # If no activity yet, check creation time from DB or just set current
                t["last_activity"] = now
                continue

            if now - last_act > timedelta(minutes=30):
                to_cancel.append(tid)

        for tid in to_cancel:
            t = ACTIVE_TRADES.get(tid)
            if not t:
                continue

            try:
                # Update DB
                db_trade = await Trade.objects.aget(id=t["db_id"])
                db_trade.status = "CANCELLED"
                await db_trade.asave()

                # Update message if possible
                if t.get("message"):
                    await t["message"].edit(
                        content="❌ Trade cancelled due to 30 minutes of inactivity.",
                        view=None,
                    )
            except Exception:
                pass
            
            del ACTIVE_TRADES[tid]

    async def cog_load(self):
        # Re-hydrate trades that were PENDING if the bot was restarted
        import json

        @sync_to_async
        def fetch_pending():
            return list(
                Trade.objects.filter(status="PENDING", message_id__isnull=False)
            )

        pending_trades = await fetch_pending()
        for db_trade in pending_trades:
            trade_id = f"trade_{db_trade.initiator_id}_{db_trade.receiver_id}"
            try:
                initiator = await self.bot.fetch_user(db_trade.initiator_id)
                receiver = await self.bot.fetch_user(db_trade.receiver_id)
                channel = await self.bot.fetch_channel(db_trade.channel_id)
                message = await channel.fetch_message(db_trade.message_id)

                state_data = db_trade.state_data or {}
                initiator_offer_ids = state_data.get("initiator_offer", [])
                receiver_offer_ids = state_data.get("receiver_offer", [])

                @sync_to_async
                def get_cards(card_ids):
                    return list(
                        UserCard.objects.filter(card_id__in=card_ids).select_related(
                            "template"
                        )
                    )

                ACTIVE_TRADES[trade_id] = {
                    "db_id": db_trade.id,
                    "initiator": initiator,
                    "receiver": receiver,
                    "initiator_offer": await get_cards(initiator_offer_ids),
                    "receiver_offer": await get_cards(receiver_offer_ids),
                    "initiator_confirm": state_data.get("initiator_confirm", False),
                    "receiver_confirm": state_data.get("receiver_confirm", False),
                    "channel": channel,
                    "message": message,
                    "last_activity": datetime.now(timezone.utc),
                }

                view = TradeActionView(trade_id, self)
                self.bot.add_view(view, message_id=db_trade.message_id)
            except Exception as e:
                # If we fail to rehydrate (e.g., message deleted), just cancel it
                db_trade.status = "CANCELLED"
                await db_trade.asave()

    trade_group = app_commands.Group(
        name="trade", description="Trade cards with other players"
    )

    @trade_group.command(name="start", description="Start a trade with another player")
    async def start(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.user.id or user.bot:
            return await interaction.response.send_message(
                "You cannot trade with yourself or a bot.", ephemeral=True
            )

        for trade_id, t in ACTIVE_TRADES.items():
            if interaction.user.id in (t["initiator"].id, t["receiver"].id):
                return await interaction.response.send_message(
                    "You are already in an active trade.", ephemeral=True
                )
            if user.id in (t["initiator"].id, t["receiver"].id):
                return await interaction.response.send_message(
                    f"**{user.display_name}** is already in an active trade.",
                    ephemeral=True,
                )

        await interaction.response.send_message(
            f"🤝 {user.mention}, you have been invited to trade with {interaction.user.mention}!",
            view=AcceptTradeView(interaction.user, user, self),
        )

    async def save_trade_state(self, trade_id):
        t = ACTIVE_TRADES[trade_id]
        db_trade = await Trade.objects.aget(id=t["db_id"])
        db_trade.state_data = {
            "initiator_offer": [c.card_id for c in t["initiator_offer"]],
            "receiver_offer": [c.card_id for c in t["receiver_offer"]],
            "initiator_confirm": t["initiator_confirm"],
            "receiver_confirm": t["receiver_confirm"],
        }
        t["last_activity"] = datetime.now(timezone.utc)
        await db_trade.asave()

    async def init_trade(self, channel, initiator, receiver):
        trade_id = f"trade_{initiator.id}_{receiver.id}"

        t_obj = await Trade.objects.acreate(
            initiator_id=initiator.id, receiver_id=receiver.id, channel_id=channel.id
        )

        ACTIVE_TRADES[trade_id] = {
            "db_id": t_obj.id,
            "initiator": initiator,
            "receiver": receiver,
            "initiator_offer": [],
            "receiver_offer": [],
            "initiator_confirm": False,
            "receiver_confirm": False,
            "channel": channel,
            "message": None,
            "last_activity": datetime.now(timezone.utc),
        }

        embed = self.build_trade_embed(trade_id)
        view = TradeActionView(trade_id, self)
        try:
            msg = await channel.send(embed=embed, view=view)
            ACTIVE_TRADES[trade_id]["message"] = msg
            t_obj.message_id = msg.id
            await t_obj.asave()
        except discord.Forbidden:
            # Bot cannot send messages here
            del ACTIVE_TRADES[trade_id]
            t_obj.status = "CANCELLED"
            await t_obj.asave()
            # If this was triggered by a button, the user will see the error in the console or we could try a DM
            # but for now we just fail gracefully to avoid the traceback.

    def build_trade_embed(self, trade_id):
        t = ACTIVE_TRADES[trade_id]
        embed = discord.Embed(
            title="🔄 Active Trade",
            description="Use `/trade add`, and `/trade remove`.",
        )

        initiator_text = f"**{len(t['initiator_offer'])} Cards Offered**\n"
        for i, card in enumerate(t["initiator_offer"][:10]):
            initiator_text += f"- {card.template.name} ({card.template.ovr})\n"
        if len(t["initiator_offer"]) > 10:
            initiator_text += f"... and {len(t['initiator_offer']) - 10} more."
        if not t["initiator_offer"]:
            initiator_text += "Nothing yet."
        if t["initiator_confirm"]:
            initiator_text += "\n\n✅ **READY**"

        receiver_text = f"**{len(t['receiver_offer'])} Cards Offered**\n"
        for i, card in enumerate(t["receiver_offer"][:10]):
            receiver_text += f"- {card.template.name} ({card.template.ovr})\n"
        if len(t["receiver_offer"]) > 10:
            receiver_text += f"... and {len(t['receiver_offer']) - 10} more."
        if not t["receiver_offer"]:
            receiver_text += "Nothing yet."
        if t["receiver_confirm"]:
            receiver_text += "\n\n✅ **READY**"

        embed.add_field(
            name=t["initiator"].display_name, value=initiator_text, inline=True
        )
        embed.add_field(name="vs", value="---", inline=True)
        embed.add_field(
            name=t["receiver"].display_name, value=receiver_text, inline=True
        )

        if t["initiator_confirm"] and t["receiver_confirm"]:
            embed.color = discord.Color.green()
        else:
            embed.color = discord.Color.gold()
        return embed

    async def card_autocomplete(self, interaction: discord.Interaction, current: str):
        trade_id = None
        for tid, t in ACTIVE_TRADES.items():
            if interaction.user.id in (t["initiator"].id, t["receiver"].id):
                trade_id = tid
                break

        if not trade_id:
            return []

        try:
            user = await DiscordUser.objects.aget(discord_id=interaction.user.id)
            cards = UserCard.objects.filter(owner=user).select_related("template")
            if current:
                cards = cards.filter(template__name__icontains=current)

            choices = []
            async for c in cards[:25]:
                choices.append(
                    app_commands.Choice(
                        name=f"{c.template.name} ({c.template.ovr}) [{c.card_id}]",
                        value=c.card_id,
                    )
                )
            return choices
        except (DiscordUser.DoesNotExist, Exception):
            return []

    async def trade_offer_autocomplete(
        self, interaction: discord.Interaction, current: str
    ):
        trade_id = None
        t = None
        for tid, obj in ACTIVE_TRADES.items():
            if interaction.user.id in (obj["initiator"].id, obj["receiver"].id):
                trade_id = tid
                t = obj
                break
        if not t:
            return []

        offer = (
            t["initiator_offer"]
            if interaction.user.id == t["initiator"].id
            else t["receiver_offer"]
        )
        choices = []
        for c in offer:
            if (
                current.lower() in c.template.name.lower()
                or current.lower() in c.card_id.lower()
            ):
                choices.append(
                    app_commands.Choice(
                        name=f"{c.template.name} ({c.template.ovr}) [{c.card_id}]",
                        value=c.card_id,
                    )
                )
        return choices[:25]

    @trade_group.command(
        name="add", description="Add a card to the active trade (No limit)"
    )
    @app_commands.autocomplete(card_id=card_autocomplete)
    async def trade_add(self, interaction: discord.Interaction, card_id: str):
        await interaction.response.defer(ephemeral=True)

        t, tid = None, None
        for k, v in ACTIVE_TRADES.items():
            if interaction.user.id in (v["initiator"].id, v["receiver"].id):
                t, tid = v, k
                break

        if not t:
            return await interaction.followup.send(
                "You are not in an active trade.", ephemeral=True
            )

        t["initiator_confirm"] = False
        t["receiver_confirm"] = False

        try:
            card = await UserCard.objects.select_related("template").aget(
                card_id=card_id, owner__discord_id=interaction.user.id
            )
        except UserCard.DoesNotExist:
            return await interaction.followup.send(
                "You don't own that card or it doesn't exist.", ephemeral=True
            )

        offer_list = (
            t["initiator_offer"]
            if interaction.user.id == t["initiator"].id
            else t["receiver_offer"]
        )
        if any(c.id == card.id for c in offer_list):
            return await interaction.followup.send(
                "You already offered that card.", ephemeral=True
            )

        offer_list.append(card)

        await self.save_trade_state(tid)
        view = TradeActionView(tid, self)
        await t["message"].edit(embed=self.build_trade_embed(tid), view=view)

        await interaction.followup.send(
            f"Added **{card.template.name}** to the trade.", ephemeral=True
        )

    @trade_group.command(
        name="remove", description="Remove a card from the active trade"
    )
    @app_commands.autocomplete(card_id=trade_offer_autocomplete)
    async def trade_remove(self, interaction: discord.Interaction, card_id: str):
        await interaction.response.defer(ephemeral=True)

        t, tid = None, None
        for k, v in ACTIVE_TRADES.items():
            if interaction.user.id in (v["initiator"].id, v["receiver"].id):
                t, tid = v, k
                break

        if not t:
            return await interaction.followup.send(
                "You are not in an active trade.", ephemeral=True
            )

        t["initiator_confirm"] = False
        t["receiver_confirm"] = False

        offer_list = (
            t["initiator_offer"]
            if interaction.user.id == t["initiator"].id
            else t["receiver_offer"]
        )
        t[
            f"{'initiator' if interaction.user.id == t['initiator'].id else 'receiver'}_offer"
        ] = [c for c in offer_list if c.card_id != card_id]

        await self.save_trade_state(tid)
        view = TradeActionView(tid, self)
        await t["message"].edit(embed=self.build_trade_embed(tid), view=view)
        await interaction.followup.send("Card removed from your offer.", ephemeral=True)

    @trade_group.command(
        name="bulk_add", description="Add multiple cards to your trade via a list menu"
    )
    @app_commands.choices(
        sort_by=[
            app_commands.Choice(name="OVR (Highest)", value="ovr"),
            app_commands.Choice(name="Rarity", value="rarity"),
            app_commands.Choice(name="Card Type", value="type"),
            app_commands.Choice(name="Catch Date (Newest)", value="date"),
        ]
    )
    async def bulk_add(self, interaction: discord.Interaction, sort_by: str = "ovr", reverse: bool = False):
        # Check if user is in a trade
        trade_id = None
        for tid, t in ACTIVE_TRADES.items():
            if interaction.user.id in (t["initiator"].id, t["receiver"].id):
                trade_id = tid
                break
        
        if not trade_id:
            return await interaction.response.send_message("You are not in an active trade.", ephemeral=True)

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        view = TradeBulkAddView(user, sort_by, self.bot, trade_id, self, reverse)
        await view.update_view(interaction)

    @trade_group.command(
        name="cancel", description="Cancel your current active trade"
    )
    async def trade_cancel(self, interaction: discord.Interaction):
        # Find any active trade this user is part of
        trade_id = None
        trade_data = None
        for tid, t in ACTIVE_TRADES.items():
            if interaction.user.id in (t["initiator"].id, t["receiver"].id):
                trade_id = tid
                trade_data = t
                break

        if not trade_id or not trade_data:
            # Also check the database for stuck PENDING trades
            stuck_trades = Trade.objects.filter(
                status="PENDING"
            ).filter(
                models.Q(initiator_id=interaction.user.id) | models.Q(receiver_id=interaction.user.id)
            )
            cancelled_count = 0
            async for stuck in stuck_trades:
                stuck.status = "CANCELLED"
                await stuck.asave()
                cancelled_count += 1

            if cancelled_count > 0:
                await interaction.response.send_message(
                    f"🧹 Cleared **{cancelled_count}** stuck trade(s) from the database. You can start a new trade now!",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "You don't have any active trades to cancel.", ephemeral=True
                )
            return

        # Try to update the trade message
        try:
            if trade_data.get("message"):
                view = TradeActionView(trade_id, self)
                for child in view.children:
                    child.disabled = True
                await trade_data["message"].edit(
                    content=f"❌ Trade cancelled by {interaction.user.mention}.",
                    view=view,
                )
        except (discord.NotFound, discord.HTTPException):
            pass  # Message might be deleted already, that's fine

        # Update database
        db_trade = await Trade.objects.aget(id=trade_data["db_id"])
        db_trade.status = "CANCELLED"
        await db_trade.asave()

        # Clean up memory
        del ACTIVE_TRADES[trade_id]

        await interaction.response.send_message(
            "✅ Trade cancelled successfully. You can start a new trade now!",
            ephemeral=True,
        )

    async def execute_trade(self, tid):
        t = ACTIVE_TRADES.pop(tid, None)
        if not t:
            return
            
        initiator_db = await DiscordUser.objects.aget(discord_id=t["initiator"].id)
        receiver_db = await DiscordUser.objects.aget(discord_id=t["receiver"].id)
        db_trade = await Trade.objects.aget(id=t["db_id"])

        for card in t["initiator_offer"]:
            fresh_card = await UserCard.objects.aget(id=card.id)
            if fresh_card.owner_id != initiator_db.discord_id:
                continue

            fresh_card.owner = receiver_db
            fresh_card.traded_by = initiator_db
            await fresh_card.asave()

            from core.utils import clear_card_from_lineups
            await clear_card_from_lineups(fresh_card.id)

            await TradeItem.objects.acreate(
                trade=db_trade,
                card=fresh_card,
                sender=initiator_db,
                receiver=receiver_db,
            )

        for card in t["receiver_offer"]:
            fresh_card = await UserCard.objects.aget(id=card.id)
            if fresh_card.owner_id != receiver_db.discord_id:
                continue

            fresh_card.owner = initiator_db
            fresh_card.traded_by = receiver_db
            await fresh_card.asave()

            from core.utils import clear_card_from_lineups
            await clear_card_from_lineups(fresh_card.id)

            await TradeItem.objects.acreate(
                trade=db_trade,
                card=fresh_card,
                sender=receiver_db,
                receiver=initiator_db,
            )

        db_trade.status = "COMPLETED"
        db_trade.completed_at = timezone.now()
        await db_trade.asave()

        await update_objective_progress(initiator_db, "perform_trade")
        await update_objective_progress(receiver_db, "perform_trade")

        embed = discord.Embed(
            title="🎉 Trade Completed successfully!",
            description=f"Trade ID: `#{db_trade.id}`",
            color=discord.Color.green()
        )
        await t["channel"].send(embed=embed)

    @trade_group.command(
        name="history", description="View your trade history"
    )
    @app_commands.describe(user="View trade history with a specific user (optional)")
    async def trade_history(self, interaction: discord.Interaction, user: discord.Member = None):
        await interaction.response.defer(ephemeral=True)

        @sync_to_async
        def get_history():
            from django.db.models import Q, Count
            qs = Trade.objects.filter(
                Q(initiator_id=interaction.user.id) | Q(receiver_id=interaction.user.id),
                status="COMPLETED"
            )
            if user:
                qs = qs.filter(
                    Q(initiator_id=user.id) | Q(receiver_id=user.id)
                )
            return list(
                qs.order_by("-completed_at")
                .select_related("initiator", "receiver")
                .annotate(item_count=Count("items"))[:20]
            )

        trades = await get_history()

        if not trades:
            target_text = f" with **{user.display_name}**" if user else ""
            return await interaction.followup.send(
                f"No completed trades found{target_text}.", ephemeral=True
            )

        embed = discord.Embed(
            title="📜 Trade History",
            description=f"Showing last {len(trades)} completed trade(s)"
                        + (f" with {user.mention}" if user else ""),
            color=discord.Color.blue()
        )

        for trade in trades:
            # Determine the other party
            if trade.initiator_id == interaction.user.id:
                partner = trade.receiver
            else:
                partner = trade.initiator

            # Try to resolve the partner's display name from the guild
            partner_name = partner.username or str(partner.discord_id)
            if interaction.guild:
                member = interaction.guild.get_member(partner.discord_id)
                if member:
                    partner_name = member.display_name

            timestamp = ""
            if trade.completed_at:
                timestamp = f" • {discord.utils.format_dt(trade.completed_at, 'R')}"

            embed.add_field(
                name=f"Trade #{trade.id} — with {partner_name}",
                value=f"{trade.item_count} card(s) exchanged{timestamp}",
                inline=False
            )

        await interaction.followup.send(embed=embed, ephemeral=True)


async def setup(bot):
    await bot.add_cog(TradeCog(bot))
