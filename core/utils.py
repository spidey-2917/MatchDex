import io
import os
import random

import discord
from asgiref.sync import sync_to_async
from django.db import models
from discord import app_commands
from django.conf import settings as django_settings
from PIL import Image, ImageDraw, ImageFont

from core.settings import settings

from .models import CardTemplate, DiscordUser, UserCard


async def player_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    from core.models import DiscordUser, UserCard

    user, _ = await DiscordUser.objects.aget_or_create(
        discord_id=interaction.user.id, defaults={"username": interaction.user.name}
    )

    @sync_to_async
    def get_choices():
        qs = UserCard.objects.filter(owner=user).select_related("template")
        if current:
            # Search by ID or Name
            qs = qs.filter(
                models.Q(card_id__icontains=current)
                | models.Q(template__name__icontains=current)
            )
        return list(qs[:25])

    cards = await get_choices()
    choices = []
    for c in cards:
        name = c.template.display_name
        label = f"#{c.card_id} {name} ({c.template.ovr} OVR)"
        choices.append(app_commands.Choice(name=label, value=c.card_id))
    return choices


async def template_autocomplete(
    interaction: discord.Interaction, current: str
) -> list[app_commands.Choice[str]]:
    from core.models import CardTemplate

    @sync_to_async
    def get_choices():
        qs = CardTemplate.objects.all()
        if current:
            qs = qs.filter(name__icontains=current)
        return list(qs[:25])

    templates = await get_choices()
    return [
        app_commands.Choice(
            name=f"{t.display_name} ({t.ovr} OVR)",
            value=t.name if not t.event_name or t.event_name.lower() == "base" else f"{t.name}|{t.event_name}"
        )
        for t in templates
    ]


SOFIFA_POS_MAP = {
    "ST": "ST",
    "LS": "ST",
    "RS": "ST",
    "CF": "ST",
    "RW": "RW",
    "RF": "RW",
    "LW": "LW",
    "LF": "LW",
    "CAM": "CAM",
    "LAM": "CAM",
    "RAM": "CAM",
    "CM": "CM",
    "LCM": "CM",
    "RCM": "CM",
    "CDM": "CDM",
    "LDM": "CDM",
    "RDM": "CDM",
    "CB": "CB",
    "LCB": "CB",
    "RCB": "CB",
    "LB": "LB",
    "LWB": "LB",
    "RB": "RB",
    "RWB": "RB",
    "GK": "GK",
}


def map_sofifa_pos(pos_str):
    """Maps a SoFIFA position string (e.g. 'CAM') to our model's choices."""
    return SOFIFA_POS_MAP.get(pos_str.upper(), "ST")


def get_drop_config(category):
    """
    Fetch the current drop rate configuration from the database.
    Falls back to config.yml settings if no database entries exist.
    """
    from .models import RateConfig

    config = RateConfig.objects.filter(category=category).first()
    if not config:
        # No config in DB at all — fall back to config.yml rarity weights
        return "RARITY", settings.rarity_weights

    rates = config.rates.all()

    if config.mode == "RARITY":
        if not rates.exists():
            return "RARITY", settings.rarity_weights
        weights = {r.rarity: r.weight for r in rates if r.rarity}
        return "RARITY", weights
    else:
        # mode == 'OVR'
        if not rates.exists():
            return "RARITY", settings.rarity_weights
        ovr_ranges = [
            (r.min_ovr, r.max_ovr, r.weight)
            for r in rates
            if r.min_ovr is not None and r.max_ovr is not None
        ]
        if not ovr_ranges:
            return "RARITY", settings.rarity_weights
        return "OVR", ovr_ranges


