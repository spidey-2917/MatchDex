import discord
from discord.ext import commands
from discord import app_commands
from core.models import DiscordUser, Referral, Season
from core.utils import to_base36, from_base36

class InviteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="invite", description="View your referral invite code and milestones")
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

    @app_commands.command(name="redeem", description="Redeem an invite code from the person who invited you")
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

    @app_commands.command(name="scouts", description="View the top recruiters leaderboard")
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

async def setup(bot):
    await bot.add_cog(InviteCog(bot))
