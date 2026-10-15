import discord
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, SimSeason, SimSeasonPlayer


class LeaderboardCog(commands.Cog, name="Leaderboard"):
    def __init__(self, bot):
        self.bot = bot

    leaderboard_group = app_commands.Group(
        name="leaderboard", description="View Matchdex leaderboards"
    )

    @leaderboard_group.command(name="matches", description="View the classic match leaderboard (by points)")
    async def lb_matches(self, interaction: discord.Interaction):
        await interaction.response.defer()

        # Top 10 by points
        top_users = [
            u async for u in DiscordUser.objects.order_by("-points")[:10]
        ]

        if not top_users:
            return await interaction.followup.send("No match data available.")

        embed = discord.Embed(
            title="🏆 Classic Matches Leaderboard",
            color=discord.Color.blue()
        )

        lines = []
        for idx, user in enumerate(top_users, 1):
            name = user.username or f"User {user.discord_id}"
            stats = f"{user.wins}W / {user.draws}D / {user.losses}L"
            lines.append(f"**{idx}.** {name} — **{user.points} pts** ({stats})")

        embed.description = "\n".join(lines)
        await interaction.followup.send(embed=embed)

    @leaderboard_group.command(name="sim_matches", description="View the active Quick Sim season leaderboard (by Trophies)")
    async def lb_sim_matches(self, interaction: discord.Interaction):
        await interaction.response.defer()

        active_season = await SimSeason.objects.filter(is_active=True).afirst()
        if not active_season:
            return await interaction.followup.send(
                "❌ There is no active Quick Sim season right now."
            )

        # Top 10 by trophies in current season
        top_players = [
            p async for p in SimSeasonPlayer.objects.filter(season=active_season)
            .select_related("user")
            .order_by("-trophies")[:10]
        ]

        embed = discord.Embed(
            title=f"🏆 {active_season.name} Leaderboard",
            description="Quick Sim mode rankings based on Trophies.",
            color=discord.Color.gold()
        )

        if not top_players:
            embed.description += "\n\n*No matches played yet this season.*"
            return await interaction.followup.send(embed=embed)

        lines = []
        for idx, player in enumerate(top_players, 1):
            name = player.user.username or f"User {player.user.discord_id}"
            stats = f"{player.wins}W / {player.draws}D / {player.losses}L"
            lines.append(f"**{idx}.** {name} — **{player.trophies} Trophies** ({stats})")

        embed.description += "\n\n" + "\n".join(lines)
        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(LeaderboardCog(bot))
