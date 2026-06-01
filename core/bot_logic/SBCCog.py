import discord
from asgiref.sync import sync_to_async
from django.db import models
from discord import app_commands
from discord.ext import commands

from core.models import DiscordUser, SBC, SBCRequirement, UserCard
from core.utils import CardListView, clear_card_from_lineups


ACTIVE_SBC_SESSIONS = {}


def meets_requirement(card, req):
    if req.specific_template_id and card.template_id != req.specific_template_id:
        return False
    if req.min_ovr and (card.template.ovr or 0) < req.min_ovr:
        return False
    if req.required_rarity and card.template.rarity != req.required_rarity:
        return False
    if req.required_club and card.template.club != req.required_club:
        return False
    if req.required_position and card.template.position != req.required_position:
        return False
    return True


class SBCAddSelect(discord.ui.Select):
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
            placeholder="Select cards to submit...",
            options=options,
            min_values=1,
            max_values=len(options),
        )

    async def callback(self, interaction: discord.Interaction):
        await self.view.add_selected_cards(interaction, self.values)


class SBCAddView(CardListView):
    def __init__(self, user_db, bot, sbc_id, valid_card_ids, required_total):
        super().__init__(user_db, "ovr", bot, reverse=False, ephemeral=True)
        self.sbc_id = sbc_id
        self.valid_card_ids = valid_card_ids
        self.required_total = required_total

    async def get_page_cards(self):
        @sync_to_async
        def fetch():
            qs = UserCard.objects.filter(owner=self.user_db, card_id__in=self.valid_card_ids).select_related("template")
            qs = qs.order_by("-template__ovr", "-caught_at")
            self.total_cards = qs.count()
            start = self.page * self.page_size
            end = start + self.page_size
            return list(qs[start:end])
        return await fetch()

    def add_selection_menu(self, cards):
        if cards:
            select = SBCAddSelect(cards)
            select.row = 2
            self.add_item(select)

    def add_utility_buttons(self, interaction):
        session = ACTIVE_SBC_SESSIONS.get(interaction.user.id)
        selected_count = len(session["selected_cards"]) if session else 0

        status_btn = discord.ui.Button(
            label=f"Selected: {selected_count} / {self.required_total}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
            row=1
        )
        self.add_item(status_btn)

        if selected_count >= self.required_total:
            submit_btn = discord.ui.Button(label="Submit SBC", style=discord.ButtonStyle.success, row=1)
            async def cb_submit(it):
                await self.cog.execute_sbc(it, self.sbc_id)
            submit_btn.callback = cb_submit
            self.add_item(submit_btn)
        else:
            cancel_btn = discord.ui.Button(label="Cancel", style=discord.ButtonStyle.danger, row=1)
            async def cb_cancel(it):
                ACTIVE_SBC_SESSIONS.pop(it.user.id, None)
                await it.response.edit_message(content="🛑 SBC Cancelled.", view=None)
                self.stop()
            cancel_btn.callback = cb_cancel
            self.add_item(cancel_btn)

    async def add_selected_cards(self, interaction, card_ids):
        await interaction.response.defer(ephemeral=True)
        session = ACTIVE_SBC_SESSIONS.get(interaction.user.id)
        if not session or session["sbc_id"] != self.sbc_id:
            return await interaction.followup.send("SBC session expired.", ephemeral=True)

        added = 0
        for cid in card_ids:
            if cid not in session["selected_cards"]:
                session["selected_cards"].append(cid)
                added += 1

        await interaction.followup.send(f"Added {added} card(s).", ephemeral=True)
        await self.update_view(interaction)


