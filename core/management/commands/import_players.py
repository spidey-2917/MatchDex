import io
import os
import re
import shutil

import requests
from bs4 import BeautifulSoup
from django.conf import settings
from django.core.files import File
from django.core.management.base import BaseCommand
from PIL import Image

from core.models import CardTemplate
from core.utils import map_sofifa_pos

try:
    import easyocr
    import numpy as np
except ImportError:
    easyocr = None


class Command(BaseCommand):
    help = "Automatically imports card templates from images in media/import_queue/ using OCR"

    def handle(self, *args, **options):
        if not easyocr:
            self.stdout.write(
                self.style.ERROR(
                    "EasyOCR not installed. Run: py -3.11 -m pip install easyocr torch torchvision"
                )
            )
            return

        self.stdout.write(
            "Initializing OCR engine (this can take 30-60 seconds on first run)..."
        )
        try:
            reader = easyocr.Reader(["en"], gpu=False)
        except Exception as e:
            self.stdout.write(self.style.ERROR(f"Failed to initialize OCR: {str(e)}"))
            return

        import_dir = os.path.join(settings.MEDIA_ROOT, "import_queue")
        imported_dir = os.path.join(import_dir, "imported")

        if not os.path.exists(import_dir):
            self.stdout.write(
                self.style.ERROR(f"Import directory {import_dir} not found.")
            )
            return

        if not os.path.exists(imported_dir):
            os.makedirs(imported_dir)

        files = [
            f
            for f in os.listdir(import_dir)
            if f.lower().endswith((".png", ".jpg", ".jpeg"))
        ]

        if not files:
            self.stdout.write(self.style.WARNING("No images found in import_queue."))
            return

        self.stdout.write(self.style.SUCCESS(f"Found {len(files)} files to process."))

        for filename in files:
            player_name = os.path.splitext(filename)[0]
            self.stdout.write(f"--- Processing '{player_name}' ---")
            src_path = os.path.join(import_dir, filename)

            try:
                # 1. Open image and handle OCR
                img = Image.open(src_path).convert("RGB")
                w, h = img.size

                # Regions: [top, left, bottom, right] in percentages
                # Refined based on visual inspection:
                def get_crop(box_pct):
                    t, l, b, r = box_pct
                    crop = img.crop(
                        (l * w / 100, t * h / 100, r * w / 100, b * h / 100)
                    )
                    # Senior Move: Preprocess for better OCR
                    crop = crop.convert("L")  # Grayscale
                    # Thresholding to isolate black/white (depends on card theme,
                    # but usually helps with textured backgrounds)
                    return crop

                att_img = get_crop([78, 12, 91, 44])  # Expanded for padding
                def_img = get_crop([78, 56, 91, 88])  # Expanded for padding

                def read_stat(image):
                    img_byte_arr = io.BytesIO()
                    image.save(img_byte_arr, format="PNG")
                    # Senior Move: Use allowlist for strict digit detection
                    results = reader.readtext(
                        img_byte_arr.getvalue(), allowlist="0123456789"
                    )
                    text = "".join([r[1] for r in results])
                    nums = re.findall(r"\d+", text)
                    if nums:
                        # Take the first number, but handle cases like "91" vs "9 1"
                        val = int("".join(nums))
                        return (
                            val if val < 200 else int(str(val)[:2])
                        )  # Sanity check for double reads
                    return 0

                att_val = read_stat(att_img)
                def_val = read_stat(def_img)

                self.stdout.write(f"OCR Detected -> ATT: {att_val}, DEF: {def_val}")

                # 2. Search SoFIFA for Position and Club (since they aren't easy to OCR)
                search_url = f"https://sofifa.com/players?keyword={player_name.replace(' ', '+')}"
                headers = {"User-Agent": "Mozilla/5.0"}
                response = requests.get(search_url, headers=headers, timeout=10)
                soup = BeautifulSoup(response.text, "html.parser")

                player_row = soup.select_one("table.table-hover tbody tr")
                mapped_pos = "ST"
                club_name = "Unknown"

                if player_row:
                    pos_tags = player_row.select('a[rel="nofollow"] span.pos')
                    raw_pos = pos_tags[0].text if pos_tags else "ST"
                    mapped_pos = map_sofifa_pos(raw_pos)

                    club_tag = player_row.select_one('a[href^="/team/"]')
                    if club_tag:
                        club_name = club_tag.text

                # 3. Create/Update Template
                template, created = CardTemplate.objects.get_or_create(
                    name=player_name,
                    defaults={
                        "position": mapped_pos,
                        "attack_stat": att_val or 70,
                        "defence_stat": def_val or 70,
                        "card_type": "BASE",
                        "club": club_name,
                    },
                )

                if not created:
                    template.attack_stat = att_val or template.attack_stat
                    template.defence_stat = def_val or template.defence_stat
                    template.position = mapped_pos
                    template.club = club_name
                    template.save()

                # 4. Save Image
                with open(src_path, "rb") as f:
                    template.image_base.save(filename, File(f), save=True)

                # 5. Move to imported
                dest_path = os.path.join(imported_dir, filename)
                if os.path.exists(dest_path):
                    os.remove(dest_path)
                shutil.move(src_path, dest_path)

                status = "Created" if created else "Updated"
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{status} {player_name} ({mapped_pos}) | Club: {club_name}"
                    )
                )

            except Exception as e:
                self.stdout.write(
                    self.style.ERROR(f"Error processing {player_name}: {str(e)}")
                )

        self.stdout.write(self.style.SUCCESS("\nAll players processed!"))
