from typing import Sequence
from django import forms
from django.contrib import admin
from django.utils.html import format_html

from .models import (
    Blacklist,
    CardTemplate,
    CommandLog,
    DiscordUser,
    FavouriteCard,
    Lineup,
    Logo,
    PremiumRole,
    PromoCode,
    PromoCodeRedemption,
    RateConfig,
    SBC,
    SBCRequirement,
    ServerSettings,
    SpawnRate,
    Trade,
    TradeItem,
    UserCard,
    UserLogo,
    Pack,
    UserPack,
)


# ── Rate Configuration with Inline Rates ────────────────────


class SpawnRateInline(admin.TabularInline):
    model = SpawnRate
    extra = 0
    fields = ("rarity", "min_ovr", "max_ovr", "weight", "get_percentage")
    readonly_fields = ("get_percentage",)

    @admin.display(description="Effective %")
    def get_percentage(self, obj):
        if obj.pk is None:
            return "—"
        return obj.percentage


@admin.register(RateConfig)
class RateConfigAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("category", "mode", "rates_summary")
    inlines: Sequence[type] = [SpawnRateInline]

    @admin.display(description="Current Rates")
    def rates_summary(self, obj):
        return obj.get_rates_summary()

    def save_model(self, request, obj, form, change):
        """Auto-populate default rates when creating a new config or switching modes."""
        super().save_model(request, obj, form, change)

        # Check if mode changed or if there are no rates yet
        existing_rates = obj.rates.count()
        mode_changed = change and "mode" in form.changed_data

        if mode_changed:
            # Wipe old rates when switching modes
            obj.rates.all().delete()
            existing_rates = 0

        if existing_rates == 0:
            self._populate_defaults(obj)

    def _populate_defaults(self, config):
        """Create default rate entries based on the mode."""
        from core.settings import settings as bot_settings

        if config.mode == "RARITY":
            for rarity, weight in bot_settings.rarity_weights.items():
                SpawnRate.objects.create(
                    config=config, rarity=rarity, weight=weight
                )
        else:
            # OVR mode defaults
            if config.category == "PACK_PREMIUM":
                ovr_defaults = [
                    (86, 87, 50.0),
                    (87, 90, 30.0),
                    (91, 92, 15.0),
                    (93, 94, 5.0),
                ]
            else:
                ovr_defaults = [
                    (80, 84, 30.0),
                    (85, 87, 50.0),
                    (88, 90, 15.0),
                    (91, 99, 5.0),
                ]
            for min_ovr, max_ovr, weight in ovr_defaults:
                SpawnRate.objects.create(
                    config=config,
                    min_ovr=min_ovr,
                    max_ovr=max_ovr,
                    weight=weight,
                )


# ── Standard Model Admins ───────────────────────────────────


@admin.register(DiscordUser)
class DiscordUserAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = (
        "discord_id",
        "username",
        "points",
        "wins",
        "losses",
        "cards_collected",
        "is_premium",
        "is_admin",
        "is_inventory_private",
    )
    search_fields = ("discord_id", "username")
    readonly_fields = (
        "get_trade_count",
        "get_catch_count",
        "get_pack_opens",
        "get_bet_count",
        "get_activity_summary",
    )
    fieldsets = (
        (None, {
            "fields": (
                "discord_id", "username", "points",
                "wins", "losses", "draws", "cards_collected",
                "is_premium", "is_booster", "is_admin", "is_inventory_private",
            )
        }),
        ("Cooldowns", {
            "fields": (
                "last_pack_daily", "last_pack_weekly",
                "last_pack_event", "last_pack_premium", "last_pack_booster",
            ),
            "classes": ("collapse",),
        }),
        ("Activity Stats (read-only)", {
            "fields": (
                "get_activity_summary",
                "get_trade_count", "get_catch_count",
                "get_pack_opens", "get_bet_count",
            ),
        }),
    )

    @admin.display(description="Completed Trades")
    def get_trade_count(self, obj):
        from django.db.models import Q
        count = Trade.objects.filter(
            Q(initiator=obj) | Q(receiver=obj),
            status="COMPLETED"
        ).count()
        return count

    @admin.display(description="Cards Caught (not traded)")
    def get_catch_count(self, obj):
        return UserCard.objects.filter(owner=obj, traded_by__isnull=True).count()

    @admin.display(description="Packs Opened")
    def get_pack_opens(self, obj):
        pack_commands = ["pack_daily", "pack_weekly", "pack_event", "pack_premium", "pack_booster"]
        count = CommandLog.objects.filter(
            user_id=obj.discord_id,
            command_name__in=pack_commands,
        ).count()
        return count

    @admin.display(description="Wagers/Bets")
    def get_bet_count(self, obj):
        count = CommandLog.objects.filter(
            user_id=obj.discord_id,
            command_name__in=["wager challenge", "wager"],
        ).count()
        return count

    @admin.display(description="Quick Summary")
    def get_activity_summary(self, obj):
        trades = self.get_trade_count(obj)
        catches = self.get_catch_count(obj)
        packs = self.get_pack_opens(obj)
        bets = self.get_bet_count(obj)
        return format_html(
            "<strong>Trades:</strong> {} | <strong>Catches:</strong> {} | "
            "<strong>Packs:</strong> {} | <strong>Bets:</strong> {}",
            trades, catches, packs, bets
        )


