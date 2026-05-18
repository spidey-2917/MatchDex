import os
import sys
import django

# Add workspace directory to path
sys.path.append(os.path.abspath(os.path.dirname(__file__) + "/.."))

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "matchdex_web.settings")
django.setup()

from core.models import CardTemplate, RateConfig, SpawnRate

print("--- Card Templates Rarities ---")
templates = CardTemplate.objects.all()
rarity_counts = {}
for t in templates:
    rarity_counts[t.rarity] = rarity_counts.get(t.rarity, 0) + 1
print(rarity_counts)

print("\n--- Rate Configurations ---")
configs = RateConfig.objects.all()
for c in configs:
    print(f"Config: {c.category} - {c.mode}")
    for r in c.rates.all():
        print(f"  Rate: {r.rarity} - {r.weight}")
