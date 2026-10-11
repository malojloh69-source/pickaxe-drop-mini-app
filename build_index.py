"""Bundle the Mini App UI into one HTML file for reliable local previews."""
from pathlib import Path
import base64
import json
import struct

ROOT = Path(__file__).resolve().parent / "static"
template = (ROOT / "index.template.html").read_text(encoding="utf-8")
css = (ROOT / "styles.css").read_text(encoding="utf-8")
js = "\n".join((ROOT / name).read_text(encoding="utf-8") for name in ("physics.js", "sound.js", "bet.js", "mines.js", "account.js", "app.js"))
# Refuse a truncated WOFF2 file instead of shipping a broken font.
font = (ROOT / "assets/fonts/InterVariable.woff2").read_bytes()
if font[:4] != b"wOF2" or len(font) < 48 or struct.unpack(">I", font[8:12])[0] != len(font):
    raise SystemExit("InterVariable.woff2 is damaged or truncated")
font_url = "data:font/woff2;base64," + base64.b64encode(font).decode("ascii")
css = css.replace("/static/assets/fonts/InterVariable.woff2", font_url)
# Embed each image only once, even when several elements share the asset.
assets = {}
for key, filename in (("hero", "pickaxe-hero.png"), ("mines-banner", "mines-banner.png"), ("mines-card", "mines-card.png")):
    assets[key] = "data:image/png;base64," + base64.b64encode((ROOT / "assets" / filename).read_bytes()).decode("ascii")
    template = template.replace(f'src="/static/assets/{filename}"', f'data-asset="{key}"')
asset_loader = "(() => {const assets = " + json.dumps(assets) + ";document.querySelectorAll('[data-asset]').forEach(img => {img.src = assets[img.dataset.asset]});})();"
sounds = {name: "data:audio/wav;base64," + base64.b64encode(
    (ROOT / f"assets/sounds/{name}.wav").read_bytes()).decode("ascii")
    for name in ("start", "hit", "ore", "blast", "pickup", "finish")}
asset_loader += "\nwindow.PickaxeAudioAssets = " + json.dumps(sounds) + ";"
style_link = '<link rel="stylesheet" href="/static/styles.css">'
script_link = '<script src="/static/app.js" defer></script>'
physics_link = '<script src="/static/physics.js" defer></script>'
sound_link = '<script src="/static/sound.js" defer></script>'
mines_link = '<script src="/static/mines.js" defer></script>'
account_link = '<script src="/static/account.js" defer></script>'
bet_link = '<script src="/static/bet.js" defer></script>'
if any(template.count(link) != 1 for link in (style_link, script_link, physics_link, sound_link, mines_link, account_link, bet_link)) or template.count("</body>") != 1:
    raise SystemExit("Unexpected HTML template structure")
if "</style" in css.lower() or "</script" in js.lower():
    raise SystemExit("Source contains an HTML closing tag")
output = template.replace(style_link, f"<style>\n{css}\n</style>")
output = output.replace(physics_link, "").replace(sound_link, "").replace(bet_link, "").replace(mines_link, "").replace(account_link, "").replace(script_link, "")
output = output.replace("</body>", f"<script>\n{asset_loader}\n{js}\n</script>\n</body>")
(ROOT / "index.html").write_text(output, encoding="utf-8")
print(f"Built {ROOT / 'index.html'} ({len(output.encode('utf-8'))} bytes)")
