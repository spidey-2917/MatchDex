# 🏆 MatchDex — Major Updates & Feature Changelog

**Release Date:** October 2026  
**System Status:** Verified & Migrated (`0 issues`)  
**Database Migration:** `0034_weeklyobjectivetemplate_and_more`

---

## 📋 Table of Contents
1. [👔 Manager System Overhaul](#1--manager-system-overhaul)
2. [🛡️ Club Logos & Crests](#2-️-club-logos--crests)
3. [⚡ Card Upgrades & Duplicate Burning](#3--card-upgrades--duplicate-burning)
4. [✨ Refractor Card Art Variations](#4--refractor-card-art-variations)
5. [🎲 Spawning Architecture & Community `/drop`](#5--spawning-architecture--community-drop)
6. [🔒 Admin Spawning Safety & Commands](#6--admin-spawning-safety--commands)
7. [📅 Weekly Objectives Engine](#7--weekly-objectives-engine)
8. [❤️ Favorite Cards Showcase](#8-️-favorite-cards-showcase)
9. [🗳️ Top.gg Voting Integration](#9-️-topgg-voting-integration)
10. [🎓 Beginner Tutorial & 11-Card Starter Pack](#10--beginner-tutorial--11-card-starter-pack)
11. [🎨 Event Logos & Custom Emojis](#11--event-logos--custom-emojis)
12. [📊 Verification & Testing Summary](#12--verification--testing-summary)

---

## 1. 👔 Manager System Overhaul

### Configurable Boosts in Django Admin
Managers are now fully customizable coaches that grant tactical synergy boosts in **Quick Sim matches** based on both lineup formation and match tactics.

- **Formation Synergy:**
  - `preferred_formation`: e.g. `433`, `442`, `352`, or `ALL`.
  - `formation_boost_type`: `ATTACK`, `DEFENCE`, `BALANCED`, `COUNTER`, or `NONE`.
  - `formation_boost_pct`: boost percentage (e.g. `+5%`, `+8%`).
- **Tactic Synergy:**
  - `preferred_tactic`: `balanced`, `attacking`, `defensive`, `possession`, `counter`, or `ALL`.
  - `tactic_boost_type`: `ATTACK`, `DEFENCE`, `BALANCED`, `COUNTER`, or `NONE`.
  - `tactic_boost_pct`: boost percentage (e.g. `+5%`).

### Manager Inventory & Single Equip
- **Inventory Collection:** Tracked per user in `UserOwnedManager`. Users can collect multiple managers.
- **Equipped Manager:** Managed 1-to-1 via `UserManager`. Users can equip exactly one manager at a time.
- **Discord Commands:**
  - `/manager view` — Displays your active manager, formation/tactic synergy, and boost status.
  - `/manager list` — Lists all managers currently in your inventory.
  - `/manager equip <name>` — Equips a manager from your inventory.
- **`/team_view` Integration:**
  - Shows `👔 Manager: [Name] ([Synergy Status])` directly beneath the squad title.
- **Match Scope:** Manager boosts strictly apply to **Quick Sim** (`quick_sim.py`); normal turn-based duels remain untouched.

---

## 2. 🛡️ Club Logos & Crests

Logos mirror the manager architecture, allowing users to collect historic club emblems and equip one for team synergy.

- **Configurable Attributes:** `name`, `club`, `rarity`, `boost_type`, `boost_val`, and `preferred_tactic`.
- **Inventory & Equip:** Tracked via `UserOwnedLogo` and equipped via `UserLogo`.
- **Discord Commands:**
  - `/logo view` — View your currently equipped crest and rating bonus.
  - `/logo list` — List all club crests in your inventory.
  - `/logo equip <name>` — Equip a crest from your collection.

---

## 3. ⚡ Card Upgrades & Duplicate Burning

Users can now sacrifice duplicate copies of a card to unlock permanent stat boosts for Quick Sim!

### Upgrade Tiers & Requirements
| Tier | Required Duplicates | Quick Sim Stat Boost |
| :--- | :--- | :--- |
| **🥈 Silver** | **10 copies** | **+5% Attack & +5% Defence** |
| **🥇 Gold** | **25 copies** | **+10% Attack & +10% Defence** |
| **💎 Diamond** | **50 copies** | **+25% Attack & +25% Defence** |

### Safety & Commands
- **Squad Protection:** Cards placed in active lineups (`Lineup`) cannot be sacrificed during upgrades.
- **`/dupes <card>`:** Displays total copies owned and progress toward the next upgrade tier.
- **`/upgrade <card>`:** Automatically consumes the required duplicate copies, upgrades the primary card's `upgrade_tier`, and unlocks the stat boosts.

---

## 4. ✨ Refractor Card Art Variations

Cards pulled in wild spawns or packs now have a chance to roll ultra-rare **Refractor Editions** with custom borders rendered dynamically using Pillow:

| Refractor Variant | Probability | Visual Art Border |
| :--- | :--- | :--- |
| **Red Edge Refractor** | `1 in 50` (2.0%) | Vibrant Crimson Edge `#FF1A35` with outer glow |
| **Rainbow Edge Refractor** | `1 in 100` (1.0%) | 6-Stage Prismatic Gradient Overlay |
| **Black Edge Refractor** | `1 in 250` (0.4%) | Stealth Onyx & Silver Carbon Border `#1A1A1A` |
| **Gold Edge Refractor** | `1 in 800` (0.125%) | Radiant Metallic 24K Gold Frame `#FFDF00` |

- **Announcements:** Pulling a refractor displays a special `✨ REFRACTOR PULL!` banner in chat and server catch logs.
- **Showcase:** Refractor badges appear in `/team_view` and `/favorite`.

---

## 5. 🎲 Spawning Architecture & Community `/drop`

### Spawn Distribution
Spawns in active chat channels and drops now use a 3-category probabilistic roll:
- **0.75% Chance:** **World-Class Manager Spawn**
  - Displays `"👔 A World-Class Manager has appeared!"`
  - Users sign the manager via interactive modal. Auto-equips if no manager is currently active.
- **0.75% Chance:** **Legendary Club Crest Spawn**
  - Displays `"🛡️ A Legendary Club Crest has appeared!"`
  - Users claim the crest via interactive modal. Auto-equips if no logo is active.
- **98.5% Chance:** **Standard Player Card Spawn**
  - Standard catch flow with refractor rolling.

### `/drop` Command (Inventory Transfer System)
The primary purpose of `/drop` is to take a card directly from **your own inventory** and drop it into the channel for someone else to catch!

- **Ownership Requirement:** A user can only drop a card that is already present in their inventory.
- **Card Autocomplete & Selection:**
  - `/drop card: [card]`: Autocomplete lists your owned cards with OVR, ID, upgrade tiers, and refractor editions.
  - Calling `/drop` without arguments randomly selects an eligible card from your inventory.
- **Refractor & Upgrade Preservation:**
  - **No New Refractor Rolls on Drop:** When a dropped card is caught, the bot does NOT roll a random refractor.
  - Instead, the card **retains its exact refractor and upgrade tier** from the dropper's inventory. If you drop a Gold Edge Refractor, the catcher receives that exact Gold Edge Refractor.
- **Fairness & Transfer Mechanics:**
  - **Dropper Restriction:** You cannot catch your own dropped card.
  - **Active Lineup Protection:** Cards placed in your active squad lineup cannot be dropped until unequipped.
  - **Ownership Transfer:** When another user guesses the player correctly, the `UserCard` transfers ownership from the dropper to the catcher.
  - **Timeout Safety:** If nobody catches the card within 5 minutes, it is safely returned to your inventory.
  - **Cooldown:** 1 hour per user.

---

## 6. 🔒 Admin Spawning Safety & Commands

### Premium Card Protection
- `/admin spawn random`: `include_premium` defaults to `False`. Premium cards will never spawn unless explicitly requested with `include_premium=True`.
- `/admin spawn card`: `allow_premium` defaults to `False`. Attempting to spawn a Premium card without explicitly passing `allow_premium=True` is safely blocked.

### Dedicated Admin Spawners
- `/admin spawn manager <name>` — Spawns a catchable manager (with autocomplete).
- `/admin spawn logo <name>` — Spawns a catchable club logo (with autocomplete).

---

## 7. 📅 Weekly Objectives Engine

A 7-day cyclical objective system mirroring the daily objectives architecture:

- **Weekly Cycle:** Resets every Monday at `00:00 UTC` (tracked by `week_start`).
- **Django Admin:** Managed via `WeeklyObjectiveTemplate` and `UserWeeklyObjective` with full search and filter controls.
- **Synchronized Progress:** Any user action (`claim_card`, `play_match`, `open_pack`, `finish_sbc`) automatically increments both active daily and weekly objectives.
- **`/weekly` Command:** Displays active challenges, progress bars, a live Discord timestamp countdown until Monday's reset, and interactive claim buttons.
- **Rewards:** Double points, multi-pack stashes, and rare card drops.

---

## 8. ❤️ Favorite Cards Showcase

- **Showcase Mode (`/favorite` with no arguments):** Displays up to 10 favorited cards sorted by OVR, detailing:
  - Player Name & Rating (`OVR`)
  - Field Position (`POS`)
  - Card ID (`#ID`)
  - Upgrade Tier (`[SILVER]`, `[GOLD]`, `[DIAMOND]`)
  - Refractor Edition (`✨ GOLD`, `✨ RAINBOW`, etc.)
- **Toggle Mode (`/favorite <card_id>`):** Toggles a card in or out of your favorites.
- **Cap:** Hard limit of **10 favorite cards** enforced per user.
- **Alias:** Both `/favorite` and `/favourite` are supported.

---

## 9. 🗳️ Top.gg Voting Integration

- **Command:** `/vote`
- **Features:**
  - Embed with direct link button to Top.gg.
  - Explains 12-hour voting cycle.
  - Outlines voting rewards (Free Base Packs, 250 Bonus Points, weekend 2x multipliers).

---

## 10. 🎓 Beginner Tutorial & 11-Card Starter Pack

- **Command:** `/tutorial`
- **Gameplay Guide:** Interactive embed explaining cards, managers, upgrades, simulation, and objectives.
- **11-Card Starter Pack:**
  - First-time callers (`user.has_completed_tutorial == False`) are immediately granted 11 random Base cards:
    - **1 Goalkeeper** (`GK`)
    - **4 Defenders** (`LB`, `CB`, `RB`)
    - **3 Midfielders** (`CM`, `CDM`, `CAM`)
    - **3 Attackers** (`LW`, `ST`, `RW`)
  - Automatically creates and slots all 11 players into **Lineup 1 (4-3-3 Balanced)** so the user can immediately jump into `/quick_sim`.
  - Sets `has_completed_tutorial = True` to prevent duplicate claims.

---

## 11. 🎨 Event Logos & Custom Emojis

- **Config File:** Located at `core/event_emojis.json`.
- **Inline Text Display:** In `/collection`, event names dynamically display custom Discord server emojis (e.g. `<:ucl:123456789012345678>`) or standard Unicode emojis.
- **Asset Storage:** High-resolution event graphics, manager portraits, and club crests are stored in:
  - `media/events/`
  - `media/managers/`
  - `media/logos/`

---

## 12. 📊 Verification & Testing Summary

| Test Area | Verification Method | Result |
| :--- | :--- | :--- |
| **Django System Check** | `python manage.py check` | `0 issues identified` |
| **Database Migrations** | `0034_weeklyobjectivetemplate_and_more` | Applied cleanly |
| **Manager Synergy** | Formation & Tactic matching in `calculate_manager_boosts` | Boosts applied as expected |
| **Upgrade Burn Logic** | Sacrifice validation & active squad exclusion | Passed (lineup protected) |
| **Refractor Probability** | 1,000 roll test distribution | Within target ranges |
| **Weekly Objective Sync** | Simultaneous progress increment & Monday cycle | Verified working |
| **Quick Sim Team Data** | `build_team_data()` snapshot test | Correctly loaded manager & tier stats |

