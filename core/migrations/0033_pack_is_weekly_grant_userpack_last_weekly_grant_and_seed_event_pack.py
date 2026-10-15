# Generated manually
from django.db import migrations, models


def seed_event_pack_data(apps, schema_editor):
    RateConfig = apps.get_model('core', 'RateConfig')
    SpawnRate = apps.get_model('core', 'SpawnRate')
    Pack = apps.get_model('core', 'Pack')

    # 1. Ensure RateConfig for PACK_EVENT exists
    rate_config, created = RateConfig.objects.get_or_create(
        category='PACK_EVENT',
        defaults={'mode': 'RARITY'}
    )

    if created or rate_config.rates.count() == 0:
        rate_config.mode = 'RARITY'
        rate_config.save()
        default_weights = {
            "Common I": 0.3,
            "Common II": 0.25,
            "Common III": 0.15,
            "Uncommon I": 0.1,
            "Uncommon II": 0.06,
            "Uncommon III": 0.04,
            "Rare I": 0.04,
            "Rare II": 0.025,
            "Rare III": 0.015,
            "Epic I": 0.007,
            "Epic II": 0.005,
            "Epic III": 0.003,
            "Legendary I": 0.0025,
            "Legendary II": 0.0020,
            "Legendary III": 0.0005,
        }
        for rarity, weight in default_weights.items():
            SpawnRate.objects.get_or_create(
                config=rate_config,
                rarity=rarity,
                defaults={'weight': weight}
            )

    # 2. Ensure Pack for Event Pack is configured for weekly grant and linked to PACK_EVENT
    Pack.objects.update_or_create(
        code="event",
        defaults={
            "name": "Event Pack",
            "cooldown_days": 7.0,
            "card_type_filter": "EVENT",
            "is_premium_only": False,
            "is_weekly_grant": True,
            "rate_config_category": "PACK_EVENT",
        }
    )


def revert_event_pack_data(apps, schema_editor):
    RateConfig = apps.get_model('core', 'RateConfig')
    RateConfig.objects.filter(category='PACK_EVENT').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_manager_cardtemplate_attack_boost_pct_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='pack',
            name='is_weekly_grant',
            field=models.BooleanField(
                default=False,
                help_text='If True, all users automatically receive this pack once every 7 days in their pack wallet'
            ),
        ),
        migrations.AddField(
            model_name='userpack',
            name='last_weekly_grant',
            field=models.DateTimeField(
                blank=True,
                help_text='Timestamp of when the user was last granted their weekly free pack',
                null=True
            ),
        ),
        migrations.RunPython(seed_event_pack_data, revert_event_pack_data),
    ]
