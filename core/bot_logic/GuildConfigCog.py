import discord
from discord import app_commands
from discord.ext import commands

from core.models import ServerSettings


class GuildConfigCog(commands.Cog, name="Server Config"):
    def __init__(self, bot):
        self.bot = bot

    config_group = app_commands.Group(
        name="config", description="Configure server-specific bot behavior"
    )

    @config_group.command(
        name="spawn_channel",
        description="Set the channel where cards will natively spawn",
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def spawn_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await interaction.response.defer(ephemeral=True)

        settings, _ = await ServerSettings.objects.aget_or_create(
            guild_id=interaction.guild_id
        )
        settings.spawn_channel_id = channel.id
        await settings.asave()

        await interaction.followup.send(
            f"✅ Spawn channel successfully set to {channel.mention}."
        )

    @config_group.command(
        name="log_channel", description="Set the admin logging channel for this server"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def log_channel(
        self, interaction: discord.Interaction, channel: discord.TextChannel
    ):
        await interaction.response.defer(ephemeral=True)

        settings, _ = await ServerSettings.objects.aget_or_create(
            guild_id=interaction.guild_id
        )
        settings.command_log_channel_id = channel.id
        await settings.asave()

        await interaction.followup.send(
            f"✅ Logging channel successfully set to {channel.mention}."
        )


async def setup(bot):
    await bot.add_cog(GuildConfigCog(bot))
