import io
import json

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, Lineup, Trade, TradeItem, UserCard, CardTemplate, Referral, Season
from core.utils import player_autocomplete, CardListView, SkipPageModal, clear_card_from_lineups, to_base36, from_base36


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

class ReceiverApprovalView(discord.ui.View):
    def __init__(self, sender_id, recipient, card):
        super().__init__(timeout=60)
        self.sender_id = sender_id
        self.recipient = recipient
        self.card = card

    @discord.ui.button(label="Accept Gift", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.recipient.id:
            return await interaction.response.send_message("Only the recipient can accept this.", ephemeral=True)

        self.card = await UserCard.objects.select_related("template").aget(id=self.card.id)
        if self.card.owner_id != self.sender_id:
            return await interaction.response.send_message("The sender no longer owns this card!", ephemeral=True)

        target_user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=self.recipient.id, defaults={"username": self.recipient.name}
        )
        sender_user = await DiscordUser.objects.aget(discord_id=self.sender_id)
        
        self.card.owner = target_user
        self.card.traded_by = sender_user
        await self.card.asave()

        await clear_card_from_lineups(self.card.id)

        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"✅ {interaction.user.mention} accepted the gift!",
            view=self
        )

        if interaction.channel and isinstance(interaction.channel, discord.abc.Messageable):
            await interaction.channel.send(
                f"🎁 **<@{self.sender_id}>** gave **{self.card.template.display_name}** "
                f"(`#{self.card.card_id}`) to {self.recipient.mention}!"
            )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.recipient.id:
            return await interaction.response.send_message("Only the recipient can decline this.", ephemeral=True)
            
        await interaction.response.edit_message(content=f"❌ {interaction.user.mention} declined the gift.", view=None)
        self.stop()

