from __future__ import annotations

import math
from io import BytesIO
from pathlib import Path


class ImageService:
    PRESET_DIMENSIONS = {
        "a4_size": (2480, 3508),
        "ssc_photo": (413, 531),
        "pan_card": (413, 295),
        "upsc_photo": (413, 531),
        "psc_photo": (413, 531),
        "passport_photo": (413, 531),
        "signature_50x20mm": (591, 236),
        "size_35x45mm": (413, 531),
        "size_2x2_inch": (600, 600),
        "size_3_4_inch": (900, 1200),
        "size_4_6_inch": (1200, 1800),
        "size_600x600": (600, 600),
        "instagram_no_crop": (1080, 1080),
        "instagram_grid": (3240, 1080),
        "whatsapp_dp": (500, 500),
        "youtube_banner": (2560, 1440),
        "sign_6x2cm": (709, 236),
        "profile_3_5x4_5cm": (413, 531),
    }

    @staticmethod
    def _pil_objects():
        from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

        return Image, ImageDraw, ImageEnhance, ImageFilter, ImageOps, ImageStat

    @staticmethod
    def _open_image(path: str):
        Image, _, _, _, _, _ = ImageService._pil_objects()
        return Image.open(path)

    @staticmethod
    def _ensure_rgb(image):
        if image.mode in {"RGBA", "LA"}:
            background = ImageService._pil_objects()[0].new("RGB", image.size, "white")
            background.paste(image, mask=image.split()[-1])
            return background
        if image.mode != "RGB":
            return image.convert("RGB")
        return image

    @staticmethod
    def _save_image(image, output_path: str, quality: int = 92, dpi: tuple[int, int] | None = None) -> str:
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        suffix = output.suffix.lower()
        save_kwargs = {}
        if dpi:
            save_kwargs["dpi"] = dpi
        if suffix in {".jpg", ".jpeg"}:
            image = ImageService._ensure_rgb(image)
            save_kwargs.update({"quality": max(20, min(quality, 98)), "optimize": True})
            image.save(output, format="JPEG", **save_kwargs)
        elif suffix == ".webp":
            image = ImageService._ensure_rgb(image)
            save_kwargs.update({"quality": max(20, min(quality, 98)), "method": 6})
            image.save(output, format="WEBP", **save_kwargs)
        else:
            image.save(output, **save_kwargs)
        return str(output)

    @staticmethod
    def _unit_to_pixels(value: float, unit: str, dpi: int) -> int:
        normalized = (unit or "px").strip().lower()
        if normalized in {"px", "pixel", "pixels"}:
            return max(1, int(round(value)))
        if normalized in {"cm", "centimeter", "centimeters"}:
            return max(1, int(round((value / 2.54) * dpi)))
        if normalized in {"mm", "millimeter", "millimeters"}:
            return max(1, int(round((value / 25.4) * dpi)))
        if normalized in {"in", "inch", "inches"}:
            return max(1, int(round(value * dpi)))
        return max(1, int(round(value)))

    @staticmethod
    def resize_pixels(input_path: str, output_path: str, width: int, height: int) -> str:
        image = ImageService._open_image(input_path)
        resized = image.resize((max(1, width), max(1, height)))
        return ImageService._save_image(resized, output_path)

    @staticmethod
    def resize_units(
        input_path: str,
        output_path: str,
        width: float,
        height: float,
        unit: str = "cm",
        dpi: int = 300,
    ) -> str:
        width_px = ImageService._unit_to_pixels(width, unit, dpi)
        height_px = ImageService._unit_to_pixels(height, unit, dpi)
        image = ImageService._open_image(input_path)
        resized = image.resize((width_px, height_px))
        return ImageService._save_image(resized, output_path, dpi=(dpi, dpi))

    @staticmethod
    def resize_with_preset(input_path: str, output_path: str, preset_key: str) -> str:
        dimensions = ImageService.PRESET_DIMENSIONS.get(preset_key)
        if not dimensions:
            raise ValueError("Unknown resize preset.")
        return ImageService.resize_pixels(input_path, output_path, dimensions[0], dimensions[1])

    @staticmethod
    def _compressed_bytes(image, quality: int) -> bytes:
        buffer = BytesIO()
        rgb = ImageService._ensure_rgb(image)
        rgb.save(buffer, format="JPEG", quality=max(20, min(quality, 98)), optimize=True)
        return buffer.getvalue()

    @staticmethod
    def compress_to_quality(input_path: str, output_path: str, quality: int = 75) -> dict:
        image = ImageService._open_image(input_path)
        payload = ImageService._compressed_bytes(image, quality)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(payload)
        original = Path(input_path).stat().st_size
        final = len(payload)
        return {
            "output_path": str(output_path),
            "original_size": original,
            "final_size": final,
            "reduction_pct": round(((original - final) / original * 100), 2) if original else 0.0,
        }

    @staticmethod
    def compress_to_target_kb(input_path: str, output_path: str, target_kb: int) -> dict:
        image = ImageService._open_image(input_path)
        target_bytes = max(3 * 1024, int(target_kb * 1024))

        working = ImageService._ensure_rgb(image.copy())
        best_payload: bytes | None = None
        best_quality = 95

        for _ in range(5):
            low, high = 20, 98
            chosen_payload = None
            chosen_quality = 20
            while low <= high:
                mid = (low + high) // 2
                payload = ImageService._compressed_bytes(working, mid)
                if len(payload) <= target_bytes:
                    chosen_payload = payload
                    chosen_quality = mid
                    low = mid + 1
                else:
                    high = mid - 1
            if chosen_payload is not None:
                best_payload = chosen_payload
                best_quality = chosen_quality
                break
            width = max(40, int(working.width * 0.9))
            height = max(40, int(working.height * 0.9))
            working = working.resize((width, height))

        if best_payload is None:
            best_payload = ImageService._compressed_bytes(working, 20)
            best_quality = 20

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(best_payload)
        original = Path(input_path).stat().st_size
        final = len(best_payload)
        return {
            "output_path": str(output_path),
            "original_size": original,
            "final_size": final,
            "target_kb": target_kb,
            "quality": best_quality,
        }

    @staticmethod
    def increase_to_target_kb(input_path: str, output_path: str, target_kb: int) -> dict:
        details = ImageService.compress_to_quality(input_path, output_path, quality=95)
        target_bytes = max(1024, int(target_kb * 1024))
        current_size = Path(output_path).stat().st_size
        if current_size > target_bytes:
            return ImageService.compress_to_target_kb(input_path, output_path, target_kb)
        if current_size < target_bytes:
            padding = target_bytes - current_size
            with open(output_path, "ab") as handle:
                handle.write(b"\0" * padding)
        details["final_size"] = Path(output_path).stat().st_size
        details["target_kb"] = target_kb
        details["mode"] = "increase"
        return details

    @staticmethod
    def convert_image(input_path: str, output_path: str) -> str:
        image = ImageService._open_image(input_path)
        return ImageService._save_image(image, output_path)

    @staticmethod
    def rotate_image(input_path: str, output_path: str, angle: float = 90) -> str:
        image = ImageService._open_image(input_path)
        rotated = image.rotate(-angle, expand=True)
        return ImageService._save_image(rotated, output_path)

    @staticmethod
    def flip_image(input_path: str, output_path: str, direction: str = "horizontal") -> str:
        _, _, _, _, ImageOps, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path)
        flipped = ImageOps.mirror(image) if direction == "horizontal" else ImageOps.flip(image)
        return ImageService._save_image(flipped, output_path)

    @staticmethod
    def grayscale_image(input_path: str, output_path: str) -> str:
        image = ImageService._open_image(input_path).convert("L")
        return ImageService._save_image(image, output_path)

    @staticmethod
    def black_white_image(input_path: str, output_path: str, threshold: int = 145) -> str:
        image = ImageService._open_image(input_path).convert("L")
        bw = image.point(lambda value: 255 if value > threshold else 0)
        return ImageService._save_image(bw, output_path)

    @staticmethod
    def blur_image(input_path: str, output_path: str, radius: float = 2.5) -> str:
        _, _, _, ImageFilter, _, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path)
        blurred = image.filter(ImageFilter.GaussianBlur(radius=max(0.5, radius)))
        return ImageService._save_image(blurred, output_path)

    @staticmethod
    def pixelate_image(input_path: str, output_path: str, pixel_size: int = 10) -> str:
        image = ImageService._open_image(input_path)
        pixel_size = max(2, pixel_size)
        small = image.resize((max(1, image.width // pixel_size), max(1, image.height // pixel_size)))
        output = small.resize(image.size, resample=0)
        return ImageService._save_image(output, output_path)

    @staticmethod
    def motion_blur_image(input_path: str, output_path: str, radius: int = 9) -> str:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return ImageService.blur_image(input_path, output_path, radius=3.0)
        image = cv2.imread(input_path)
        if image is None:
            raise ValueError("Unable to read image.")
        kernel_size = max(3, radius | 1)
        kernel = np.zeros((kernel_size, kernel_size))
        kernel[int((kernel_size - 1) / 2), :] = np.ones(kernel_size)
        kernel = kernel / kernel_size
        blurred = cv2.filter2D(image, -1, kernel)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), blurred)
        return str(output_path)

    @staticmethod
    def crop_square(input_path: str, output_path: str) -> str:
        image = ImageService._open_image(input_path)
        edge = min(image.width, image.height)
        x = (image.width - edge) // 2
        y = (image.height - edge) // 2
        cropped = image.crop((x, y, x + edge, y + edge))
        return ImageService._save_image(cropped, output_path)

    @staticmethod
    def crop_circle(input_path: str, output_path: str) -> str:
        Image, ImageDraw, _, _, _, _ = ImageService._pil_objects()
        square_path = Path(output_path).with_suffix(".png")
        ImageService.crop_square(input_path, str(square_path))
        base = Image.open(square_path).convert("RGBA")
        mask = Image.new("L", base.size, 0)
        draw = ImageDraw.Draw(mask)
        draw.ellipse((0, 0, base.width, base.height), fill=255)
        base.putalpha(mask)
        return ImageService._save_image(base, output_path)

    @staticmethod
    def crop_custom(input_path: str, output_path: str, x: int, y: int, width: int, height: int) -> str:
        image = ImageService._open_image(input_path)
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(image.width, x0 + max(1, width))
        y1 = min(image.height, y0 + max(1, height))
        cropped = image.crop((x0, y0, x1, y1))
        return ImageService._save_image(cropped, output_path)

    @staticmethod
    def add_text(input_path: str, output_path: str, text: str, x: int = 20, y: int = 20, color: str = "#1b2d27") -> str:
        _, ImageDraw, _, _, _, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path).convert("RGBA")
        drawer = ImageDraw.Draw(image)
        drawer.text((x, y), text, fill=color)
        return ImageService._save_image(image, output_path)

    @staticmethod
    def add_logo(input_path: str, output_path: str, logo_path: str, x: int = 20, y: int = 20, width: int = 120) -> str:
        Image, _, _, _, _, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path).convert("RGBA")
        logo = Image.open(logo_path).convert("RGBA")
        scale = width / max(1, logo.width)
        logo = logo.resize((max(1, int(logo.width * scale)), max(1, int(logo.height * scale))))
        image.paste(logo, (x, y), mask=logo)
        return ImageService._save_image(image, output_path)

    @staticmethod
    def watermark_text(input_path: str, output_path: str, text: str) -> str:
        return ImageService.add_text(input_path, output_path, text, x=24, y=24, color="#ffffff")

    @staticmethod
    def join_images(input_paths: list[str], output_path: str, direction: str = "vertical") -> str:
        Image, _, _, _, _, _ = ImageService._pil_objects()
        if len(input_paths) < 2:
            raise ValueError("Join images requires at least two files.")
        images = [ImageService._ensure_rgb(Image.open(path)) for path in input_paths]
        if direction == "horizontal":
            width = sum(image.width for image in images)
            height = max(image.height for image in images)
            canvas = Image.new("RGB", (width, height), "white")
            cursor = 0
            for image in images:
                canvas.paste(image, (cursor, 0))
                cursor += image.width
        else:
            width = max(image.width for image in images)
            height = sum(image.height for image in images)
            canvas = Image.new("RGB", (width, height), "white")
            cursor = 0
            for image in images:
                canvas.paste(image, (0, cursor))
                cursor += image.height
        return ImageService._save_image(canvas, output_path)

    @staticmethod
    def split_image(input_path: str, output_dir: str, rows: int = 2, cols: int = 2) -> list[str]:
        image = ImageService._open_image(input_path)
        rows = max(1, rows)
        cols = max(1, cols)
        tile_w = max(1, image.width // cols)
        tile_h = max(1, image.height // rows)
        target = Path(output_dir)
        target.mkdir(parents=True, exist_ok=True)
        outputs: list[str] = []
        index = 1
        for row in range(rows):
            for col in range(cols):
                x0 = col * tile_w
                y0 = row * tile_h
                x1 = image.width if col == cols - 1 else x0 + tile_w
                y1 = image.height if row == rows - 1 else y0 + tile_h
                tile = image.crop((x0, y0, x1, y1))
                path = target / f"part_{index:02d}.png"
                tile.save(path)
                outputs.append(str(path))
                index += 1
        return outputs

    @staticmethod
    def merge_photo_signature(photo_path: str, signature_path: str, output_path: str) -> str:
        Image, _, _, _, _, _ = ImageService._pil_objects()
        photo = ImageService._open_image(photo_path).convert("RGBA")
        signature = Image.open(signature_path).convert("RGBA")
        target_width = int(photo.width * 0.35)
        scale = target_width / max(1, signature.width)
        signature = signature.resize((target_width, max(1, int(signature.height * scale))))
        x = photo.width - signature.width - 24
        y = photo.height - signature.height - 24
        photo.paste(signature, (x, y), signature)
        return ImageService._save_image(photo, output_path)

    @staticmethod
    def dominant_colors(input_path: str, color_count: int = 5) -> list[str]:
        image = ImageService._open_image(input_path).convert("RGB")
        palette = image.resize((160, 160)).quantize(colors=max(2, color_count))
        colors = palette.getpalette()[: color_count * 3]
        output = []
        for index in range(0, len(colors), 3):
            if index + 2 >= len(colors):
                break
            output.append(f"#{colors[index]:02x}{colors[index+1]:02x}{colors[index+2]:02x}")
        return output

    @staticmethod
    def _face_boxes(input_path: str) -> tuple[list[tuple[int, int, int, int]], "np.ndarray"]:
        import cv2  # type: ignore
        import numpy as np  # type: ignore

        frame = cv2.imread(input_path)
        if frame is None:
            raise ValueError("Unable to read image.")
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        cascade = cv2.CascadeClassifier(cv2.data.haarcascades + "haarcascade_frontalface_default.xml")
        boxes = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=4, minSize=(40, 40))
        return [(int(x), int(y), int(w), int(h)) for (x, y, w, h) in boxes], frame

    @staticmethod
    def face_effect(input_path: str, output_path: str, mode: str = "blur") -> str:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return ImageService.blur_image(input_path, output_path, radius=3.0)

        boxes, frame = ImageService._face_boxes(input_path)
        if not boxes:
            return ImageService._save_image(ImageService._open_image(input_path), output_path)
        for x, y, w, h in boxes:
            region = frame[y : y + h, x : x + w]
            if mode == "pixelate":
                small = cv2.resize(region, (max(1, w // 12), max(1, h // 12)), interpolation=cv2.INTER_LINEAR)
                frame[y : y + h, x : x + w] = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)
            elif mode == "censor":
                frame[y : y + h, x : x + w] = (0, 0, 0)
            else:
                frame[y : y + h, x : x + w] = cv2.GaussianBlur(region, (35, 35), 30)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), frame)
        return str(output_path)

    @staticmethod
    def remove_background(input_path: str, output_path: str) -> str:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
            from PIL import Image
        except Exception:
            return ImageService._save_image(ImageService._open_image(input_path), output_path)
        frame = cv2.imread(input_path)
        if frame is None:
            raise ValueError("Unable to read image.")
        mask = np.zeros(frame.shape[:2], np.uint8)
        bg = np.zeros((1, 65), np.float64)
        fg = np.zeros((1, 65), np.float64)
        rect = (
            max(1, int(frame.shape[1] * 0.05)),
            max(1, int(frame.shape[0] * 0.05)),
            max(20, int(frame.shape[1] * 0.9)),
            max(20, int(frame.shape[0] * 0.9)),
        )
        cv2.grabCut(frame, mask, rect, bg, fg, 3, cv2.GC_INIT_WITH_RECT)
        alpha = np.where((mask == 2) | (mask == 0), 0, 255).astype("uint8")
        rgba = cv2.cvtColor(frame, cv2.COLOR_BGR2RGBA)
        rgba[:, :, 3] = alpha
        output_img = Image.fromarray(rgba)
        return ImageService._save_image(output_img, output_path)

    @staticmethod
    def blur_background(input_path: str, output_path: str) -> str:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return ImageService.blur_image(input_path, output_path, radius=3.0)
        frame = cv2.imread(input_path)
        if frame is None:
            raise ValueError("Unable to read image.")
        mask = np.zeros(frame.shape[:2], np.uint8)
        bg = np.zeros((1, 65), np.float64)
        fg = np.zeros((1, 65), np.float64)
        rect = (
            max(1, int(frame.shape[1] * 0.12)),
            max(1, int(frame.shape[0] * 0.08)),
            max(20, int(frame.shape[1] * 0.76)),
            max(20, int(frame.shape[0] * 0.84)),
        )
        cv2.grabCut(frame, mask, rect, bg, fg, 3, cv2.GC_INIT_WITH_RECT)
        foreground_mask = np.where((mask == 2) | (mask == 0), 0, 1).astype("uint8")
        blurred = cv2.GaussianBlur(frame, (35, 35), 25)
        result = frame * foreground_mask[:, :, None] + blurred * (1 - foreground_mask[:, :, None])
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), result)
        return str(output_path)

    @staticmethod
    def remove_object(input_path: str, output_path: str, x: int, y: int, width: int, height: int) -> str:
        try:
            import cv2  # type: ignore
            import numpy as np  # type: ignore
        except Exception:
            return ImageService._save_image(ImageService._open_image(input_path), output_path)
        frame = cv2.imread(input_path)
        if frame is None:
            raise ValueError("Unable to read image.")
        mask = np.zeros(frame.shape[:2], dtype="uint8")
        x0 = max(0, x)
        y0 = max(0, y)
        x1 = min(frame.shape[1], x0 + max(1, width))
        y1 = min(frame.shape[0], y0 + max(1, height))
        mask[y0:y1, x0:x1] = 255
        cleaned = cv2.inpaint(frame, mask, 7, cv2.INPAINT_TELEA)
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        cv2.imwrite(str(output_path), cleaned)
        return str(output_path)

    @staticmethod
    def upscale(input_path: str, output_path: str, scale: float = 2.0) -> str:
        _, _, ImageEnhance, _, _, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path)
        width = max(1, int(image.width * max(1.1, scale)))
        height = max(1, int(image.height * max(1.1, scale)))
        upscaled = image.resize((width, height), resample=1)
        upscaled = ImageEnhance.Sharpness(upscaled).enhance(1.3)
        return ImageService._save_image(upscaled, output_path)

    @staticmethod
    def pixel_art(input_path: str, output_path: str, factor: int = 12) -> str:
        image = ImageService._open_image(input_path)
        factor = max(2, factor)
        reduced = image.resize((max(1, image.width // factor), max(1, image.height // factor)), resample=0)
        expanded = reduced.resize(image.size, resample=0)
        return ImageService._save_image(expanded, output_path)

    @staticmethod
    def beautify(input_path: str, output_path: str) -> str:
        _, _, ImageEnhance, ImageFilter, _, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path)
        smooth = image.filter(ImageFilter.SMOOTH_MORE)
        smooth = ImageEnhance.Color(smooth).enhance(1.08)
        smooth = ImageEnhance.Contrast(smooth).enhance(1.05)
        return ImageService._save_image(smooth, output_path)

    @staticmethod
    def unblur(input_path: str, output_path: str) -> str:
        _, _, ImageEnhance, ImageFilter, _, _ = ImageService._pil_objects()
        image = ImageService._open_image(input_path)
        sharp = image.filter(ImageFilter.UnsharpMask(radius=2, percent=170, threshold=3))
        sharp = ImageEnhance.Sharpness(sharp).enhance(1.25)
        return ImageService._save_image(sharp, output_path)

    @staticmethod
    def metadata_view(input_path: str) -> dict:
        image = ImageService._open_image(input_path)
        exif = image.getexif()
        tags = {}
        for key, value in exif.items():
            tags[str(key)] = str(value)
        dpi = image.info.get("dpi", (72, 72))
        return {
            "format": image.format,
            "mode": image.mode,
            "width": image.width,
            "height": image.height,
            "dpi": dpi,
            "tags": tags,
        }

    @staticmethod
    def metadata_remove(input_path: str, output_path: str) -> str:
        image = ImageService._open_image(input_path)
        copy = ImageService._ensure_rgb(image.copy())
        return ImageService._save_image(copy, output_path)

    @staticmethod
    def metadata_edit(input_path: str, output_path: str, title: str = "", author: str = "") -> str:
        image = ImageService._open_image(input_path)
        copy = ImageService._ensure_rgb(image.copy())
        exif = copy.getexif()
        if title:
            exif[270] = title
        if author:
            exif[315] = author
        output = Path(output_path)
        output.parent.mkdir(parents=True, exist_ok=True)
        copy.save(output, exif=exif.tobytes())
        return str(output)

    @staticmethod
    def check_dpi(input_path: str) -> dict:
        image = ImageService._open_image(input_path)
        dpi = image.info.get("dpi", (72, 72))
        if isinstance(dpi, tuple):
            x_dpi, y_dpi = dpi[0], dpi[1] if len(dpi) > 1 else dpi[0]
        else:
            x_dpi = y_dpi = dpi
        return {"x_dpi": x_dpi, "y_dpi": y_dpi}

    @staticmethod
    def convert_dpi(input_path: str, output_path: str, dpi: int = 300) -> str:
        image = ImageService._open_image(input_path)
        return ImageService._save_image(image, output_path, dpi=(dpi, dpi))

    @staticmethod
    def size_conversion(value: float, source_unit: str, target_unit: str) -> float:
        to_kb_factor = {
            "kb": 1.0,
            "mb": 1024.0,
        }
        source = source_unit.lower()
        target = target_unit.lower()
        if source not in to_kb_factor or target not in to_kb_factor:
            raise ValueError("Invalid size units.")
        kb = value * to_kb_factor[source]
        return kb / to_kb_factor[target]
