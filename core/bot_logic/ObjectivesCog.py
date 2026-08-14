import discord
from discord.ext import commands
from discord import app_commands
from core.models import DiscordUser
from core.objectives import get_or_assign_daily_objectives, claim_objective_reward

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
            return await interaction.followup.send("No objectives are currently configured for the bot.")
            
        embed = discord.Embed(
            title=f"🎯 Daily Objectives for {interaction.user.display_name}",
            color=discord.Color.gold(),
            description="Complete these tasks before the day ends (UTC) to earn rewards!"
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
                        # Remove button by editing original message
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

async def setup(bot):
    await bot.add_cog(ObjectivesCog(bot))
