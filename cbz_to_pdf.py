#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Andy Pastuszak
"""Convert CBZ/CBR comic archives to PDF."""

import argparse
import io
import re
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    import rarfile
    HAS_RAR = True
except ImportError:
    HAS_RAR = False

import img2pdf
from PIL import Image, UnidentifiedImageError
from pypdf import PdfWriter, PdfReader


IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
IMG2PDF_NATIVE_FORMATS = {'JPEG', 'PNG', 'TIFF', 'GIF', 'BMP'}

# Limits to guard against malicious archives (zip bombs, oversized members)
MAX_MEMBER_SIZE = 200 * 1024 * 1024   # 200 MB per file
MAX_TOTAL_SIZE  = 4  * 1024 * 1024 * 1024  # 4 GB total uncompressed
MAX_MEMBERS     = 10_000


class ConversionError(Exception):
    """An expected, per-file failure (bad archive, no images, etc.)."""


def natural_sort_key(s: str) -> list:
    return [int(c) if c.isdigit() else c.lower() for c in re.split(r'(\d+)', s)]


def parse_comic_info(xml_data: bytes) -> dict:
    metadata = {}
    try:
        root = ET.fromstring(xml_data)

        def get(tag):
            el = root.find(tag)
            return el.text.strip() if el is not None and el.text else None

        series = get('Series')
        number = get('Number')
        title = get('Title')

        title_parts = []
        if series:
            title_parts.append(series)
        if number:
            title_parts.append(f'#{number}')
        if title:
            title_parts.append(title)
        if title_parts:
            metadata['/Title'] = ' '.join(title_parts)

        creators = []
        for role in ('Writer', 'Penciller', 'Inker', 'Colorist', 'CoverArtist', 'Letterer', 'Editor'):
            val = get(role)
            if val:
                creators.append(val)
        if creators:
            metadata['/Author'] = ', '.join(creators)

        summary = get('Summary')
        if summary:
            metadata['/Subject'] = summary

        keyword_parts = []
        for field in ('Publisher', 'Genre', 'LanguageISO', 'AgeRating'):
            val = get(field)
            if val:
                keyword_parts.append(val)
        if keyword_parts:
            metadata['/Keywords'] = ', '.join(keyword_parts)

        year = get('Year')
        month = get('Month')
        day = get('Day')
        if year and re.match(r'^\d{1,4}$', year):
            m = month.zfill(2) if month and re.match(r'^\d{1,2}$', month) else '01'
            d = day.zfill(2) if day and re.match(r'^\d{1,2}$', day) else '01'
            metadata['/CreationDate'] = f"D:{year.zfill(4)}{m}{d}000000"

    except ET.ParseError as e:
        print(f"Warning: Could not parse ComicInfo.xml: {e}", file=sys.stderr)

    return metadata


