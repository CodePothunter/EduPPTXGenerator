import base64
import re

from PIL import Image

from edupptx.agent import PPTXAgent
from edupptx.models import SlideAssets


def test_inject_images_preserves_png_alpha_for_transparent_padding(tmp_path):
    image_path = tmp_path / "padded.png"
    image = Image.new("RGBA", (16, 12), (0, 0, 0, 0))
    image.putpixel((8, 6), (255, 0, 0, 255))
    image.save(image_path)

    svg = '<svg><image href="__IMAGE_ILLUSTRATION_1__" x="0" y="0" width="160" height="120"/></svg>'
    assets = SlideAssets(page_number=1)
    assets.image_paths["illustration_1"] = image_path

    injected = PPTXAgent._inject_images(svg, assets)

    assert "data:image/png;base64," in injected
    payload = re.search(r"data:image/png;base64,([^\"']+)", injected).group(1)
    decoded = base64.b64decode(payload)
    assert decoded.startswith(b"\x89PNG")
