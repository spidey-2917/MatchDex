---
name: MatchDex Knowledge Base
description: Comprehensive knowledge about the MatchDex bot, its Django backend architecture, database schema, commands, and admin procedures.
---
# MatchDex Bot Knowledge

## Overview
MatchDex is a Discord bot focused on football (soccer) card collection, trading, and team building. It uses the Django Web Framework for its backend and database management (sqlite).

## Architecture
- **Discord Bot**: Uses a cog-based structure (`WagerCog`, `AdminCog`, `MdSettingsCog`, etc.) to organize commands.
- **Backend**: Django ORM. Models are defined in `core/models.py`. 
- **Database**: SQLite (`db.sqlite3`).
- **Images**: Dynamic image generation using Pillow. Empty templates are stored and text/stats are drawn on top of them on the fly.

## Key Models
- `DiscordUser`: Stores user data, points, match stats (wins, losses, draws), inventory visibility, and timestamps for pack cooldowns.
- `CardTemplate`: The base definition of a card. Has attributes like Name, Position, Attack Stat, Defence Stat, OVR, Rarity, Event, Club, and Card Type (Base, Icon, Event, Premium).
- `UserCard`: An instance of a card owned by a `DiscordUser`. Linked via foreign keys to `DiscordUser` and `CardTemplate`. Has a unique base36 `card_id`.
- `FavouriteCard`: Tracks cards users have favourited.

## Card System Mechanics
- **OVR & Rarity Calculation**: When a new `CardTemplate` is saved in the Django Admin panel, a signal automatically calculates the OVR (highest of attack or defence) and assigns the correct Rarity based on predefined weight ranges.
- **Rarities**: Common (I, II, III), Uncommon (I, II, III), Rare (I, II, III), Epic (I, II, III), Legendary (I, II, III), Premium.

## Core Features
1. **Packs**: Users can open daily, weekly, event, and premium packs.
2. **Squad Management**: Build a team, pick formations, auto-fill highest rated cards.
3. **Matches & Wagers**: Challenge users to standard matches or high-stakes wagers using cards.
4. **Trading**: Bulk and single card trading with other users.

## Administration
- **Django Admin Panel**: The primary way to add new cards. Adding a card automatically injects it into the bot's drop pool without requiring a bot restart.
- **Bulk Imports**: Cards can be bulk imported via CSV/JSON using a management command (`python manage.py import_cards ...`).
- **Bot Admin Commands**: Spawn cards manually, give cards directly, reload blacklist, manage permissions.

## Best Practices & Gotchas
- When adding cards, do not manually specify OVR or Rarity; let the pre_save signal in `core/models.py` handle it.
- Card templates don't need individual images; they use base rarity templates dynamically painted with text.
- User data updates (like points or cooldowns) are handled via `DiscordUser` model instances.
