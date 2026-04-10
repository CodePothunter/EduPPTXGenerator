import json
from pathlib import Path

import pytest
from PIL import Image

from edupptx.material_library import MaterialLibrary


@pytest.fixture
def library(tmp_path):
    return MaterialLibrary(tmp_path / "library")


@pytest.fixture
def sample_image(tmp_path):
    """Create a tiny valid image file for testing."""
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    path = tmp_path / "test_bg.jpg"
    img.save(path)
    return path


def test_empty_library(library):
    assert library.list_all() == []
    assert library.summary() == {"total": 0, "by_type": {}}


def test_add_material(library, sample_image):
    entry = library.add(
        source_path=sample_image,
        type="background",
        tags=["math", "geometry"],
        palette="emerald",
        source="programmatic",
        description="Test background",
    )
    assert entry.id == "mat_0000"
    assert entry.type == "background"
    assert (library.dir / entry.path).exists()


def test_add_increments_id(library, sample_image):
    e1 = library.add(sample_image, "background", ["a"], "emerald", "programmatic", "First")
    e2 = library.add(sample_image, "diagram", ["b"], "blue", "programmatic", "Second")
    assert e1.id == "mat_0000"
    assert e2.id == "mat_0001"


def test_search_by_tags(library, sample_image):
    library.add(sample_image, "background", ["math", "geometry"], "emerald", "programmatic", "Math bg")
    library.add(sample_image, "background", ["biology", "cell"], "emerald", "programmatic", "Bio bg")
    results = library.search(tags=["math"])
    assert len(results) == 1
    assert results[0].description == "Math bg"


def test_search_palette_bonus(library, sample_image):
    library.add(sample_image, "background", ["math"], "blue", "programmatic", "Blue math")
    library.add(sample_image, "background", ["math"], "emerald", "programmatic", "Emerald math")
    results = library.search(tags=["math"], palette="emerald")
    assert results[0].description == "Emerald math"


def test_search_by_type(library, sample_image):
    library.add(sample_image, "background", ["math"], "emerald", "programmatic", "BG")
    library.add(sample_image, "diagram", ["math"], "emerald", "programmatic", "Diagram")
    results = library.search(tags=["math"], type="diagram")
    assert len(results) == 1
    assert results[0].type == "diagram"


def test_search_no_match(library, sample_image):
    library.add(sample_image, "background", ["math"], "emerald", "programmatic", "Math bg")
    results = library.search(tags=["biology"])
    assert len(results) == 0


def test_get_by_id(library, sample_image):
    entry = library.add(sample_image, "background", ["math"], "emerald", "programmatic", "Test")
    found = library.get(entry.id)
    assert found is not None
    assert found.id == entry.id


def test_get_missing(library):
    assert library.get("nonexistent") is None


def test_list_all_filtered(library, sample_image):
    library.add(sample_image, "background", ["a"], "emerald", "programmatic", "BG")
    library.add(sample_image, "diagram", ["b"], "emerald", "programmatic", "Diagram")
    bgs = library.list_all(type="background")
    assert len(bgs) == 1
    assert bgs[0].type == "background"


def test_index_persists(tmp_path, sample_image):
    lib_dir = tmp_path / "library"
    lib1 = MaterialLibrary(lib_dir)
    lib1.add(sample_image, "background", ["math"], "emerald", "programmatic", "Persisted")
    lib2 = MaterialLibrary(lib_dir)
    assert len(lib2.list_all()) == 1
    assert lib2.list_all()[0].description == "Persisted"


def test_summary(library, sample_image):
    library.add(sample_image, "background", ["a"], "emerald", "programmatic", "BG1")
    library.add(sample_image, "background", ["b"], "emerald", "programmatic", "BG2")
    library.add(sample_image, "diagram", ["c"], "emerald", "programmatic", "D1")
    s = library.summary()
    assert s["total"] == 3
    assert s["by_type"]["background"] == 2
    assert s["by_type"]["diagram"] == 1
