"""Standalone application entry point and packaged dependency smoke check."""
import sys

if "--smoke-test" in sys.argv:
    import json
    import os
    import tempfile
    from pathlib import Path
    import rawpy
    from PIL import Image
    from PySide6.QtWidgets import QApplication
    from raw2leica.core import Options, convert, load_profiles, read_metadata
    import raw2leica.core as core

    os.environ["EXIFTOOL_PATH"] = str(
        Path(core.__file__).resolve().parents[1] / ".tools/exiftool/bin/exiftool"
    )
    app = QApplication([])
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        source = root / "测试.jpg"
        Image.new("RGB", (120, 80), "#b26135").save(source)
        profiles = load_profiles()
        output = convert(source, Options(profiles[0], output_dir=root / "output"))
        metadata = read_metadata(output)
        assert metadata["Model"] == "LEICA M11-P"
        assert (metadata["ImageWidth"], metadata["ImageHeight"]) == (120, 80)
        assert output.with_suffix(".jpg.json").exists()
        print(json.dumps({"status": "passed", "profiles": len(profiles),
                          "rawpy": rawpy.__version__, "model": metadata["Model"]}))
else:
    from raw2leica.app import main
    main()
