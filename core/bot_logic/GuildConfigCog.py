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
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
    ):
        await interaction.response.defer(ephemeral=True)

        settings, _ = await ServerSettings.objects.aget_or_create(
            guild_id=interaction.guild_id
        )
        settings.spawn_channel_id = channel.id
        await settings.asave()

        # Reset spawn counters so the natural cycle starts fresh — no free spawns
        spawning_cog = self.bot.get_cog("Spawning")
        if spawning_cog:
            import time as _time
            guild_id = interaction.guild_id
            spawning_cog.message_counts[guild_id] = 0
            spawning_cog.last_spawn_time[guild_id] = _time.time()
            spawning_cog._roll_threshold(guild_id)

        await interaction.followup.send(
            f"✅ Spawn channel set to {channel.mention}. Cards will start appearing naturally as members chat!"
        )

    @config_group.command(
        name="log_channel", description="Set the admin logging channel for this server"
    )
    @app_commands.checks.has_permissions(administrator=True)
    async def log_channel(
        self, interaction: discord.Interaction, channel: discord.abc.GuildChannel
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
