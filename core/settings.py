"""
Matchdex bot settings — loaded from config.yml on startup.

Inspired by BallsDex's centralised configuration, but adapted for
Django + Matchdex's own feature set. Every setting has a sane default
so the bot can start even with a minimal (or missing) config file.
"""

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

log = logging.getLogger("matchdex.settings")


@dataclass
class Settings:
    # ── Identity ─────────────────────────────────────────────
    bot_name: str = "Matchdex"
    about_description: str = (
        "Collect football player cards, build your dream team, "
        "and battle against other managers on Discord!"
    )
    collectible_name: str = "player card"
    plural_collectible_name: str = "player cards"

    # ── Links ────────────────────────────────────────────────
    github_link: str = ""
    discord_invite: str = ""
    terms_of_service: str = ""
    privacy_policy: str = ""

    # ── Admin ────────────────────────────────────────────────
    admin_guild_ids: list[int] = field(default_factory=list)
    co_admins: list[int] = field(default_factory=list)

    # ── Spawn ────────────────────────────────────────────────
    spawn_chance_range: tuple[int, int] = (20, 50)
    spawn_cooldown_seconds: int = 900
    spawn_max_interval: int = 1800
    spawn_messages: list[str] = field(
        default_factory=lambda: ["A wild player card appeared!"]
    )
    catch_button_label: str = "Catch"

    # ── Rarity ───────────────────────────────────────────────
    rarity_weights: dict[str, float] = field(
        default_factory=lambda: {
            "Common": 0.70,
            "Uncommon": 0.20,
            "Rare": 0.08,
            "Epic": 0.015,
            "Legendary": 0.005,
        }
    )

    # ── Packs ────────────────────────────────────────────────
    pack_cooldowns: dict[str, int] = field(
        default_factory=lambda: {
            "daily": 1,
            "weekly": 7,
            "event": 7,
            "premium": 2,
        }
    )

    # ── Error logging ────────────────────────────────────────
    error_log_channel: Optional[int] = None

    # ── Packages ─────────────────────────────────────────────
    packages: list[str] = field(
        default_factory=lambda: [
            "core.bot_logic.AdminCog",
            "core.bot_logic.GeneralCog",
            "core.bot_logic.GuildConfigCog",
            "core.bot_logic.InfoCog",
            "core.bot_logic.MatchCog",
            "core.bot_logic.MdSettingsCog",
            "core.bot_logic.PackCog",
            "core.bot_logic.SpawningCog",
            "core.bot_logic.TeamCog",
            "core.bot_logic.TradeCog",
            "core.bot_logic.WagerCog",
        ]
    )


# Singleton used everywhere via `from core.settings import settings`
settings = Settings()


def read_settings(path: Optional[Path] = None):
    """Load values from config.yml into the global settings singleton."""
    if path is None:
        path = Path("config.yml")

    if not path.exists():
        log.warning("config.yml not found — running with defaults.")
        return

    content = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    settings.bot_name = content.get("bot-name", settings.bot_name)
    settings.about_description = content.get(
        "about-description", settings.about_description
    )
    settings.collectible_name = content.get(
        "collectible-name", settings.collectible_name
    )
    settings.plural_collectible_name = content.get(
        "plural-collectible-name", settings.plural_collectible_name
    )

    settings.github_link = content.get("github-link", settings.github_link)
    settings.discord_invite = content.get("discord-invite", settings.discord_invite)
    settings.terms_of_service = content.get(
        "terms-of-service", settings.terms_of_service
    )
    settings.privacy_policy = content.get("privacy-policy", settings.privacy_policy)

    admin = content.get("admin", {})
    settings.admin_guild_ids = admin.get("guild-ids") or []
    settings.co_admins = admin.get("co-admins") or []

    spawn = content.get("spawn", {})
    chance = spawn.get("chance-range", list(settings.spawn_chance_range))
    settings.spawn_chance_range = tuple(chance)
    settings.spawn_cooldown_seconds = spawn.get(
        "cooldown-seconds", settings.spawn_cooldown_seconds
    )
    settings.spawn_max_interval = spawn.get(
        "max-interval-seconds", settings.spawn_max_interval
    )
    settings.spawn_messages = spawn.get("spawn-messages", settings.spawn_messages)
    settings.catch_button_label = spawn.get(
        "catch-button-label", settings.catch_button_label
    )

    settings.rarity_weights = content.get("rarity-weights", settings.rarity_weights)

    packs = content.get("packs", {})
    for key in ("daily", "weekly", "event", "premium"):
        if key in packs:
            settings.pack_cooldowns[key] = packs[key]

    settings.error_log_channel = content.get("error-log-channel") or None
    settings.packages = content.get("packages", settings.packages)

    log.info("Settings loaded from %s", path)
