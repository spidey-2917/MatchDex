import os
from io import BytesIO
from PIL import Image, ImageDraw, ImageFont

# Define base paths
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA_DIR = os.path.join(BASE_DIR, "media", "pitch_assets")

PITCH_PATH = os.path.join(MEDIA_DIR, "pitch.png")
JERSEY_PATH = os.path.join(MEDIA_DIR, "jersey.png")

# Coordinates are (X, Y) relative from 0.0 to 1.0
# Y coordinates: GK is bottom, AT is top
ROW_Y = {
    "gk": 0.88,
    "df": 0.70,
    "md": 0.45,
    "at": 0.18
}

def _get_x_coords(count):
    if count == 1:
        return [0.5]
    elif count == 2:
        return [0.35, 0.65]
    elif count == 3:
        return [0.2, 0.5, 0.8]
    elif count == 4:
        return [0.15, 0.38, 0.62, 0.85]
    elif count == 5:
        return [0.1, 0.3, 0.5, 0.7, 0.9]
    return []

def get_slot_coords(formation_key):
    from core.bot_logic.TeamCog import FORMATIONS
    f = FORMATIONS.get(formation_key, FORMATIONS["433"])
    
    coords = {}
    
    # GK
    coords["gk"] = (0.5, ROW_Y["gk"])
    
    # DF
    df_count = f["df"]
    xs = _get_x_coords(df_count)
    for i in range(df_count):
        coords[f"df{i+1}"] = (xs[i], ROW_Y["df"])
        
    # MD
    md_count = f["md"]
    xs = _get_x_coords(md_count)
    
    if formation_key == "4231":
        # md1, md2 are CDMs
        coords["md1"] = (0.35, 0.58)
        coords["md2"] = (0.65, 0.58)
        # md3, md4, md5 are CAMs
        coords["md3"] = (0.2, 0.38)
        coords["md4"] = (0.5, 0.38)
        coords["md5"] = (0.8, 0.38)
    elif formation_key == "4141":
        # md1 is CDM
        coords["md1"] = (0.5, 0.58)
        # md2-md5 are LM/CM/CM/RM
        xs_4 = _get_x_coords(4)
        for i in range(4):
            coords[f"md{i+2}"] = (xs_4[i], 0.40)
    else:
        for i in range(md_count):
            coords[f"md{i+1}"] = (xs[i], ROW_Y["md"])
            
    # AT
    at_count = f["at"]
    xs = _get_x_coords(at_count)
    for i in range(at_count):
        coords[f"at{i+1}"] = (xs[i], ROW_Y["at"])
        
    return coords


def generate_pitch_image(lineup_data, formation_key):
    """
    lineup_data is a dict mapping slot_name -> {"ovr": "94", "name": "Maldini"}
    If slot is empty, it shouldn't be in the dict.
    """
    if not os.path.exists(PITCH_PATH) or not os.path.exists(JERSEY_PATH):
        # Fallback to None if files don't exist
        return None
        
    pitch = Image.open(PITCH_PATH).convert("RGBA")
    jersey = Image.open(JERSEY_PATH).convert("RGBA")
    
    pitch_w, pitch_h = pitch.size
    jersey_w, jersey_h = jersey.size
    
    draw = ImageDraw.Draw(pitch)
    
    try:
        font_ovr = ImageFont.truetype("arial.ttf", 26)
        font_name = ImageFont.truetype("arial.ttf", 16)
    except IOError:
        font_ovr = ImageFont.load_default()
        font_name = ImageFont.load_default()

    coords = get_slot_coords(formation_key)
    
    for slot, (rel_x, rel_y) in coords.items():
        if slot in lineup_data:
            data = lineup_data[slot]
            
            # Paste jersey
            paste_x = int(rel_x * pitch_w - jersey_w / 2)
            paste_y = int(rel_y * pitch_h - jersey_h / 2)
            pitch.paste(jersey, (paste_x, paste_y), jersey)
            
            # Draw OVR on jersey
            ovr_text = str(data["ovr"])
            # In older PIL versions textbbox might not be available or we can just use textlength
            try:
                bbox_ovr = draw.textbbox((0, 0), ovr_text, font=font_ovr)
                ovr_w = bbox_ovr[2] - bbox_ovr[0]
                ovr_h = bbox_ovr[3] - bbox_ovr[1]
            except AttributeError:
                ovr_w = draw.textlength(ovr_text, font=font_ovr)
                ovr_h = 26
            
            # center OVR on jersey, moving it up slightly
            draw.text((paste_x + (jersey_w - ovr_w) / 2, paste_y + (jersey_h - ovr_h) / 2 - 8), ovr_text, fill="white", font=font_ovr)
            
            # Draw Name below jersey
            name_text = str(data["name"])
            try:
                bbox_name = draw.textbbox((0, 0), name_text, font=font_name)
                name_w = bbox_name[2] - bbox_name[0]
                name_h = bbox_name[3] - bbox_name[1]
            except AttributeError:
                name_w = draw.textlength(name_text, font=font_name)
                name_h = 16
            
            name_x = int(rel_x * pitch_w - name_w / 2)
            name_y = paste_y + jersey_h + 2
            
            # small black background for text
            draw.rectangle([name_x - 3, name_y - 2, name_x + name_w + 3, name_y + name_h + 4], fill=(0, 0, 0, 180))
            draw.text((name_x, name_y), name_text, fill="white", font=font_name)
            
    out_io = BytesIO()
    pitch.save(out_io, format="PNG")
    out_io.seek(0)
    return out_io
