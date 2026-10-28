import discord
from discord.ext import commands
from discord import app_commands
from core.models import DiscordUser
from core.objectives import (
    get_or_assign_daily_objectives,
    claim_objective_reward,
    get_or_assign_weekly_objectives,
    claim_weekly_objective_reward,
)
from django.utils import timezone

class ObjectivesCog(commands.Cog, name="Objectives"):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="objectives", description="View and claim your daily objectives")
    async def objectives(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        
        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )
        
        objs = await get_or_assign_daily_objectives(user)
        
        if not objs:
            return await interaction.followup.send("No daily objectives are currently configured for the bot.")
            
        embed = discord.Embed(
            title=f"🎯 Daily Objectives for {interaction.user.display_name}",
            color=discord.Color.gold(),
            description="Complete these tasks before the day ends (UTC) to earn rewards!\n*Tip: Use `/weekly` to view weekly objectives.*"
        )
        
        view = discord.ui.View(timeout=180)
        
        for idx, obj in enumerate(objs):
            status = "✅ Completed" if obj.is_claimed else f"In Progress ({obj.progress}/{obj.template.target_amount})"
            if obj.progress >= obj.template.target_amount and not obj.is_claimed:
                status = "🟢 Ready to Claim!"
                
            embed.add_field(
                name=f"{idx+1}. {obj.template.description}",
                value=f"Status: **{status}**",
                inline=False
            )
            
            if obj.progress >= obj.template.target_amount and not obj.is_claimed:
                btn = discord.ui.Button(
                    label=f"Claim #{idx+1}", 
                    style=discord.ButtonStyle.success, 
                    custom_id=f"claim_obj_{obj.id}"
                )
                
                async def claim_callback(intx: discord.Interaction, obj_id=obj.id):
                    reward_text = await claim_objective_reward(user, obj_id)
                    if reward_text:
                        await intx.response.send_message(f"🎉 You claimed: {reward_text}", ephemeral=True)
                        for item in intx.message.components[0].children:
                            if item.custom_id == f"claim_obj_{obj_id}":
                                item.disabled = True
                                item.style = discord.ButtonStyle.secondary
                                item.label = "Claimed"
                        try:
                            await intx.message.edit(view=view)
                        except discord.NotFound:
                            pass
                    else:
                        await intx.response.send_message("❌ Could not claim reward. You may have already claimed it.", ephemeral=True)
                        
                btn.callback = claim_callback
                view.add_item(btn)
                
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="weekly", description="View and claim your weekly objectives (resets every Monday)")
    async def weekly(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        objs = await get_or_assign_weekly_objectives(user)

        if not objs:
            return await interaction.followup.send("No weekly objectives are currently configured for the bot.")

        today = timezone.now().date()
        monday = today - timezone.timedelta(days=today.weekday())
        next_monday = monday + timezone.timedelta(days=7)
        import datetime
        reset_dt = datetime.datetime.combine(next_monday, datetime.time.min, tzinfo=datetime.timezone.utc)
        reset_ts = int(reset_dt.timestamp())

        embed = discord.Embed(
            title=f"📅 Weekly Objectives for {interaction.user.display_name}",
            color=discord.Color.purple(),
            description=f"Complete these high-tier challenges for boosted rewards!\nResets every Monday (<t:{reset_ts}:R>)."
        )

        view = discord.ui.View(timeout=180)

        for idx, obj in enumerate(objs):
            status = "✅ Completed" if obj.is_claimed else f"In Progress ({obj.progress}/{obj.template.target_amount})"
            if obj.progress >= obj.template.target_amount and not obj.is_claimed:
                status = "🟢 Ready to Claim!"

            embed.add_field(
                name=f"{idx+1}. {obj.template.description}",
                value=f"Status: **{status}**",
                inline=False
            )

            if obj.progress >= obj.template.target_amount and not obj.is_claimed:
                btn = discord.ui.Button(
                    label=f"Claim #{idx+1}",
                    style=discord.ButtonStyle.success,
                    custom_id=f"claim_weekly_{obj.id}"
                )

                async def claim_weekly_callback(intx: discord.Interaction, obj_id=obj.id):
                    reward_text = await claim_weekly_objective_reward(user, obj_id)
                    if reward_text:
                        await intx.response.send_message(f"🎉 You claimed weekly reward: {reward_text}", ephemeral=True)
                        for item in intx.message.components[0].children:
                            if item.custom_id == f"claim_weekly_{obj_id}":
                                item.disabled = True
                                item.style = discord.ButtonStyle.secondary
                                item.label = "Claimed"
                        try:
                            await intx.message.edit(view=view)
                        except discord.NotFound:
                            pass
                    else:
                        await intx.response.send_message("❌ Could not claim reward. You may have already claimed it.", ephemeral=True)

                btn.callback = claim_weekly_callback
                view.add_item(btn)

        await interaction.followup.send(embed=embed, view=view)


async def setup(bot):
    await bot.add_cog(ObjectivesCog(bot))