@admin.register(CardTemplate)
class CardTemplateAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("name", "ovr", "rarity", "position", "card_type", "event_name")
    list_filter: Sequence[str] = ("rarity", "card_type", "position", "event_name")
    search_fields: Sequence[str] = ("name", "club", "event_name")
    readonly_fields: Sequence[str] = ()


@admin.register(UserCard)
class UserCardAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("owner", "template", "caught_at")
    list_filter: Sequence[str] = ("caught_at",)
    search_fields: Sequence[str] = ("owner__username", "template__name")


@admin.register(FavouriteCard)
class FavouriteCardAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("owner", "card", "created_at")
    search_fields: Sequence[str] = ("owner__username",)


@admin.register(Logo)
class LogoAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("name", "rarity", "bonus")


@admin.register(UserLogo)
class UserLogoAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("owner", "logo")


@admin.register(Lineup)
class LineupAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("owner", "name", "formation", "is_active")


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("code", "reward_type", "uses", "max_uses", "expires_at")
    autocomplete_fields: Sequence[str] = ["reward_card"]


@admin.register(PromoCodeRedemption)
class PromoCodeRedemptionAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("user", "promo_code", "redeemed_at")
    list_filter: Sequence[str] = ("redeemed_at",)
    search_fields: Sequence[str] = ("user__username", "promo_code__code")


@admin.register(ServerSettings)
class ServerSettingsAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = (
        "guild_id",
        "spawn_channel_id",
        "catch_log_channel_id",
        "command_log_channel_id",
    )


@admin.register(Blacklist)
class BlacklistAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("type", "target_id", "reason", "created_at")
    list_filter: Sequence[str] = ("type", "created_at")
    search_fields: Sequence[str] = ("target_id", "reason")


@admin.register(CommandLog)
class CommandLogAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("user_id", "command_name", "guild_id", "timestamp")
    list_filter: Sequence[str] = ("command_name", "timestamp")
    search_fields: Sequence[str] = ("user_id", "guild_id")


class SBCRequirementInline(admin.TabularInline):
    model = SBCRequirement
    extra = 1
    autocomplete_fields: Sequence[str] = ["specific_template"]


@admin.register(SBC)
class SBCAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("name", "reward_card", "reward_pack", "is_active", "end_date")
    list_filter: Sequence[str] = ("is_active", "end_date")
    search_fields: Sequence[str] = ("name",)
    autocomplete_fields: Sequence[str] = ["reward_card", "reward_pack"]
    inlines: Sequence[type] = [SBCRequirementInline]


@admin.register(PremiumRole)
class PremiumRoleAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("role_id", "label", "added_at")
    search_fields: Sequence[str] = ("role_id", "label")


class PackAdminForm(forms.ModelForm):
    class Meta:
        model = Pack
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        try:
            # Populate event_name_filter choices
            event_names = list(CardTemplate.objects.exclude(event_name__isnull=True).exclude(event_name="").values_list('event_name', flat=True).distinct())
            event_choices = [("", "--- Any Event ---")] + [(name, name) for name in sorted(event_names)]
            self.fields['event_name_filter'] = forms.ChoiceField(
                choices=event_choices,
                required=False,
                help_text="Require specific event name (dropdown from existing templates)"
            )

            # Populate rate_config_category choices
            rate_configs = list(RateConfig.objects.values_list('category', flat=True).distinct())
            rate_choices = [("", "--- Default ---")] + [(cat, cat) for cat in sorted(rate_configs)]
            self.fields['rate_config_category'] = forms.ChoiceField(
                choices=rate_choices,
                required=False,
                help_text="The category string matching a RateConfig"
            )
        except Exception:
            pass


@admin.register(Pack)
class PackAdmin(admin.ModelAdmin):
    form = PackAdminForm
    list_display: Sequence[str] = ("name", "code", "cooldown_days", "is_premium_only", "card_type_filter", "event_name_filter", "min_ovr_filter", "max_ovr_filter")
    search_fields: Sequence[str] = ("name", "code")


@admin.register(UserPack)
class UserPackAdmin(admin.ModelAdmin):
    list_display: Sequence[str] = ("user", "pack", "stash_count", "last_opened_at")
    search_fields: Sequence[str] = ("user__username", "user__discord_id", "pack__name")
    list_filter: Sequence[str] = ("pack",)