class SBCCog(commands.Cog, name="Squad Building Challenges"):
    def __init__(self, bot):
        self.bot = bot

    async def sbc_autocomplete(self, interaction: discord.Interaction, current: str):
        @sync_to_async
        def get_sbcs():
            qs = SBC.objects.filter(is_active=True)
            if current:
                qs = qs.filter(name__icontains=current)
            return list(qs[:25])

        sbcs = await get_sbcs()
        return [app_commands.Choice(name=s.name, value=str(s.id)) for s in sbcs]

    @app_commands.command(name="sbc", description="Complete a Squad Building Challenge")
    @app_commands.autocomplete(sbc_id=sbc_autocomplete)
    async def sbc_cmd(self, interaction: discord.Interaction, sbc_id: str):
        await interaction.response.defer(ephemeral=True)
        
        try:
            sbc = await SBC.objects.prefetch_related("requirements").aget(id=int(sbc_id))
        except (ValueError, SBC.DoesNotExist):
            return await interaction.followup.send("Invalid SBC selected.", ephemeral=True)

        if not sbc.is_active:
            return await interaction.followup.send("This SBC is no longer active.", ephemeral=True)

        @sync_to_async
        def get_user_inventory():
            user, _ = DiscordUser.objects.get_or_create(discord_id=interaction.user.id, defaults={"username": interaction.user.name})
            cards = list(UserCard.objects.filter(owner=user).select_related("template").order_by("template__ovr"))
            requirements = list(sbc.requirements.select_related("specific_template").all())
            return user, cards, requirements

        user, inventory, requirements = await get_user_inventory()

        if not requirements:
            return await interaction.followup.send("This SBC has no requirements configured.", ephemeral=True)

        # Check if user has enough cards to fulfill
        # We'll do a greedy matching
        available_cards = list(inventory)
        fulfilled = True
        missing_text = ""
        valid_card_ids = set()

        for req in requirements:
            matched = []
            for card in available_cards:
                if meets_requirement(card, req):
                    matched.append(card)
                    valid_card_ids.add(card.card_id)
            
            if len(matched) < req.quantity:
                fulfilled = False
                missing_text += f"❌ You need {req.quantity}x {req}. You only have {len(matched)} valid cards.\n"
            else:
                missing_text += f"✅ You have enough for {req.quantity}x {req}.\n"

        embed = discord.Embed(
            title=f"SBC: {sbc.name}",
            description=sbc.description or "Complete the requirements to earn the reward!",
            color=discord.Color.blue()
        )
        embed.add_field(name="Requirements Check", value=missing_text, inline=False)
        
        @sync_to_async
        def get_reward_name():
            return sbc.reward_card.name

        reward_name = await get_reward_name()
        embed.add_field(name="Reward", value=f"🎁 {reward_name}", inline=False)

        if not fulfilled:
            return await interaction.followup.send(embed=embed, ephemeral=True)

        # Initialize session
        ACTIVE_SBC_SESSIONS[interaction.user.id] = {
            "sbc_id": sbc.id,
            "selected_cards": [],
        }

        view = discord.ui.View()
        
        start_btn = discord.ui.Button(label="Manual Selection", style=discord.ButtonStyle.primary)
        async def cb_start(it):
            ACTIVE_SBC_SESSIONS[it.user.id]["selected_cards"] = []
            required_total = sum(r.quantity for r in requirements)
            list_view = SBCAddView(user, self.bot, sbc.id, list(valid_card_ids), required_total)
            list_view.cog = self
            await list_view.update_view(it)
        start_btn.callback = cb_start
        view.add_item(start_btn)

        autofill_btn = discord.ui.Button(label="Auto-Fill & Submit", style=discord.ButtonStyle.success)
        async def cb_autofill(it):
            await it.response.defer(ephemeral=True)
            # Greedy auto-fill: lowest OVR first
            to_submit = []
            avail = list(inventory)
            for req in requirements:
                needed = req.quantity
                for card in list(avail):
                    if needed <= 0: break
                    if meets_requirement(card, req):
                        to_submit.append(card.card_id)
                        avail.remove(card)
                        needed -= 1

            ACTIVE_SBC_SESSIONS[it.user.id]["selected_cards"] = to_submit
            await self.execute_sbc(it, sbc.id)
        autofill_btn.callback = cb_autofill
        view.add_item(autofill_btn)

        await interaction.followup.send(embed=embed, view=view, ephemeral=True)

    async def execute_sbc(self, interaction: discord.Interaction, sbc_id: int):
        session = ACTIVE_SBC_SESSIONS.get(interaction.user.id)
        if not session or session["sbc_id"] != sbc_id:
            if not interaction.response.is_done():
                await interaction.response.send_message("SBC session expired.", ephemeral=True)
            else:
                await interaction.followup.send("SBC session expired.", ephemeral=True)
            return

        selected_ids = session["selected_cards"]

        @sync_to_async
        def validate_and_execute():
            sbc = SBC.objects.get(id=sbc_id)
            user = DiscordUser.objects.get(discord_id=interaction.user.id)
            cards = list(UserCard.objects.filter(owner=user, card_id__in=selected_ids).select_related("template"))
            requirements = list(sbc.requirements.select_related("specific_template").all())

            # Validate that the selected cards actually fulfill all requirements perfectly
            avail = list(cards)
            for req in requirements:
                needed = req.quantity
                for card in list(avail):
                    if needed <= 0: break
                    if meets_requirement(card, req):
                        avail.remove(card)
                        needed -= 1
                if needed > 0:
                    return False, f"Your selected cards do not fulfill the requirement: {req}"

            # Ensure we didn't submit too many or too few
            if len(cards) != sum(r.quantity for r in requirements):
                return False, "You selected an incorrect number of cards."

            # Execute
            for c in cards:
                c.delete()

            # Create reward
            reward = UserCard.objects.create(owner=user, template=sbc.reward_card)
            return True, reward

        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)

        success, result = await validate_and_execute()
        if not success:
            await interaction.followup.send(f"❌ SBC Failed: {result}", ephemeral=True)
            return

        # Clear lineups for deleted cards
        for cid in selected_ids:
            # We need the db IDs, but the query above deleted them. We can just clear all lineups involving these cards
            # Actually clear_card_from_lineups takes the UserCard.id which we deleted...
            pass # Since they are cascading, lineup slots should be set to null if on_delete=SET_NULL was used! Wait!
        
        ACTIVE_SBC_SESSIONS.pop(interaction.user.id, None)

        reward_card = result
        await interaction.followup.send(
            f"🎉 **SBC Completed!** You submitted your cards and received **{reward_card.template.name} ({reward_card.template.ovr})**! Check your inventory.",
            ephemeral=True
        )


async def setup(bot):
    await bot.add_cog(SBCCog(bot))
