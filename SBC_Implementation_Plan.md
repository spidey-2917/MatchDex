# Squad Building Challenge (SBC) Feature Implementation Plan

This document outlines the implementation plan for adding an EAFC-style Squad Building Challenge feature to the MatchDex bot, updated with user feedback.

## User Feedback Incorporated

1.  **Card Selection Limits**: If a player has more than 25 cards that meet the requirements, the drop-down menu will be paginated (25 cards per page) similar to the bulk trade feature. An "Auto-Fill" button will also be provided for players who don't want to select cards one by one.
2.  **Multiple Requirements**: All required players/cards will be combined into a single selection flow/list rather than having separate drop-downs for each requirement.
3.  **Requirement Types**: The system will support both specific card requirements (e.g., "Erling Haaland Base Card") and generic requirements (e.g., "Any Rare card" or "Any Real Madrid player").

## Proposed Changes

### Database Models (`core/models.py`)
We will create two new models to track SBCs and their requirements.

#### [NEW] SBC Model
- `name`: Name of the SBC (e.g., "Flashback Ronaldo").
- `description`: Details about the SBC.
- `reward_card`: `ForeignKey` to `CardTemplate` (the card the user receives).
- `is_active`: Boolean to easily turn the SBC on/off.
- `end_date`: Optional expiration date.

#### [NEW] SBCRequirement Model
- `sbc`: `ForeignKey` to the `SBC`.
- `quantity`: Integer (how many cards are needed to satisfy this specific requirement).
- **Flexible Requirement Fields** (Only one or a combination must be met):
  - `specific_template`: `ForeignKey` to `CardTemplate` (optional, for specific players).
  - `min_ovr`: Integer (optional, minimum OVR required).
  - `required_rarity`: CharField matching the RARITIES choices (optional).
  - `required_club`: CharField (optional).
  - `required_position`: CharField matching POSITIONS choices (optional).

---

### Django Admin Panel (`core/admin.py`)
#### [MODIFY] core/admin.py
- Register the `SBC` model.
- Add `SBCRequirement` as a `TabularInline` to the `SBC` admin page. 
- This will allow you to create an SBC and configure both specific and generic requirements with ease.

---

### Discord Bot Logic (`core/bot_logic/SBCCog.py`)
#### [NEW] core/bot_logic/SBCCog.py
- Create a new cog with the `/sbc` command.
- **Command Flow:**
  1. User runs `/sbc` and selects an active SBC from an autocomplete list.
  2. The bot queries the user's inventory to see what cards they own that match the requirements.
  3. **Not Enough Cards**: If they lack the required amount, the bot replies with an embed detailing what they need vs what they have.
  4. **Has Enough Cards**: The bot displays a "Start the SBC" button and an "Auto-Fill" button.
  5. **Auto-Fill Path**: If Auto-Fill is clicked, the bot automatically selects the lowest OVR cards that meet the criteria, asks for confirmation, and completes the SBC.
  6. **Manual Selection Path**: Clicking "Start the SBC" reveals a paginated drop-down menu (max 25 options per page). The user can use page navigation buttons to browse all their valid cards. The user selects the cards they want to submit.
  7. **Confirmation**: Once they select the required amount of cards, they click a "Confirm" button.
  8. **Execution**: The selected `UserCard` items are deleted from their inventory, and a new `UserCard` (the SBC reward) is generated and assigned to them. A success message is displayed.

## Verification Plan

### Automated Tests
- Run Django migrations to ensure the `SBC` and `SBCRequirement` models are created successfully.

### Manual Verification
- **Admin Panel**: Log into the Django admin panel, create test SBCs with specific template requirements and generic requirements (like rarity or OVR).
- **Discord Testing**:
  - Run `/sbc` without the required cards to verify the "missing cards" error message.
  - Run `/sbc` with the required cards.
  - Test the **Auto-Fill** feature to ensure it grabs the correct cards.
  - Test the **Manual Selection** feature, ensuring pagination works correctly if more than 25 valid cards are owned.
  - Complete the SBC, verify the submitted cards are removed from the inventory, and verify the reward card is added.
