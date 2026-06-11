from PIL import Image

from edupptx.planning.exercise_plan_binder import _materialize_exercise_image


def test_materialize_exercise_image_returns_saved_canvas_ratio(tmp_path):
    source = tmp_path / "raw_question.png"
    destination = tmp_path / "session" / "materials" / "exercises" / "q_1_img.png"
    Image.new("RGBA", (100, 120), (20, 80, 180, 255)).save(source)

    aspect_ratio = _materialize_exercise_image(
        source,
        destination,
        target_aspect_ratio="",
    )

    assert aspect_ratio == "3:4"
    with Image.open(destination) as image:
        assert image.mode == "RGBA"
        assert image.width * 4 == image.height * 3
        assert image.size == (102, 136)
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((51, 68))[3] == 255
