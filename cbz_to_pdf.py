#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-3-Clause
# Copyright (c) 2026, Andy Pastuszak
"""Convert CBZ/CBR comic archives to PDF."""

import argparse
import io
import re
import sys
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


def prepare_image(data: bytes) -> io.BytesIO:
    """Return image data ready for img2pdf, converting unsupported formats to PNG."""
    try:
        img = Image.open(io.BytesIO(data))
        if img.format in IMG2PDF_NATIVE_FORMATS:
            return io.BytesIO(data)
        buf = io.BytesIO()
        if img.mode in ('RGBA', 'LA', 'P'):
            img.convert('RGBA').save(buf, format='PNG')
        else:
            img.convert('RGB').save(buf, format='PNG')
        buf.seek(0)
        return buf
    except UnidentifiedImageError:
        # Format not recognised by Pillow; let img2pdf attempt it
        return io.BytesIO(data)
    # DecompressionBombError and others propagate — they signal a malicious image


def check_archive_safety(archive) -> None:
    infos = archive.infolist()
    if len(infos) > MAX_MEMBERS:
        sys.exit(f"Archive has too many members ({len(infos)} > {MAX_MEMBERS})")
    total = 0
    for info in infos:
        size = info.file_size
        if size > MAX_MEMBER_SIZE:
            name = getattr(info, 'filename', str(info))
            sys.exit(f"Archive member too large: {name} ({size / 1_048_576:.0f} MB > {MAX_MEMBER_SIZE // 1_048_576} MB limit)")
        total += size
        if total > MAX_TOTAL_SIZE:
            sys.exit(f"Archive total uncompressed size exceeds {MAX_TOTAL_SIZE // 1_073_741_824} GB limit")


def open_archive(path: Path):
    suffix = path.suffix.lower()
    if suffix == '.cbz':
        return zipfile.ZipFile(path, 'r')
    elif suffix == '.cbr':
        if not HAS_RAR:
            sys.exit(
                "The 'rarfile' package is required for CBR files.\n"
                "Install with: pip install rarfile\n"
                "Also requires 'unrar' on your system PATH."
            )
        return rarfile.RarFile(path, 'r')
    else:
        sys.exit(f"Unsupported file extension: {suffix!r}. Expected .cbz or .cbr")


def convert(input_path: Path, output_path: Path) -> None:
    archive = open_archive(input_path)
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
        archive.close()
        sys.exit("No image files found in archive")

    print(f"Converting {len(image_names)} image(s)...")

    images = []
    for name in image_names:
        with archive.open(name) as f:
            images.append(prepare_image(f.read()))
    archive.close()

    pdf_bytes = img2pdf.convert(images)

    if metadata:
        reader = PdfReader(io.BytesIO(pdf_bytes))
        writer = PdfWriter()
        writer.append(reader)
        writer.add_metadata(metadata)
        with open(output_path, 'wb') as out:
            writer.write(out)
    else:
        with open(output_path, 'wb') as out:
            out.write(pdf_bytes)

    size_mb = output_path.stat().st_size / 1_000_000
    print(f"Written: {output_path} ({size_mb:.1f} MB)")


def main():
    parser = argparse.ArgumentParser(description='Convert CBZ/CBR comic archive to PDF')
    parser.add_argument('input', type=Path, help='Input .cbz or .cbr file')
    parser.add_argument('output', type=Path, help='Output .pdf file')
    args = parser.parse_args()

    if not args.input.exists():
        sys.exit(f"Input file not found: {args.input}")

    convert(args.input, args.output)


if __name__ == '__main__':
    main()