def prepare_image_to_file(data: bytes, dest_path: Path) -> None:
    """Write image data to dest_path, converting formats img2pdf can't embed natively to PNG."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format in IMG2PDF_NATIVE_FORMATS:
            dest_path.write_bytes(data)
            return
        if img.mode in ('RGBA', 'LA', 'P'):
            img.convert('RGBA').save(dest_path, format='PNG')
        else:
            img.convert('RGB').save(dest_path, format='PNG')
    except UnidentifiedImageError:
        # Format not recognised by Pillow; let img2pdf attempt it
        dest_path.write_bytes(data)
    # DecompressionBombError and others propagate — they signal a malicious image


def check_archive_safety(archive) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        raise ConversionError(f"Archive has too many members ({len(infos)} > {MAX_MEMBERS})")
    total = 0
    for info in infos:
        size = info.file_size
        if size > MAX_MEMBER_SIZE:
            name = getattr(info, 'filename', str(info))
            raise ConversionError(f"Archive member too large: {name} ({size / 1_048_576:.0f} MB > {MAX_MEMBER_SIZE // 1_048_576} MB limit)")
        total += size
        if total > MAX_TOTAL_SIZE:
            raise ConversionError(f"Archive total uncompressed size exceeds {MAX_TOTAL_SIZE // 1_073_741_824} GB limit")


def open_archive(path: Path):
    suffix = path.suffix.lower()
    if suffix == '.cbz':
        return zipfile.ZipFile(path, 'r')
    elif suffix == '.cbr':
        if not HAS_RAR:
            raise ConversionError(
                "The 'rarfile' package is required for CBR files.\n"
                "Install with: pip install rarfile\n"
                "Also requires 'unrar' on your system PATH."
            )
        return rarfile.RarFile(path, 'r')
    else:
        raise ConversionError(f"Unsupported file extension: {suffix!r}. Expected .cbz or .cbr")


def convert(input_path: Path, output_path: Path) -> None:
    archive = open_archive(input_path)
    try:
        check_archive_safety(archive)
        members = archive.namelist()

        comic_info_name = next(
            (m for m in members if Path(m).name.lower() == 'comicinfo.xml'),
            None,
        )
        metadata = {}
        if comic_info_name:
            with archive.open(comic_info_name) as f:
                metadata = parse_comic_info(f.read())
            if metadata:
                print(f"ComicInfo.xml: {len(metadata)} metadata field(s) found")

        image_names = sorted(
            (m for m in members if Path(m).suffix.lower() in IMAGE_EXTENSIONS),
            key=lambda n: natural_sort_key(Path(n).name),
        )

        if not image_names:
            raise ConversionError("No image files found in archive")

        print(f"Converting {len(image_names)} image(s)...")

        # Extract and prepare images to disk one at a time so peak memory stays
        # at roughly a single page rather than the whole comic at once.
        with tempfile.TemporaryDirectory(prefix='cbz2pdf-') as tmp:
            tmp_dir = Path(tmp)
            image_paths = []
            for idx, name in enumerate(image_names):
                with archive.open(name) as f:
                    data = f.read()
                dest = tmp_dir / f"{idx:06d}"
                prepare_image_to_file(data, dest)
                image_paths.append(str(dest))
            write_pdf(image_paths, metadata, output_path)
    finally:
        archive.close()

    size_mb = output_path.stat().st_size / 1_000_000
    print(f"Written: {output_path} ({size_mb:.1f} MB)")


def write_pdf(image_paths: list, metadata: dict, output_path: Path) -> None:
    """Assemble image files into a PDF, streaming to disk, and apply metadata if any."""
    try:
        if metadata:
            # img2pdf has no metadata API, so write a temp PDF then rewrite with pypdf.
            fd, tmp_name = tempfile.mkstemp(prefix='cbz2pdf-', suffix='.pdf')
            tmp_pdf = Path(tmp_name)
            try:
                with open(fd, 'wb') as out:
                    img2pdf.convert(image_paths, outputstream=out)
                reader = PdfReader(str(tmp_pdf))
                writer = PdfWriter()
                writer.append(reader)
                writer.add_metadata(metadata)
                with open(output_path, 'wb') as out:
                    writer.write(out)
            finally:
                tmp_pdf.unlink(missing_ok=True)
        else:
            with open(output_path, 'wb') as out:
                img2pdf.convert(image_paths, outputstream=out)
    except BaseException:
        # Don't leave a truncated PDF behind on failure (incl. Ctrl-C)
        output_path.unlink(missing_ok=True)
        raise


ARCHIVE_EXTENSIONS = {'.cbz', '.cbr'}


def main():
    parser = argparse.ArgumentParser(description='Convert CBZ/CBR comic archive(s) to PDF')
    parser.add_argument('input', type=Path, help='Input .cbz/.cbr file or directory of archives')
    parser.add_argument('output', type=Path, nargs='?',
                        help='Output .pdf file (single file) or output directory (batch); '
                             'defaults to same directory as input when batching')
    parser.add_argument('--skip-existing', action='store_true',
                        help='Skip archives whose output .pdf already exists (batch mode)')
    parser.add_argument('--force', action='store_true',
                        help='Overwrite existing output .pdf files (batch mode)')
    parser.add_argument('-r', '--recursive', action='store_true',
                        help='Recurse into subdirectories when batching a directory')
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Input not found: {args.input}")

    if args.input.is_dir():
        candidates = args.input.rglob('*') if args.recursive else args.input.iterdir()
        archives = sorted(
            [p for p in candidates if p.is_file() and p.suffix.lower() in ARCHIVE_EXTENSIONS],
            key=lambda p: natural_sort_key(str(p.relative_to(args.input))),
        )
        if not archives:
            sys.exit(f"No .cbz or .cbr files found in: {args.input}")

        out_dir = args.output if args.output else args.input
        if args.output:
            args.output.mkdir(parents=True, exist_ok=True)

        errors = 0
        skipped = 0
        for i, archive in enumerate(archives, 1):
            rel = archive.relative_to(args.input)
            print(f"\n[{i}/{len(archives)}] {rel}")
            # Mirror the input subtree under out_dir so same-named issues don't collide.
            out = (out_dir / rel).with_suffix('.pdf')
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists() and not args.force:
                if args.skip_existing:
                    print(f"Skipping (output exists): {out}")
                    skipped += 1
                    continue
                print(f"Error: output already exists: {out} (use --force or --skip-existing)", file=sys.stderr)
                errors += 1
                continue
            try:
                convert(archive, out)
            except ConversionError as e:
                print(f"Error: {e}", file=sys.stderr)
                errors += 1
            except Exception as e:
                print(f"Error: unexpected failure: {e}", file=sys.stderr)
                errors += 1

        summary = f"\nDone: {len(archives) - errors - skipped} converted"
        if skipped:
            summary += f", {skipped} skipped"
        if errors:
            summary += f", {errors} failed"
        print(summary)
        if errors:
            sys.exit(1)
    else:
        if args.output is None:
            sys.exit("Output path is required for single-file conversion")
        try:
            convert(args.input, args.output)
        except ConversionError as e:
            sys.exit(str(e))
        except Exception as e:
            sys.exit(f"Unexpected failure: {e}")


if __name__ == '__main__':
    main()
