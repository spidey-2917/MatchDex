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


def get_random_rarity():
    """Pick a rarity using the weights from config.yml."""
    rarities = list(settings.rarity_weights.keys())
    weights = list(settings.rarity_weights.values())
    return random.choices(rarities, weights=weights, k=1)[0]


def get_random_card_by_rarity(rarity):
    # Event cards now have a small chance to appear alongside BASE and ICON
    chosen_type = random.choices(
        ["BASE", "ICON", "EVENT"], weights=[90, 7, 3], k=1
    )[0]
    cards = CardTemplate.objects.filter(card_type=chosen_type, rarity=rarity)
    if not cards.exists():
        # Fallback: any card of this type regardless of rarity
        fallback = CardTemplate.objects.filter(card_type=chosen_type)
        if fallback.exists():
            return fallback.order_by("?").first()
        # Last resort: any BASE card
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
            Q(at2_id=user_card_id) | Q(at3_id=user_card_id) | Q(sub1_id=user_card_id) | \
            Q(sub2_id=user_card_id) | Q(sub3_id=user_card_id)

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
            if lineup.sub1_id == user_card_id: lineup.sub1 = None
            if lineup.sub2_id == user_card_id: lineup.sub2 = None
            if lineup.sub3_id == user_card_id: lineup.sub3 = None
            lineup.save()

    await clear()
