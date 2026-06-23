import logging
import sys
from datetime import datetime, timezone

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from core.settings import settings

log = logging.getLogger("matchdex.info")


def mention_command(cmd: app_commands.Command | app_commands.Group) -> str:
    """Return a clickable mention if available, otherwise a code-styled fallback."""
    if "mention" in cmd.extras:
        return cmd.extras["mention"]
    return f"`/{cmd.qualified_name}`"


class InfoCog(commands.Cog, name="Info"):
    """Bot information, help menu, favourites, and collection completion."""

    def __init__(self, bot):
        self.bot = bot

    # ── /about ───────────────────────────────────────────────
    @app_commands.command(name="about", description="Get information about this bot")
    async def about(self, interaction: discord.Interaction):
        await interaction.response.defer()

        @sync_to_async
        def get_stats():
            from core.models import CardTemplate, DiscordUser, UserCard

            return {
                "players": DiscordUser.objects.count(),
                "cards_caught": UserCard.objects.count(),
                "templates": CardTemplate.objects.filter().count(),
            }

        stats = await get_stats()

        # Uptime
        if self.bot.startup_time:
            delta = datetime.now(timezone.utc) - self.bot.startup_time
            hours, remainder = divmod(int(delta.total_seconds()), 3600)
            minutes, seconds = divmod(remainder, 60)
            if hours >= 24:
                days, hours = divmod(hours, 24)
                uptime = f"{days}d {hours}h {minutes}m"
            else:
                uptime = f"{hours}h {minutes}m {seconds}s"
        else:
            uptime = "N/A"

        # Build invite link with your exact custom permissions
        invite = f"https://discord.com/oauth2/authorize?client_id={self.bot.application_id}&permissions=2147863616&integration_type=0&scope=bot+applications.commands"

        embed = discord.Embed(
            title=f"⚽ {settings.bot_name}",
            color=discord.Color.green(),
        )

        lines = [
            settings.about_description,
            "",
            f"🟢 Online for **{uptime}**",
            "",
            f"**{stats['templates']:,}** {settings.plural_collectible_name} to collect",
            f"**{stats['players']:,}** managers who caught "
            f"**{stats['cards_caught']:,}** {settings.plural_collectible_name}",
            f"**{len(self.bot.guilds):,}** servers playing",
            "",
        ]

        # Links row
        link_parts = [f"[Invite Me]({invite})"]
        if settings.discord_invite:
            link_parts.append(f"[Support Server]({settings.discord_invite})")
        if settings.github_link:
            link_parts.append(f"[GitHub]({settings.github_link})")
        lines.append(" • ".join(link_parts))

        # Legal row
        legal = []
        if settings.terms_of_service:
            legal.append(f"[Terms of Service]({settings.terms_of_service})")
        if settings.privacy_policy:
            legal.append(f"[Privacy Policy]({settings.privacy_policy})")
        if legal:
            lines.append(" • ".join(legal))

        embed.description = "\n".join(lines)

        assert self.bot.user
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        v = sys.version_info
        embed.set_footer(
            text=f"Python {v.major}.{v.minor}.{v.micro} • discord.py {discord.__version__}"
        )

        await interaction.followup.send(embed=embed)

    # ── /help ────────────────────────────────────────────────
    @app_commands.command(name="help", description="Overview of all available commands")
    async def help(self, interaction: discord.Interaction):
        assert self.bot.user
        embed = discord.Embed(
            title=f"{settings.bot_name} — Help",
            color=discord.Color.blurple(),
        )
        embed.set_thumbnail(url=self.bot.user.display_avatar.url)

        for cog in self.bot.cogs.values():
            # Skip admin cog from public help
            if cog.qualified_name in ("Admin", "AdminCog"):
                continue

            content = ""
            for cmd in cog.walk_app_commands():
                content += f"{mention_command(cmd)}: {cmd.description}\n"

            if not content:
                continue

            # Split into 1024-char chunks for embed field limits
            while content:
                chunk = content[:1024]
                content = content[1024:]
                embed.add_field(
                    name=(
                        cog.qualified_name
                        if len(embed.fields) == 0 or content
                        else cog.qualified_name
                    ),
                    value=chunk,
                    inline=False,
                )

        await interaction.response.send_message(embed=embed)

    # ── /shop ────────────────────────────────────────────────
    @app_commands.command(name="shop", description="Get a link to the MatchDex store")
    async def shop(self, interaction: discord.Interaction):
        message_content = (
            "Checkout the store for exciting packs and premium cards follow the link below : \n"
            "https://ko-fi.com/matchdex\n"
            "can mail issues to \n"
            "matchdexadministration@gmail.com"
        )
        try:
            await interaction.user.send(message_content)
            await interaction.response.send_message("I've sent you a DM with the shop details!", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("I couldn't send you a DM. Please check your privacy settings!\n\n" + message_content, ephemeral=True)

    # ── /favourite ───────────────────────────────────────────
    @app_commands.command(
        name="favourite",
        description="Toggle a card as favourite (shows a ❤️ in your collection)",
    )
    async def favourite(self, interaction: discord.Interaction, card_id: str):
        from core.models import DiscordUser, FavouriteCard, UserCard

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        @sync_to_async
        def toggle():
            try:
                card = UserCard.objects.select_related("template").get(
                    card_id=card_id, owner=user
                )
            except UserCard.DoesNotExist:
                return None, False

            fav = FavouriteCard.objects.filter(owner=user, card=card).first()
            if fav:
                fav.delete()
                return card.template.name, False
            else:
                # Enforce limit
                count = FavouriteCard.objects.filter(owner=user).count()
                if count >= 50:
                    return "LIMIT", True
                FavouriteCard.objects.create(owner=user, card=card)
                return card.template.name, True

        name, is_fav = await toggle()

        if name is None:
            await interaction.response.send_message(
                f"You don't own a card with ID `{card_id}`.", ephemeral=True
            )
        elif name == "LIMIT":
            await interaction.response.send_message(
                "You've hit the **50 favourite** limit. " "Unfavourite a card first.",
                ephemeral=True,
            )
        elif is_fav:
            await interaction.response.send_message(
                f"❤️ **{name}** is now a favourite!", ephemeral=True
            )
        else:
            await interaction.response.send_message(
                f"💔 **{name}** removed from favourites.", ephemeral=True
            )

    # ── /completion ──────────────────────────────────────────
    @app_commands.command(
        name="completion",
        description="Show how much of the card collection you've completed",
    )
    async def completion(self, interaction: discord.Interaction, show_missing: bool = False):
        from core.models import CardTemplate, DiscordUser, UserCard

        user, _ = await DiscordUser.objects.aget_or_create(
            discord_id=interaction.user.id,
            defaults={"username": interaction.user.name},
        )

        @sync_to_async
        def get_completion():
            total = CardTemplate.objects.count()
            if total == 0:
                return 0, 0, {}

            owned_ids = set(
                UserCard.objects.filter(owner=user)
                .values_list("template_id", flat=True)
                .distinct()
            )

            # Per card-type breakdown
            from django.db.models import Count

            types = dict(
                CardTemplate.objects.values_list("card_type")
                .annotate(c=Count("id"))
                .values_list("card_type", "c")
            )
            owned_by_type = {}
            for ct, ct_total in types.items():
                ct_owned = (
                    UserCard.objects.filter(owner=user, template__card_type=ct)
                    .values("template_id")
                    .distinct()
                    .count()
                )
                owned_by_type[ct] = (ct_owned, ct_total)

            return owned_ids, total, owned_by_type

        owned_ids, total, by_type = await get_completion()
        owned = len(owned_ids)

        if total == 0:
            await interaction.response.send_message(
                "No cards exist in the game yet!", ephemeral=True
            )
            return

        pct = (owned / total) * 100
        bar_filled = round(pct / 5)
        bar_empty = 20 - bar_filled
        bar = "█" * bar_filled + "░" * bar_empty

        embed = discord.Embed(
            title=f"📊 {interaction.user.name}'s Collection",
            color=discord.Color.teal(),
        )
        embed.description = (
            f"**{owned}** / **{total}** unique {settings.plural_collectible_name}\n"
            f"`{bar}` **{pct:.1f}%**"
        )

        # Type breakdown
        type_labels = {
            "BASE": "Base",
            "ICON": "Icon",
            "EVENT": "Event",
            "PREMIUM": "Premium",
        }
        breakdown = []
        for ct, label in type_labels.items():
            if ct in by_type:
                ct_owned, ct_total = by_type[ct]
                ct_pct = (ct_owned / ct_total * 100) if ct_total > 0 else 0
                breakdown.append(f"**{label}**: {ct_owned}/{ct_total} ({ct_pct:.0f}%)")
        if breakdown:
            embed.add_field(
                name="Breakdown by Type",
                value="\n".join(breakdown),
                inline=False,
            )

        embed.set_footer(text="Catch 'em all!")

        if show_missing:
            @sync_to_async
            def get_missing():
                return list(CardTemplate.objects.exclude(id__in=owned_ids).order_by('-ovr'))

            missing_cards = await get_missing()
            if missing_cards:
                lines = [f"Missing Cards for {interaction.user.name} (Sorted by OVR)\n"]
                for c in missing_cards:
                    event_str = f" [{c.event_name}]" if c.event_name else ""
                    lines.append(f"{c.ovr} OVR | {c.name}{event_str} | {c.rarity}")
                
                import io
                file_bytes = io.BytesIO("\n".join(lines).encode("utf-8"))
                missing_file = discord.File(fp=file_bytes, filename=f"missing_cards_{interaction.user.name}.txt")
                await interaction.response.send_message(embed=embed, file=missing_file)
            else:
                await interaction.response.send_message(content="You have collected all cards!", embed=embed)
        else:
            await interaction.response.send_message(embed=embed)


async def setup(bot):
    await bot.add_cog(InfoCog(bot))