def pick_random_card(category, card_type_filter=None):
    """
    Pick a random card based on the current drop rate configuration.
    """
    mode, weights = get_drop_config(category)

    # 1. Choose card type (BASE, ICON, EVENT)
    if card_type_filter:
        if isinstance(card_type_filter, list):
            # If multiple types allowed, we still use the default weighted distribution
            # but filtered for the allowed types.
            possible = ["BASE", "ICON", "EVENT"]
            all_weights = [90, 7, 3]
            
            filtered_types = []
            filtered_weights = []
            for t, w in zip(possible, all_weights):
                if t in card_type_filter:
                    filtered_types.append(t)
                    filtered_weights.append(w)
            
            if not filtered_types:
                chosen_type = random.choice(card_type_filter)
            else:
                chosen_type = random.choices(filtered_types, weights=filtered_weights, k=1)[0]
        else:
            chosen_type = card_type_filter
    else:
        chosen_type = random.choices(["BASE", "ICON", "EVENT"], weights=[90, 7, 3], k=1)[0]

    # 2. Pick card based on mode
    spawnable_rarities = list(settings.rarity_weights.keys())

    if mode == "RARITY":
        rarity = random.choices(list(weights.keys()), weights=list(weights.values()), k=1)[
            0
        ]
        qs = CardTemplate.objects.filter(
            card_type=chosen_type, rarity=rarity
        ).filter(rarity__in=spawnable_rarities)
        if not qs.exists():
            # Fallback: keep type but ignore rarity (still filtering spawnable)
            fallback = CardTemplate.objects.filter(
                card_type=chosen_type, rarity__in=spawnable_rarities
            )
            if fallback.exists():
                return fallback.order_by("?").first()
            # Last resort: any BASE card (still filtering spawnable)
            return (
                CardTemplate.objects.filter(card_type="BASE", rarity__in=spawnable_rarities)
                .order_by("?")
                .first()
            )
        return qs.order_by("?").first()
    else:
        # mode == 'OVR'
        chosen_range = random.choices(weights, weights=[w[2] for w in weights], k=1)[0]
        min_ovr, max_ovr = chosen_range[0], chosen_range[1]
        qs = CardTemplate.objects.filter(
            card_type=chosen_type,
            ovr__gte=min_ovr,
            ovr__lte=max_ovr,
            rarity__in=spawnable_rarities,
        )
        if not qs.exists():
            # Fallback: keep type but ignore OVR range (still filtering spawnable)
            fallback = CardTemplate.objects.filter(
                card_type=chosen_type, rarity__in=spawnable_rarities
            )
            if fallback.exists():
                return fallback.order_by("?").first()
            # Last resort: any BASE card (still filtering spawnable)
            return (
                CardTemplate.objects.filter(card_type="BASE", rarity__in=spawnable_rarities)
                .order_by("?")
                .first()
            )
        return qs.order_by("?").first()


def get_random_rarity():
    """Deprecated: Use pick_random_card instead. Kept for minimal compatibility."""
    rarities = list(settings.rarity_weights.keys())
    weights = list(settings.rarity_weights.values())
    return random.choices(rarities, weights=weights, k=1)[0]


def get_random_card_by_rarity(rarity):
    """Deprecated: Use pick_random_card instead. Kept for minimal compatibility."""
    chosen_type = random.choices(["BASE", "ICON", "EVENT"], weights=[90, 7, 3], k=1)[0]
    cards = CardTemplate.objects.filter(card_type=chosen_type, rarity=rarity)
    if not cards.exists():
        fallback = CardTemplate.objects.filter(card_type=chosen_type)
        if fallback.exists():
            return fallback.order_by("?").first()
        return CardTemplate.objects.filter(card_type="BASE").order_by("?").first()
    return cards.order_by("?").first()


def generate_card_image(card_template):
    # If a custom image was uploaded in the admin panel, use it directly
    if card_template.image_base and card_template.image_base.name:
        try:
            image_path = os.path.join(
                django_settings.MEDIA_ROOT, card_template.image_base.name
            )
            img = Image.open(image_path)
            img = img.convert("RGB")

            # Resize large images to prevent 413 Payload Too Large errors
            img.thumbnail((800, 1200), Image.Resampling.LANCZOS)

            buffer = io.BytesIO()
            img.save(buffer, format="PNG", optimize=True)
            buffer.seek(0)
            return buffer
        except (OSError, IOError, FileNotFoundError):
            pass  # Fall through to generated card

    # Fallback: generated text-based card
    width, height = 400, 600
    rarity_colors = {
        "Common": (169, 169, 169),
        "Uncommon": (34, 139, 34),
        "Rare": (30, 144, 255),
        "Epic": (138, 43, 226),
        "Legendary": (255, 215, 0),
        "Premium": (220, 20, 60),
    }

    color = rarity_colors.get(card_template.rarity, (255, 255, 255))
    img = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(img)

    try:
        font_large = ImageFont.truetype("arial.ttf", 40)
        font_small = ImageFont.truetype("arial.ttf", 20)
    except (OSError, IOError):
        font_large = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((20, 20), card_template.name, fill=(0, 0, 0), font=font_large)
    draw.text((20, 80), f"OVR: {card_template.ovr}", fill=(0, 0, 0), font=font_large)
    draw.text(
        (20, 140), f"POS: {card_template.position}", fill=(0, 0, 0), font=font_small
    )
    draw.text(
        (20, 170),
        f"ATT: {card_template.attack_stat}  DEF: {card_template.defence_stat}",
        fill=(0, 0, 0),
        font=font_small,
    )
    draw.text((20, 200), f"CLUB: {card_template.club}", fill=(0, 0, 0), font=font_small)
    draw.text((20, 550), card_template.rarity.upper(), fill=(0, 0, 0), font=font_large)

    buffer = io.BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer


async def catch_card(user_id, card_template):
    user, created = await DiscordUser.objects.aget_or_create(discord_id=user_id)
    user_card = await UserCard.objects.acreate(owner=user, template=card_template)
    user.cards_collected += 1
    await user.asave()
    return user_card


def to_base36(n: int) -> str:
    """Convert an integer to a base36 string."""
    chars = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    if n == 0:
        return "0"
    res = ""
    while n > 0:
        n, rem = divmod(n, 36)
        res = chars[rem] + res
    return res


def from_base36(s: str) -> int:
    """Convert a base36 string to an integer."""
    return int(s, 36)



async def clear_card_from_lineups(user_card_id: int):
    """
    Find all Lineup records that reference this specific UserCard ID
    and set those slots to NULL.
    """
    from django.db.models import Q

    from .models import Lineup

    # Search across all 14 slots + 3 subs
    query = Q(gk_id=user_card_id) | Q(df1_id=user_card_id) | Q(df2_id=user_card_id) | \
            Q(df3_id=user_card_id) | Q(df4_id=user_card_id) | Q(df5_id=user_card_id) | \
            Q(md1_id=user_card_id) | Q(md2_id=user_card_id) | Q(md3_id=user_card_id) | \
            Q(md4_id=user_card_id) | Q(md5_id=user_card_id) | Q(at1_id=user_card_id) | \
            Q(at2_id=user_card_id) | Q(at3_id=user_card_id) | Q(at4_id=user_card_id) | \
            Q(sub1_id=user_card_id) | Q(sub2_id=user_card_id) | Q(sub3_id=user_card_id)

    @sync_to_async
    def clear():
        lineups = Lineup.objects.filter(query)
        for lineup in lineups:
            if lineup.gk_id == user_card_id: lineup.gk = None
            if lineup.df1_id == user_card_id: lineup.df1 = None
            if lineup.df2_id == user_card_id: lineup.df2 = None
            if lineup.df3_id == user_card_id: lineup.df3 = None
            if lineup.df4_id == user_card_id: lineup.df4 = None
            if lineup.df5_id == user_card_id: lineup.df5 = None
            if lineup.md1_id == user_card_id: lineup.md1 = None
            if lineup.md2_id == user_card_id: lineup.md2 = None
            if lineup.md3_id == user_card_id: lineup.md3 = None
            if lineup.md4_id == user_card_id: lineup.md4 = None
            if lineup.md5_id == user_card_id: lineup.md5 = None
            if lineup.at1_id == user_card_id: lineup.at1 = None
            if lineup.at2_id == user_card_id: lineup.at2 = None
            if lineup.at3_id == user_card_id: lineup.at3 = None
            if lineup.at4_id == user_card_id: lineup.at4 = None
            if lineup.sub1_id == user_card_id: lineup.sub1 = None
            if lineup.sub2_id == user_card_id: lineup.sub2 = None
            if lineup.sub3_id == user_card_id: lineup.sub3 = None
            lineup.save()

    await clear()


class SkipPageModal(discord.ui.Modal, title="Skip to Page"):
    page_input = discord.ui.TextInput(
        label="Enter Page Number",
        placeholder="e.g. 5",
        min_length=1,
        max_length=5,
    )

    def __init__(self, view, interaction):
        super().__init__()
        self.view = view
        self.original_interaction = interaction

    async def on_submit(self, interaction: discord.Interaction):
        try:
            target_page = int(self.page_input.value)
            total_pages = (self.view.total_cards + self.view.page_size - 1) // self.view.page_size
            
            if 1 <= target_page <= total_pages:
                self.view.page = target_page - 1
                await interaction.response.defer()
                await self.view.update_view(self.original_interaction)
            else:
                await interaction.response.send_message(f"Invalid page! Please enter a number between 1 and {total_pages}.", ephemeral=True)
        except ValueError:
            await interaction.response.send_message("Please enter a valid numerical page number.", ephemeral=True)