class FriendRequestView(discord.ui.View):
    def __init__(self, requester, target):
        super().__init__(timeout=10)
        self.requester = requester
        self.target = target

    @discord.ui.button(label="Accept", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("Only the recipient can accept this.", ephemeral=True)

        user1, _ = await DiscordUser.objects.aget_or_create(discord_id=self.requester.id, defaults={"username": self.requester.name})
        user2, _ = await DiscordUser.objects.aget_or_create(discord_id=self.target.id, defaults={"username": self.target.name})
        
        await user1.friends.aadd(user2)

        for child in self.children:
            child.disabled = True
        
        await interaction.response.edit_message(
            content=f"✅ {self.target.mention} and {self.requester.mention} are now friends!",
            view=self
        )
        self.stop()

    @discord.ui.button(label="Decline", style=discord.ButtonStyle.danger)
    async def decline(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.target.id:
            return await interaction.response.send_message("Only the recipient can decline this.", ephemeral=True)
            
        await interaction.response.edit_message(content=f"❌ {self.target.mention} declined the friend request.", view=None)
        self.stop()
class MdSettingsCog(commands.Cog, name="Settings"):
    def __init__(self, bot):
        self.bot = bot

    md_group = app_commands.Group(
        name="md", description="Master configuration and data management commands"
    )

    friend_group = app_commands.Group(
        name="friend", description="Manage your MatchDex friends", parent=md_group
    )

    @friend_group.command(name="add", description="Send a friend request to another user")
    async def friend_add(self, interaction: discord.Interaction, user: discord.Member):
        if user.id == interaction.user.id:
            return await interaction.response.send_message("You cannot add yourself as a friend!", ephemeral=True)
        if user.bot:
            return await interaction.response.send_message("You cannot add bots as friends!", ephemeral=True)

        requester, _ = await DiscordUser.objects.aget_or_create(discord_id=interaction.user.id, defaults={"username": interaction.user.name})
        target, _ = await DiscordUser.objects.aget_or_create(discord_id=user.id, defaults={"username": user.name})

        if await requester.friends.filter(discord_id=target.discord_id).aexists():
            return await interaction.response.send_message(f"You are already friends with {user.mention}!", ephemeral=True)

        view = FriendRequestView(interaction.user, user)
        await interaction.response.send_message(
            f"👋 {user.mention}, **{interaction.user.name}** wants to add you as a friend!\n*(You have 10 seconds to accept)*",
            view=view
        )

    @friend_group.command(name="remove", description="Remove a friend")
    async def friend_remove(self, interaction: discord.Interaction, user: discord.Member):
        requester, _ = await DiscordUser.objects.aget_or_create(discord_id=interaction.user.id, defaults={"username": interaction.user.name})
        target, _ = await DiscordUser.objects.aget_or_create(discord_id=user.id, defaults={"username": user.name})
        
        if not await requester.friends.filter(discord_id=target.discord_id).aexists():
            return await interaction.response.send_message(f"You are not friends with {user.mention}.", ephemeral=True)
            
        await requester.friends.aremove(target)
        await interaction.response.send_message(f"✅ Removed {user.mention} from your friends list.", ephemeral=True)

    @friend_group.command(name="list", description="List all your friends")
    async def friend_list(self, interaction: discord.Interaction):
        requester, _ = await DiscordUser.objects.aget_or_create(discord_id=interaction.user.id, defaults={"username": interaction.user.name})
        
        friends = []
        async for friend in requester.friends.all():
            friends.append(f"<@{friend.discord_id}>")
            
        if not friends:
            return await interaction.response.send_message("You don't have any friends yet! Use `/md friend add`.", ephemeral=True)
            
        embed = discord.Embed(title=f"👥 {interaction.user.name}'s Friends", color=discord.Color.blue())
        embed.description = "\n".join(friends)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

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

        sender_user, _ = await DiscordUser.objects.aget_or_create(discord_id=interaction.user.id, defaults={"username": interaction.user.name})
        target_user, _ = await DiscordUser.objects.aget_or_create(discord_id=user.id, defaults={"username": user.name})
        
        policy = target_user.donation_policy
        is_friend = await sender_user.friends.filter(discord_id=target_user.discord_id).aexists()
        
        needs_approval = False
        if policy == "PRIVATE":
            needs_approval = True
        elif policy == "FRIENDS" and not is_friend:
            needs_approval = True
            
        if needs_approval:
            view = ReceiverApprovalView(interaction.user.id, user, card)
            await interaction.response.send_message(
                f"🤝 {user.mention}, **{interaction.user.name}** wants to gift you **{card.template.display_name}** (#{card.card_id}). Do you want to receive it?\n*(You have 60 seconds to accept)*",
                view=view,
                ephemeral=False
            )
        else:
            view = ConfirmGiftView(interaction.user.id, user, card)
            await interaction.response.send_message(
                f"🤝 Are you sure you want to give your **{card.template.display_name}** (#{card.card_id}) to {user.mention}?\n"
                "*This action is irreversible!*",
                view=view,
                ephemeral=True
            )

    @md_group.command(name="donation_policy", description="Set who can send you gifts (Open, Friends Only, Private)")
    @app_commands.choices(policy=[
        app_commands.Choice(name="Open (Anyone)", value="OPEN"),
        app_commands.Choice(name="Friends Only", value="FRIENDS"),
        app_commands.Choice(name="Private (Require Approval)", value="PRIVATE"),
    ])
    async def donation_policy(self, interaction: discord.Interaction, policy: str):
        user, _ = await DiscordUser.objects.aget_or_create(discord_id=interaction.user.id, defaults={"username": interaction.user.name})
        user.donation_policy = policy
        await user.asave()
        
        policy_names = {"OPEN": "Open", "FRIENDS": "Friends Only", "PRIVATE": "Private"}
        await interaction.response.send_message(f"✅ Your donation policy has been set to **{policy_names[policy]}**.", ephemeral=True)

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

    @md_group.command(name="invite", description="View your referral invite code and milestones")
    async def invite(self, interaction: discord.Interaction):
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name}
        )
        invite_code = to_base36(user.discord_id)
        
        # Count stats
        completed = await Referral.objects.filter(inviter=user, status="COMPLETED").acount()
        pending = await Referral.objects.filter(inviter=user, status="PENDING").acount()
        
        embed = discord.Embed(
            title="🤝 MatchDex Referral Program",
            description=(
                f"Share your invite code with new players to earn rewards!\n"
                f"**Your Invite Code**: `{invite_code}`\n\n"
                f"To use it, the new player must run `/md redeem code:{invite_code}`."
            ),
            color=discord.Color.green()
        )
        embed.add_field(name="Your Stats", value=f"✅ Completed: **{completed}**\n⏳ Pending: **{pending}**\n*(Pending invites complete when the new user opens 25 packs)*", inline=False)
        
        embed.add_field(name="Milestone Rewards", value=(
            "**1 Invite**: Rare Card\n"
            "**3 Invites**: Guaranteed Special\n"
            "**5 Invites**: Event Pack\n"
            "**10 Invites**: Exclusive Card\n"
            "**25 Invites**: Limited Icon"
        ), inline=False)

        await interaction.response.send_message(embed=embed)

    @md_group.command(name="redeem", description="Redeem an invite code from the person who invited you")
    async def redeem(self, interaction: discord.Interaction, code: str):
        # Decode the base36 code
        try:
            inviter_id = from_base36(code)
        except ValueError:
            await interaction.response.send_message("❌ Invalid invite code format.", ephemeral=True)
            return

        if inviter_id == interaction.user.id:
            await interaction.response.send_message("❌ You cannot redeem your own invite code.", ephemeral=True)
            return

        # Check if the user already redeemed a code
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name}
        )

        existing_referral = await Referral.objects.filter(invited_user=user).afirst()
        if existing_referral:
            await interaction.response.send_message("❌ You have already redeemed an invite code.", ephemeral=True)
            return
            
        # Check if inviter exists
        inviter = await DiscordUser.objects.filter(discord_id=inviter_id).afirst()
        if not inviter:
            await interaction.response.send_message("❌ The invite code belongs to a user that doesn't exist.", ephemeral=True)
            return

        # Check if they are eligible to redeem (must be relatively new)
        if user.total_packs_opened >= 25:
            await interaction.response.send_message("❌ You are no longer considered a new player (opened >= 25 packs) and cannot redeem an invite code.", ephemeral=True)
            return

        # Get active season
        active_season = await Season.objects.filter(is_active=True).afirst()

        # Create the referral
        await Referral.objects.acreate(
            inviter=inviter,
            invited_user=user,
            status="PENDING",
            season=active_season
        )

        await interaction.response.send_message(f"✅ Successfully linked to **{inviter.username}**! Once you open 25 packs, the referral will be complete.")

    @md_group.command(name="scouts", description="View the top recruiters leaderboard")
    async def scouts(self, interaction: discord.Interaction):
        active_season = await Season.objects.filter(is_active=True).afirst()
        season_filter = {"season": active_season} if active_season else {}

        # We need to group by inviter and count completed
        from django.db.models import Count, Q
        top_scouts = []
        async for r in Referral.objects.filter(status="COMPLETED", **season_filter).values('inviter__username').annotate(total=Count('id')).order_by('-total')[:10]:
            top_scouts.append(r)

        season_name = active_season.name if active_season else "All Time"
        embed = discord.Embed(
            title=f"🏆 Top Scouts Leaderboard ({season_name})",
            color=discord.Color.gold()
        )

        if not top_scouts:
            embed.description = "No completed referrals yet. Be the first!"
        else:
            board = ""
            for i, scout in enumerate(top_scouts, 1):
                board += f"**{i}.** {scout['inviter__username']} - {scout['total']} invites\n"
            embed.description = board

        await interaction.response.send_message(embed=embed)


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