class CardListView(discord.ui.View):
    """
    Reusable base view for listing cards with pagination.
    Subclasses should override add_selection_menu to customize behavior.
    """
    def __init__(self, user_db, sort_by, bot, reverse=False, ephemeral=False):
        super().__init__(timeout=180)
        self.user_db = user_db
        self.sort_by = sort_by
        self.bot = bot
        self.reverse = reverse
        self.ephemeral = ephemeral
        self.page = 0
        self.page_size = 25
        self.total_cards = 0
        self.current_cards = []

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.user_db.discord_id:
            await interaction.response.send_message(
                "Only the user who opened this list can control it.", ephemeral=True
            )
            return False
        return True

    async def get_page_cards(self):
        from .models import UserCard
        @sync_to_async
        def fetch():
            qs = UserCard.objects.filter(owner=self.user_db).select_related("template")
            
            if self.sort_by == "ovr":
                order = ["-template__ovr", "-caught_at"]
                if self.reverse: order = ["template__ovr", "caught_at"]
                qs = qs.order_by(*order)
            elif self.sort_by == "rarity":
                order = ["-template__rarity", "-template__ovr"]
                if self.reverse: order = ["template__rarity", "template__ovr"]
                qs = qs.order_by(*order)
            elif self.sort_by == "type":
                order = ["-template__card_type", "-template__ovr"]
                if self.reverse: order = ["template__card_type", "template__ovr"]
                qs = qs.order_by(*order)
            elif self.sort_by == "date":
                order = ["-caught_at"]
                if self.reverse: order = ["caught_at"]
                qs = qs.order_by(*order)
            
            self.total_cards = qs.count()
            start = self.page * self.page_size
            end = start + self.page_size
            return list(qs[start:end])

        return await fetch()

    def add_selection_menu(self, cards):
        # To be overridden by subclasses
        pass

    def add_utility_buttons(self, interaction):
        # To be overridden by subclasses
        quit_btn = discord.ui.Button(label="Quit", style=discord.ButtonStyle.danger, row=1)
        async def cb_quit(it):
            await it.response.edit_message(content="🛑 List closed.", view=None)
            self.stop()
        quit_btn.callback = cb_quit
        self.add_item(quit_btn)

    async def update_view(self, interaction: discord.Interaction):
        self.current_cards = await self.get_page_cards()
        total_pages = (self.total_cards + self.page_size - 1) // self.page_size
        
        if not self.current_cards and self.page == 0:
            msg = "You have no cards!"
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=self.ephemeral)
            else:
                await interaction.response.send_message(msg, ephemeral=self.ephemeral)
            return

        self.clear_items()

        # Row 0: Pagination Controls
        first_btn = discord.ui.Button(label="<<", style=discord.ButtonStyle.secondary, disabled=(self.page == 0), row=0)
        prev_btn = discord.ui.Button(label="...", style=discord.ButtonStyle.primary, disabled=(self.page == 0), row=0)
        curr_page_btn = discord.ui.Button(label=str(self.page + 1), style=discord.ButtonStyle.primary, row=0)
        next_page_val = self.page + 2 if self.page + 1 < total_pages else "-"
        next_page_btn = discord.ui.Button(label=str(next_page_val), style=discord.ButtonStyle.secondary, disabled=(next_page_val == "-"), row=0)
        last_btn = discord.ui.Button(label=">>", style=discord.ButtonStyle.secondary, disabled=(self.page + 1 >= total_pages), row=0)

        async def cb_first(it):
            self.page = 0
            await it.response.defer()
            await self.update_view(interaction)
        async def cb_prev(it):
            self.page = max(0, self.page - 1)
            await it.response.defer()
            await self.update_view(interaction)
        async def cb_next(it):
            self.page = min(total_pages - 1, self.page + 1)
            await it.response.defer()
            await self.update_view(interaction)
        async def cb_last(it):
            self.page = total_pages - 1
            await it.response.defer()
            await self.update_view(interaction)

        first_btn.callback, prev_btn.callback, next_page_btn.callback, last_btn.callback = cb_first, cb_prev, cb_next, cb_last
        for btn in [first_btn, prev_btn, curr_page_btn, next_page_btn, last_btn]: self.add_item(btn)

        # Row 1: Utility
        skip_btn = discord.ui.Button(label="Skip to page...", style=discord.ButtonStyle.secondary, row=1)
        async def cb_skip(it): await it.response.send_modal(SkipPageModal(self, interaction))
        skip_btn.callback = cb_skip
        self.add_item(skip_btn)
        
        self.add_utility_buttons(interaction)

        # Row 2+: Selection
        self.add_selection_menu(self.current_cards)

        content = f"**{self.user_db.username}'s Cards**\n" \
                  f"Page {self.page + 1} of {max(1, total_pages)} ({self.total_cards} cards total)"

        if interaction.response.is_done():
            await interaction.edit_original_response(content=content, view=self)
        else:
            await interaction.response.send_message(content=content, view=self, ephemeral=self.ephemeral)
